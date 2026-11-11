# SPDX-License-Identifier: AGPL-3.0-or-later
"""Equipment editing: per-device details, properties, and zone moves.

Details are a freeform {label: value} bag (labels may be whole phrases)
for facts like modifiers, distances, and diffusion that help the AI
reason about a fixture. Descriptive properties, dual-protocol transport,
and zone membership are editable through validated saves; identity
fields (id, type, plug, driver) stay read-only so a save can never
rewire the studio.
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
from ..config.ranges import effective_ranges
from ..core.logging import get_logger
from ..registry import Registry, RegistryError

log = get_logger("api")
router = APIRouter()

MAX_KEY_LEN = 120


class DetailsBody(BaseModel):
    details: dict


class PropertiesBody(BaseModel):
    name: str | None = None
    position: str | None = None
    role: str | None = None
    notes: str | None = None
    estWatts: float | None = None


EDITABLE_PROPERTIES = ("name", "position", "role", "notes", "estWatts")


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


@router.get("/api/studios/{studio_id}/ranges")
def equipment_ranges(studio_id: str, request: Request):
    """Effective value ranges for every device (the one resolver, served).

    Each entry carries the device's kind plus per-property control shapes
    (numeric min/max/step/source, enums, text limits) resolved from profile
    spec + capabilities. Control sliders, the Default-sets builder, and the
    data map all read this instead of hardcoding spans.
    """
    registry: Registry = _registry_or_404(request, studio_id)
    devices = {}
    for device_id in registry.device_ids:
        effective = registry.effective_device(device_id)
        devices[device_id] = {
            "name": effective.get("name", device_id),
            "type": effective.get("type"),
            "driver": effective.get("driver"),
            "controllable": effective.get("controllable", True),
            "profile": effective.get("profile"),
            **effective_ranges(effective),
        }
    return {"devices": devices}


@router.get("/api/studios/{studio_id}/devices/{device_id}")
def equipment_get(studio_id: str, device_id: str, request: Request):
    """One effective device (instance merged with profile) plus its details bag."""
    registry: Registry = _registry_or_404(request, studio_id)
    try:
        effective = registry.effective_device(device_id)
    except RegistryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"device": effective, "details": effective.get("details") or {}}


def save_properties(config_root, studio_id, device_id, updates):
    """Persist editable built-in fields, reload registry (local only).

    Callers pass validated single-purpose updates (descriptive
    properties, dual-protocol transport, zone membership); identity
    fields (id, type, plug, driver) always stay put so a save can never
    rewire the studio. Values are validated against the device schema
    before anything is written.
    """
    devices_path = Path(config_root) / "studios.local" / studio_id / "devices.json"
    if not devices_path.exists():
        raise DetailsSaveError(f"property save only for local studios (no studios.local/{studio_id})")
    try:
        stored = json.loads(devices_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DetailsSaveError(f"cannot read {devices_path}: {exc}") from exc
    if not isinstance(stored, list):
        raise DetailsSaveError(f"{devices_path} must hold a device list")
    matches = [item for item in stored if isinstance(item, dict) and item.get("id") == device_id]
    if not matches:
        raise DetailsSaveError(f"device {device_id} not in {devices_path}")
    candidate = dict(matches[0])
    for key, value in updates.items():
        if value is None:
            candidate.pop(key, None)
        else:
            candidate[key] = value
    if not candidate.get("name"):
        raise DetailsSaveError("name must not be empty")
    try:
        validate(candidate, "device", f"device {device_id}")
    except ConfigError as exc:
        raise DetailsSaveError(str(exc)) from exc
    matches[0].clear()
    matches[0].update(candidate)
    tmp_path = devices_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    os.replace(tmp_path, devices_path)
    try:
        loaded = load_studio(config_root, studio_id)
        profiles = load_profiles(config_root)
        check_references(loaded, profiles, studio_id)
    except ConfigError as exc:
        raise DetailsSaveError(f"saved properties broke studio references: {exc}") from exc
    return Registry.from_loaded(loaded, profiles)


@router.put("/api/studios/{studio_id}/devices/{device_id}/properties")
def equipment_save_properties(studio_id: str, device_id: str, body: PropertiesBody, request: Request):
    """Update a device's editable built-in properties (local studios only)."""
    _registry_or_404(request, studio_id)
    if device_id not in request.app.state.registries[studio_id].device_ids:
        raise HTTPException(status_code=404, detail=f"unknown device: {device_id}")
    updates = {k: v for k, v in body.model_dump().items() if k in EDITABLE_PROPERTIES and k in body.model_fields_set}
    if not updates:
        raise HTTPException(status_code=422, detail="no editable properties sent")
    if "estWatts" in updates and updates["estWatts"] is not None and updates["estWatts"] < 0:
        raise HTTPException(status_code=422, detail="estWatts must be >= 0")
    log.info("equipment properties save", extra={"fields": {"studio": studio_id, "device": device_id}})
    try:
        reloaded = save_properties(request.app.state.config_root, studio_id, device_id, updates)
    except DetailsSaveError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    request.app.state.registries[studio_id] = reloaded
    return {"device": device_id, "properties": updates}


class TransportBody(BaseModel):
    transport: str


@router.put("/api/studios/{studio_id}/devices/{device_id}/transport")
def equipment_save_transport(studio_id: str, device_id: str, body: TransportBody, request: Request):
    """Select BLE or DMX for a dual-protocol fixture (local studios only).

    The device must actually carry both addresses; the selection is
    stored as `transport` and every surface rewires from it.
    """
    registry = _registry_or_404(request, studio_id)
    if device_id not in registry.device_ids:
        raise HTTPException(status_code=404, detail=f"unknown device: {device_id}")
    if body.transport not in ("ble", "dmx"):
        raise HTTPException(status_code=422, detail='transport must be "ble"|"dmx"')
    device = registry.get_device(device_id)
    address = device.get("driverAddress") or {}
    has_ble = bool(address.get("channel") and address.get("group"))
    has_dmx = bool((address.get("dmx") or {}).get("dimmer"))
    if not (has_ble and has_dmx):
        raise HTTPException(status_code=422, detail=f"{device_id} is not a dual-protocol fixture")
    try:
        reloaded = save_properties(request.app.state.config_root, studio_id, device_id,
                                   {"transport": body.transport})
    except DetailsSaveError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    request.app.state.registries[studio_id] = reloaded
    return {"device": device_id, "transport": body.transport}


class AddressBody(BaseModel):
    driverAddress: dict


MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


def _check_int(value, low, high, where):
    """Integer channel in span (bools rejected). Raises DetailsSaveError."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise DetailsSaveError(f"{where} must be an integer")
    if not low <= value <= high:
        raise DetailsSaveError(f"{where} must be {low}..{high}")
    return value


def check_address(driver, address):
    """Validate + normalize a driver address for its family.

    Shapes follow what each driver needs: gvm takes a BLE MAC; ninja/dmx
    take a BLE channel+group pair and/or a DMX dimmer map (CCT optional,
    for fixed-temp single-channel fixtures to omit). BLE halves arrive as
    a pair or not at all; at least one half must stay. Returns the clean
    address (group upper-cased, MAC upper-cased).
    """
    if not isinstance(address, dict):
        raise DetailsSaveError("driverAddress must be an object")
    if driver == "gvm":
        unknown = set(address) - {"ble"}
        if unknown:
            raise DetailsSaveError(f"gvm address takes only ble (got {sorted(unknown)})")
        ble = address.get("ble")
        if not isinstance(ble, str) or not MAC_RE.match(ble.strip()):
            raise DetailsSaveError("gvm ble must be a MAC like AA:BB:CC:DD:EE:FF")
        return {"ble": ble.strip().upper()}
    if driver in ("ninja", "dmx"):
        unknown = set(address) - {"channel", "group", "dmx"}
        if unknown:
            raise DetailsSaveError(f"{driver} address takes only channel/group/dmx (got {sorted(unknown)})")
        clean = {}
        if "channel" in address or "group" in address:
            if "channel" not in address or "group" not in address:
                raise DetailsSaveError("BLE needs both channel and group (or neither to remove it)")
            clean["channel"] = _check_int(address["channel"], 1, 19, "BLE channel")
            group = address["group"]
            if not isinstance(group, str) or len(group.strip()) != 1 or group.strip().upper() not in "ABCDEF":
                raise DetailsSaveError("BLE group must be one letter A..F")
            clean["group"] = group.strip().upper()
        if "dmx" in address:
            dmx = address["dmx"]
            if not isinstance(dmx, dict):
                raise DetailsSaveError("dmx must be an object")
            unknown_dmx = set(dmx) - {"dimmer", "cct"}
            if unknown_dmx:
                raise DetailsSaveError(f"dmx takes only dimmer/cct (got {sorted(unknown_dmx)})")
            if "dimmer" not in dmx:
                raise DetailsSaveError("dmx needs a dimmer channel (or drop dmx to remove it)")
            clean_dmx = {"dimmer": _check_int(dmx["dimmer"], 1, 512, "DMX dimmer")}
            if dmx.get("cct") is not None:
                clean_dmx["cct"] = _check_int(dmx["cct"], 1, 512, "DMX CCT")
            clean["dmx"] = clean_dmx
        if not clean:
            raise DetailsSaveError(f"{driver} needs at least one half (BLE and/or DMX)")
        return clean
    raise DetailsSaveError(f"no editable address on driver {driver!r} (gvm/ninja/dmx only)")


class ZoneBody(BaseModel):
    zone: str


@router.put("/api/studios/{studio_id}/devices/{device_id}/zone")
def equipment_move_zone(studio_id: str, device_id: str, body: ZoneBody, request: Request):
    """Move a device to another zone (local studios only).

    Every device lives in exactly one zone, so removing from one zone
    means adding to another. Power wiring (group/plug/policy) is never
    touched here — this only changes which zone lists the asset.
    """
    registry = _registry_or_404(request, studio_id)
    if device_id not in registry.device_ids:
        raise HTTPException(status_code=404, detail=f"unknown device: {device_id}")
    if body.zone not in registry.zone_ids:
        raise HTTPException(status_code=422, detail=f"unknown zone: {body.zone}")
    log.info("equipment zone move", extra={"fields": {"studio": studio_id, "device": device_id, "zone": body.zone}})
    try:
        reloaded = save_properties(request.app.state.config_root, studio_id, device_id, {"zone": body.zone})
    except DetailsSaveError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    request.app.state.registries[studio_id] = reloaded
    return {"device": device_id, "zone": body.zone}


@router.put("/api/studios/{studio_id}/devices/{device_id}/address")
def equipment_save_address(studio_id: str, device_id: str, body: AddressBody, request: Request):
    """Replace a device's driver address (local studios only).

    Shapes are validated per driver family (GVM MAC; Ninja/DMX BLE pair
    and/or DMX dimmer map). Dropping the half the transport pick points at
    moves the pick to the surviving half and says so.
    """
    registry = _registry_or_404(request, studio_id)
    if device_id not in registry.device_ids:
        raise HTTPException(status_code=404, detail=f"unknown device: {device_id}")
    devices_path = Path(request.app.state.config_root) / "studios.local" / studio_id / "devices.json"
    if not devices_path.exists():
        raise HTTPException(
            status_code=422, detail=f"address save only for local studios (no studios.local/{studio_id})"
        )
    device = registry.get_device(device_id)
    try:
        clean = check_address(device.get("driver"), body.driverAddress)
    except DetailsSaveError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    updates = {"driverAddress": clean}
    coerced = False
    transport = device.get("transport")
    if transport in ("ble", "dmx"):
        has_ble = bool(clean.get("channel") and clean.get("group"))
        has_dmx = bool((clean.get("dmx") or {}).get("dimmer"))
        if not (has_ble and has_dmx):
            surviving = "ble" if has_ble else ("dmx" if has_dmx else None)
            if transport != surviving:
                updates["transport"] = surviving
                coerced = True
    log.info("equipment address save", extra={"fields": {"studio": studio_id, "device": device_id}})
    try:
        reloaded = save_properties(request.app.state.config_root, studio_id, device_id, updates)
    except DetailsSaveError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    request.app.state.registries[studio_id] = reloaded
    return {
        "device": device_id,
        "address": clean,
        "transport": reloaded.get_device(device_id).get("transport"),
        "transport_coerced": coerced,
    }


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
