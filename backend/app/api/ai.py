# SPDX-License-Identifier: AGPL-3.0-or-later
"""AI creative director endpoint: one prompt + target selector, validated."""

import json
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..ai.director import apply_look
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
from .modes import _address_labels, _scrub, build_modes_module

log = get_logger("api")
router = APIRouter()

PRESET_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


class LookBody(BaseModel):
    prompt: str = ""
    targets: str = "both"


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
        ModesModule("modes", registry).plan_custom(preset_id, gate_content)
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


@router.post("/api/studios/{studio_id}/ai/look")
def ai_look(studio_id: str, body: LookBody, request: Request):
    """Prompted look on lights/screen/both: direct -> validate -> critique -> apply.

    Mock provider in the scaffold; the pipeline (and its gate) is the real one.
    """
    try:
        registry = request.app.state.registries[studio_id]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown studio: {studio_id}") from exc
    module = build_modes_module(registry, request.app.state.config_root)
    log.info("ai look", extra={"fields": {"studio": studio_id, "targets": body.targets}})
    try:
        outcome = apply_look(MockProvider(), module, body.prompt, body.targets)
    except (ModesError, SafetyViolation) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
