# SPDX-License-Identifier: AGPL-3.0-or-later
"""Default sets: named per-studio collections of equipment values.

One set holds a value snapshot per device (lights, screens, strips)
plus Kasa plug states; a studio assigns one set as its default. The
Control page seeds its inputs from the assigned set (session values
win while the tab lives) and every device surface offers one-click
reset to it. Storage is defaults.json per studio (local studios
writable, tracked examples read-only); the assignment lives in
studio.json as defaultSet beside defaultMode.
"""

import json
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..config.loader import (
    ConfigError,
    check_references,
    load_profiles,
    load_studio,
    validate,
)
from ..config.ranges import RangeError, check_light_values
from ..core.logging import get_logger
from ..registry import Registry, RegistryError

log = get_logger("api")
router = APIRouter()

SET_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
RESERVED_IDS = {"assign"}

LIGHT_KEYS = {"power", "intensity", "cct", "hue", "saturation", "page", "transport"}
STRIP_KEYS = {"power", "color"}
SCREEN_KEYS = {"theme", "text", "animation"}
SCREEN_VALUE_LIMIT = 500


class CreateBody(BaseModel):
    set_id: str
    label: str | None = None
    devices: dict = {}
    plugs: dict = {}
    overwrite: bool = False


class EditBody(BaseModel):
    label: str | None = None
    devices: dict | None = None
    plugs: dict | None = None


class AssignBody(BaseModel):
    set_id: str | None = None


class DefaultSetSaveError(Exception):
    """Unusable save request (non-local studio, bad id, rejected content)."""


class DefaultSetConflictError(DefaultSetSaveError):
    """Set id already exists and overwrite was not set."""


def _registry_or_404(request, studio_id):
    try:
        return request.app.state.registries[studio_id]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown studio: {studio_id}") from exc


def _is_num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_light_values(device_id, values, effective_device):
    """Validate light values against the device's effective ranges.

    Spans come from the profile spec via the shared ranges resolver (generic
    outer bounds only where a profile omits a range), so the gate agrees with
    every page instead of carrying its own hardcoded copy.
    """
    for key in values:
        if key not in LIGHT_KEYS:
            raise DefaultSetSaveError(f"{device_id}: unknown light value {key!r}")
    try:
        check_light_values(device_id, values, effective_device)
    except RangeError as exc:
        raise DefaultSetSaveError(str(exc)) from exc


def _check_strip_values(device_id, values):
    for key, value in values.items():
        if key not in STRIP_KEYS:
            raise DefaultSetSaveError(f"{device_id}: unknown strip value {key!r}")
        if key == "power" and value not in ("on", "off"):
            raise DefaultSetSaveError(f"{device_id}: power must be \"on\"|\"off\"")
        if key == "color" and (not isinstance(value, str) or not value.strip()):
            raise DefaultSetSaveError(f"{device_id}: color must be a saved code label")


def _check_screen_values(device_id, values):
    for key, value in values.items():
        if key not in SCREEN_KEYS:
            raise DefaultSetSaveError(f"{device_id}: unknown screen value {key!r}")
        if not isinstance(value, str) or len(value) > SCREEN_VALUE_LIMIT:
            raise DefaultSetSaveError(f"{device_id}: {key} must be text (<= {SCREEN_VALUE_LIMIT} chars)")


def _check_generic_values(device_id, values):
    for key, value in values.items():
        if isinstance(value, bool) or _is_num(value) or isinstance(value, str):
            continue
        raise DefaultSetSaveError(f"{device_id}: {key!r} must be a flat value (text/number/true/false)")


def _check_devices(registry, devices):
    """Validate device entries: known + controllable + family-shaped values."""
    if not isinstance(devices, dict):
        raise DefaultSetSaveError("devices must be an object of device id to values")
    clean = {}
    for device_id, values in devices.items():
        if device_id not in registry.device_ids:
            raise DefaultSetSaveError(f"unknown device: {device_id}")
        device = registry.effective_device(device_id)
        if device.get("controllable", True) is False:
            raise DefaultSetSaveError(f"{device_id} is not controllable (no reset target)")
        if not isinstance(values, dict) or not values:
            raise DefaultSetSaveError(f"{device_id}: values must be a non-empty object")
        if device.get("type") in ("light", "lightstrip") and device.get("driver") == "ir":
            _check_strip_values(device_id, values)
        elif device.get("type") in ("light", "lightstrip"):
            _check_light_values(device_id, values, device)
        elif device.get("type") == "screen":
            _check_screen_values(device_id, values)
        else:
            _check_generic_values(device_id, values)
        clean[device_id] = dict(values)
    return clean


def _check_plugs(plugs):
    """Validate plug entries: {plug: {"on": bool}}. Existence is not checked
    (the plug map is hand-maintained); unknown plugs simply never match."""
    if not isinstance(plugs, dict):
        raise DefaultSetSaveError("plugs must be an object of plug id to state")
    clean = {}
    for plug, state in plugs.items():
        if not isinstance(plug, str) or not plug.strip():
            raise DefaultSetSaveError(f"plug ids must be non-empty text: {plug!r}")
        if not isinstance(state, dict) or set(state) != {"on"} or not isinstance(state["on"], bool):
            raise DefaultSetSaveError(f"plug {plug.strip()!r} must be {{\"on\": true|false}}")
        clean[plug.strip()] = {"on": state["on"]}
    return clean


def _check_set_id(set_id):
    if not SET_ID_RE.fullmatch(set_id or ""):
        raise DefaultSetSaveError(f"set_id must match {SET_ID_RE.pattern}: {set_id!r}")
    if set_id in RESERVED_IDS:
        raise DefaultSetSaveError(f"set_id is reserved: {set_id!r}")


def _check_label(label, set_id):
    text = set_id if label is None else label
    if not isinstance(text, str) or not text.strip():
        raise DefaultSetSaveError("label must be non-empty text")
    return text.strip()


def _reload(config_root, studio_id):
    try:
        loaded = load_studio(config_root, studio_id)
        profiles = load_profiles(config_root)
        check_references(loaded, profiles, studio_id)
    except ConfigError as exc:
        raise DefaultSetSaveError(f"saved default set broke studio references: {exc}") from exc
    return Registry.from_loaded(loaded, profiles)


def _defaults_path(config_root, studio_id):
    return Path(config_root) / "studios.local" / studio_id / "defaults.json"


def _read_sets(config_root, studio_id):
    """Stored sets for a local studio (missing file reads as empty)."""
    path = _defaults_path(config_root, studio_id)
    if not path.parent.is_dir():
        raise DefaultSetSaveError(f"default sets save only for local studios (no studios.local/{studio_id})")
    if not path.exists():
        return {}
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DefaultSetSaveError(f"cannot read {path}: {exc}") from exc
    if not isinstance(stored, dict):
        raise DefaultSetSaveError(f"{path} must hold an object")
    return stored


def _write_sets(config_root, studio_id, stored):
    path = _defaults_path(config_root, studio_id)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


@router.get("/api/studios/{studio_id}/defaults")
def defaults_list(studio_id: str, request: Request):
    """List a studio's default sets plus its assigned default set id."""
    registry = _registry_or_404(request, studio_id)
    return {
        "default": registry.studio.get("defaultSet"),
        "sets": {set_id: registry.get_default_set(set_id) for set_id in registry.default_set_ids},
    }


@router.get("/api/studios/{studio_id}/defaults/{set_id}")
def defaults_get(studio_id: str, set_id: str, request: Request):
    """Read one default set."""
    registry = _registry_or_404(request, studio_id)
    try:
        return {"set": registry.get_default_set(set_id)}
    except RegistryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/studios/{studio_id}/defaults")
def defaults_create(studio_id: str, body: CreateBody, request: Request):
    """Create a default set (local studios only). 409 unless overwrite."""
    registry = _registry_or_404(request, studio_id)
    try:
        _check_set_id(body.set_id)
        label = _check_label(body.label, body.set_id)
        devices = _check_devices(registry, body.devices)
        plugs = _check_plugs(body.plugs)
        if not devices and not plugs:
            raise DefaultSetSaveError("a default set must hold at least one device or plug entry")
        entry = {"label": label, "devices": devices, "plugs": plugs}
        validate(entry, "defaultset", f"default set {body.set_id}")
        stored = _read_sets(request.app.state.config_root, studio_id)
        overwrote = body.set_id in stored
        if overwrote and not body.overwrite:
            raise DefaultSetConflictError(f"default set {body.set_id} exists (set overwrite to replace)")
        stored[body.set_id] = entry
        _write_sets(request.app.state.config_root, studio_id, stored)
        reloaded = _reload(request.app.state.config_root, studio_id)
    except DefaultSetConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DefaultSetSaveError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    request.app.state.registries[studio_id] = reloaded
    log.info("default set save", extra={"fields": {"studio": studio_id, "set": body.set_id}})
    return {"set": body.set_id, "label": label, "overwrote": overwrote}


@router.put("/api/studios/{studio_id}/defaults/{set_id}")
def defaults_edit(studio_id: str, set_id: str, body: EditBody, request: Request):
    """Replace a default set's label/devices/plugs (local studios only)."""
    registry = _registry_or_404(request, studio_id)
    try:
        current = registry.get_default_set(set_id)
    except RegistryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    sent = body.model_fields_set
    try:
        label = _check_label(body.label, set_id) if "label" in sent else current.get("label", set_id)
        devices = _check_devices(registry, body.devices) if "devices" in sent else dict(current.get("devices") or {})
        plugs = _check_plugs(body.plugs) if "plugs" in sent else dict(current.get("plugs") or {})
        if not devices and not plugs:
            raise DefaultSetSaveError("a default set must hold at least one device or plug entry")
        entry = {"label": label, "devices": devices, "plugs": plugs}
        validate(entry, "defaultset", f"default set {set_id}")
        stored = _read_sets(request.app.state.config_root, studio_id)
        if set_id not in stored:
            raise DefaultSetSaveError(f"unknown default set: {set_id}")
        stored[set_id] = entry
        _write_sets(request.app.state.config_root, studio_id, stored)
        reloaded = _reload(request.app.state.config_root, studio_id)
    except DefaultSetSaveError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    request.app.state.registries[studio_id] = reloaded
    log.info("default set edit", extra={"fields": {"studio": studio_id, "set": set_id}})
    return {"set": set_id, "label": label}


@router.delete("/api/studios/{studio_id}/defaults/{set_id}")
def defaults_delete(studio_id: str, set_id: str, request: Request):
    """Delete a default set (never the studio's assigned default)."""
    registry = _registry_or_404(request, studio_id)
    if registry.studio.get("defaultSet") == set_id:
        raise HTTPException(
            status_code=422, detail=f"{set_id} is the studio default (assign another set first)"
        )
    try:
        stored = _read_sets(request.app.state.config_root, studio_id)
        if set_id not in stored:
            raise HTTPException(status_code=404, detail=f"unknown default set: {set_id}")
        del stored[set_id]
        _write_sets(request.app.state.config_root, studio_id, stored)
        reloaded = _reload(request.app.state.config_root, studio_id)
    except DefaultSetSaveError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    request.app.state.registries[studio_id] = reloaded
    log.info("default set delete", extra={"fields": {"studio": studio_id, "set": set_id}})
    return {"set": set_id, "deleted": True}


@router.post("/api/studios/{studio_id}/defaults/assign")
def defaults_assign(studio_id: str, body: AssignBody, request: Request):
    """Assign (or clear, with null) the studio's default set (local only)."""
    registry = _registry_or_404(request, studio_id)
    if body.set_id is not None and body.set_id not in registry.default_set_ids:
        raise HTTPException(status_code=422, detail=f"unknown default set: {body.set_id}")
    studio_path = Path(request.app.state.config_root) / "studios.local" / studio_id / "studio.json"
    if not studio_path.exists():
        raise HTTPException(
            status_code=422, detail=f"default assign only for local studios (no studios.local/{studio_id})"
        )
    try:
        meta = json.loads(studio_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"cannot read {studio_path}: {exc}") from exc
    if body.set_id is None:
        meta.pop("defaultSet", None)
    else:
        meta["defaultSet"] = body.set_id
    try:
        validate(meta, "studio", str(studio_path))
    except ConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    tmp_path = studio_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    os.replace(tmp_path, studio_path)
    try:
        reloaded = _reload(request.app.state.config_root, studio_id)
    except DefaultSetSaveError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    request.app.state.registries[studio_id] = reloaded
    log.info("default set assign", extra={"fields": {"studio": studio_id, "set": body.set_id}})
    return {"default": body.set_id}
