# SPDX-License-Identifier: AGPL-3.0-or-later
"""AI creative director endpoint: one prompt + target selector, validated."""

import json
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..ai.director import apply_look
from ..ai.meta import DEFAULT_MODEL as META_DEFAULT_MODEL
from ..ai.meta import MetaProvider, MetaProviderError
from ..ai.mock import MockProvider
from ..config.loader import (
    ConfigError,
    check_references,
    load_profiles,
    load_studio,
    validate,
)
from ..core.logging import get_logger
from ..core.safety import SafetyViolation
from ..modules.modes import ModesError, ModesModule
from ..registry import Registry, RegistryError
from .modes import _address_labels, _scrub, build_modes_module, record_action

log = get_logger("api")
router = APIRouter()

PRESET_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


class LookBody(BaseModel):
    prompt: str = ""
    targets: str = "both"
    provider: str = "mock"
    scope_mode: str = "all"
    scope_zones: list = []
    scope_lights: list = []


class SavePresetBody(BaseModel):
    preset_id: str
    label: str | None = None
    lights: dict = {}
    screen: dict = {}
    fade_s: float | None = None
    overwrite: bool = False


class PresetSaveError(Exception):
    """Unusable save request (non-local studio, bad id, rejected content)."""


class PresetConflictError(PresetSaveError):
    """Preset id already exists and overwrite was not set."""


def save_preset(config_root, registry, preset_id, preset, overwrite=False):
    """Validate (schema + modes gate), persist to studios.local, reload registry.

    Only studios with a studios.local directory can receive presets; tracked
    example studios are refused. Returns (reloaded Registry, overwrote).
    """
    if not PRESET_ID_RE.fullmatch(preset_id or ""):
        raise PresetSaveError(f"preset_id must match {PRESET_ID_RE.pattern}: {preset_id!r}")
    studio_id = registry.studio.get("id")
    presets_path = Path(config_root) / "studios.local" / studio_id / "presets.json"
    if not presets_path.exists():
        raise PresetSaveError(f"presets save only for local studios (no studios.local/{studio_id})")
    try:
        validate(preset, "preset", f"preset {preset_id}")
    except ConfigError as exc:
        raise PresetSaveError(str(exc)) from exc
    gate_content = {"lights": preset.get("lights") or {}, "screen": preset.get("screen") or {}}
    if preset.get("fade_s") is not None:
        gate_content["fade_s"] = preset["fade_s"]
    try:
        # Strict gate (no AI clamp): user-authored presets must be exactly valid.
        ModesModule("modes", registry).plan_custom(preset_id, gate_content, ai_caps=False)
    except (ModesError, SafetyViolation, RegistryError) as exc:
        raise PresetSaveError(f"preset content rejected: {exc}") from exc
    try:
        stored = json.loads(presets_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PresetSaveError(f"cannot read {presets_path}: {exc}") from exc
    if not isinstance(stored, dict):
        raise PresetSaveError(f"{presets_path} must hold an object")
    overwrote = preset_id in stored
    if overwrote and not overwrite:
        raise PresetConflictError(f"preset {preset_id} exists (set overwrite to replace)")
    stored[preset_id] = preset
    tmp_path = presets_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    os.replace(tmp_path, presets_path)
    try:
        loaded = load_studio(config_root, studio_id)
        profiles = load_profiles(config_root)
        check_references(loaded, profiles, studio_id)
    except ConfigError as exc:
        raise PresetSaveError(f"saved preset broke studio references: {exc}") from exc
    return Registry.from_loaded(loaded, profiles), overwrote


def _meta_provider(config_root):
    """MetaProvider from hub-local studios.local/meta.json. None when no key file."""
    path = Path(config_root) / "studios.local" / "meta.json"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        cfg = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"meta.json malformed: {exc}") from exc
    if not isinstance(cfg, dict) or not cfg.get("api_key"):
        return None
    return MetaProvider(cfg["api_key"], cfg.get("model") or META_DEFAULT_MODEL)


def _resolve_scope(registry, body):
    """Turn the scope picker into an allowed id list (None = everything).

    Zones and lights are exclusive: scope_mode picks which list counts.
    Unknown zones, lights, or modes are 422, never a silent empty look.
    """
    mode = body.scope_mode or "all"
    if mode == "all":
        return None, {"mode": "all", "zones": [], "lights": []}
    if mode == "zones":
        unknown = [zone for zone in body.scope_zones if zone not in registry.zone_ids]
        if unknown:
            raise HTTPException(status_code=422, detail=f"unknown scope zones: {unknown}")
        wanted = set(body.scope_zones)
        allowed = []
        for device_id in registry.device_ids:
            device = registry.get_device(device_id)
            if device.get("zone") not in wanted:
                continue
            if device.get("controllable", True) is False:
                continue
            if device.get("type") == "light" or device.get("driver") == "screen":
                allowed.append(device_id)
        return allowed, {"mode": "zones", "zones": list(body.scope_zones), "lights": []}
    if mode == "lights":
        unknown = [item for item in body.scope_lights if item not in registry.device_ids]
        if unknown:
            raise HTTPException(status_code=422, detail=f"unknown scope lights: {unknown}")
        return list(body.scope_lights), {"mode": "lights", "zones": [], "lights": list(body.scope_lights)}
    raise HTTPException(status_code=422, detail=f'scope_mode must be "all"|"zones"|"lights": {mode!r}')


@router.post("/api/studios/{studio_id}/ai/look")
def ai_look(studio_id: str, body: LookBody, request: Request):
    """Prompted look on lights/screen/both: direct -> validate -> critique -> apply.

    Provider "mock" is the offline demo; "meta" uses the dev.meta.ai key in
    hub-local studios.local/meta.json (absent file = honest 503).
    Scope (all/zones/lights) limits which devices the look may touch.
    """
    try:
        registry = request.app.state.registries[studio_id]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown studio: {studio_id}") from exc
    if body.provider == "mock":
        provider = MockProvider()
    elif body.provider == "meta":
        provider = _meta_provider(request.app.state.config_root)
        if provider is None:
            raise HTTPException(
                status_code=503, detail="meta provider not configured (no studios.local/meta.json)"
            )
    else:
        raise HTTPException(
            status_code=422, detail=f'unknown provider: {body.provider!r} (want "mock"|"meta")'
        )
    module = build_modes_module(registry, request.app.state.config_root)
    log.info(
        "ai look",
        extra={"fields": {"studio": studio_id, "targets": body.targets, "provider": provider.name()}},
    )
    allowed, scope = _resolve_scope(registry, body)
    try:
        outcome = apply_look(provider, module, body.prompt, body.targets, allowed=allowed)
    except MetaProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (ModesError, SafetyViolation) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    outcome["scope"].update(scope)
    directives = outcome.get("directives") or {}
    record_action(
        request,
        studio_id,
        "custom",
        f"ai-{outcome['provider']}",
        {"lights": directives.get("lights") or {}, "screen": directives.get("screens") or {}},
        outcome["apply"],
    )
    return _scrub(outcome, _address_labels(registry))


@router.post("/api/studios/{studio_id}/ai/save-preset")
def ai_save_preset(studio_id: str, body: SavePresetBody, request: Request):
    """Save a prompted look as a preset (local studios only).

    The candidate faces the schema plus the modes gate before anything is
    written; the file update is atomic and the registry reloads, so the
    preset is immediately listable and applicable.
    """
    try:
        registry = request.app.state.registries[studio_id]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown studio: {studio_id}") from exc
    preset = {"label": body.label or body.preset_id, "lights": body.lights, "screen": body.screen}
    if body.fade_s is not None:
        preset["fade_s"] = body.fade_s
    log.info("ai save-preset", extra={"fields": {"studio": studio_id, "preset": body.preset_id}})
    try:
        reloaded, overwrote = save_preset(
            request.app.state.config_root, registry, body.preset_id, preset, body.overwrite
        )
    except PresetConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PresetSaveError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    request.app.state.registries[studio_id] = reloaded
    return {"preset": body.preset_id, "label": preset["label"], "overwrote": overwrote}
