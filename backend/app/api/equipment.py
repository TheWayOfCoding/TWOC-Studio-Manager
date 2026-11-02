# SPDX-License-Identifier: AGPL-3.0-or-later
"""Equipment datapoints: per-device freeform details, editable, AI-visible.

Details are a freeform {label: value} bag (labels may be whole phrases)
for facts like modifiers, distances, and diffusion that help the AI
reason about a fixture. Built-in fields stay schema-owned and read-only
here; only the bag is editable, so saves cannot break validation.
"""

import json
import os
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
from ..core.logging import get_logger
from ..registry import Registry, RegistryError

log = get_logger("api")
router = APIRouter()

MAX_KEY_LEN = 120


class DetailsBody(BaseModel):
    details: dict


class DetailsSaveError(Exception):
    """Unusable save request (non-local studio, bad shape, broken file)."""


def _registry_or_404(request, studio_id):
    try:
        return request.app.state.registries[studio_id]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown studio: {studio_id}") from exc


def _check_details(details):
    """Validate the bag: non-empty labels, text-or-number values."""
    if not isinstance(details, dict):
        raise DetailsSaveError("details must be an object of label to value")
    clean = {}
    for key, value in details.items():
        if not isinstance(key, str) or not key.strip() or len(key.strip()) > MAX_KEY_LEN:
            raise DetailsSaveError(f"detail labels must be 1..{MAX_KEY_LEN} chars: {key!r}")
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise DetailsSaveError(f"detail {key.strip()!r} must be text or a number")
        clean[key.strip()] = value
    return clean


def save_details(config_root, studio_id, device_id, details):
    """Validate, persist to studios.local devices.json, reload registry.

    Only studios with a studios.local directory can receive details; tracked
    example studios are refused. Returns the reloaded Registry.
    """
    devices_path = Path(config_root) / "studios.local" / studio_id / "devices.json"
    if not devices_path.exists():
        raise DetailsSaveError(f"details save only for local studios (no studios.local/{studio_id})")
    try:
        stored = json.loads(devices_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DetailsSaveError(f"cannot read {devices_path}: {exc}") from exc
    if not isinstance(stored, list):
        raise DetailsSaveError(f"{devices_path} must hold a device list")
    matches = [item for item in stored if isinstance(item, dict) and item.get("id") == device_id]
    if not matches:
        raise DetailsSaveError(f"device {device_id} not in {devices_path}")
    matches[0]["details"] = details
    try:
        validate(matches[0], "device", f"device {device_id}")
    except ConfigError as exc:
        raise DetailsSaveError(str(exc)) from exc
    tmp_path = devices_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    os.replace(tmp_path, devices_path)
    try:
        loaded = load_studio(config_root, studio_id)
        profiles = load_profiles(config_root)
        check_references(loaded, profiles, studio_id)
    except ConfigError as exc:
        raise DetailsSaveError(f"saved details broke studio references: {exc}") from exc
    return Registry.from_loaded(loaded, profiles)


@router.get("/api/studios/{studio_id}/devices/{device_id}")
def equipment_get(studio_id: str, device_id: str, request: Request):
    """One effective device (instance merged with profile) plus its details bag."""
    registry: Registry = _registry_or_404(request, studio_id)
    try:
        effective = registry.effective_device(device_id)
    except RegistryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"device": effective, "details": effective.get("details") or {}}


@router.put("/api/studios/{studio_id}/devices/{device_id}/details")
def equipment_save(studio_id: str, device_id: str, body: DetailsBody, request: Request):
    """Replace a device's details bag (local studios only)."""
    _registry_or_404(request, studio_id)
    if device_id not in request.app.state.registries[studio_id].device_ids:
        raise HTTPException(status_code=404, detail=f"unknown device: {device_id}")
    log.info("equipment save", extra={"fields": {"studio": studio_id, "device": device_id}})
    try:
        clean = _check_details(body.details)
        reloaded = save_details(request.app.state.config_root, studio_id, device_id, clean)
    except DetailsSaveError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    request.app.state.registries[studio_id] = reloaded
    return {"device": device_id, "details": clean}
