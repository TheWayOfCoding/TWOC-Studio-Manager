# SPDX-License-Identifier: AGPL-3.0-or-later
"""Mode and preset endpoints: list catalogs, apply across subsystems.

Driver wiring is honest: plugs resolve through the LAN plug-map (entries are
claimed by zone, so the example studio never touches real hardware), GVM
panels through registry BLE addresses. Anything unwired degrades by name.
"""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from ..core.logging import get_logger
from ..core.safety import SafetyViolation
from ..drivers.gvm_ble import GvmBleDriver
from ..drivers.kasa import KasaDriver
from ..modules.modes import ModesError, ModesModule
from ..modules.power import PowerModule, UnknownPowerGroupError
from ..registry import (
    Registry,
    UnknownDeviceError,
    UnknownModeError,
    UnknownPresetError,
)

log = get_logger("api")
router = APIRouter()


def _registry_or_404(request, studio_id):
    try:
        return request.app.state.registries[studio_id]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown studio: {studio_id}") from exc


def _plug_map(config_root):
    """LAN plug inventory. Missing or broken reads as empty (degraded power)."""
    path = Path(config_root) / "studios.local" / "plug-map.json"
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))["plugs"]
    except (OSError, ValueError, KeyError) as exc:
        log.warning("plug-map unreadable: %s", exc)
        return []
    return entries if isinstance(entries, list) else []


def _plug_drivers(registry, config_root):
    """Claim plug-map entries whose zone exists in this studio. Hosts stay server-side."""
    zone_ids = set(registry.zone_ids)
    drivers = {}
    for entry in _plug_map(config_root):
        if not isinstance(entry, dict):
            continue
        plug, host, zone = entry.get("plug"), entry.get("host"), entry.get("zone")
        if not plug or not host or zone not in zone_ids:
            continue
        drivers[plug] = KasaDriver(plug, host)
    return drivers


def _light_drivers(registry):
    """One BLE driver per addressed GVM panel. Never exposes addresses."""
    drivers = {}
    for device_id in registry.device_ids:
        device = registry.get_device(device_id)
        if device.get("driver") != "gvm":
            continue
        address = (device.get("driverAddress") or {}).get("ble")
        if not address:
            continue
        drivers[device_id] = GvmBleDriver(device_id, address)
    return drivers


def _address_labels(registry):
    """BLE address -> panel label, for scrubbing hardware echoes from responses."""
    mapping = {}
    for device_id in registry.device_ids:
        device = registry.get_device(device_id)
        address = (device.get("driverAddress") or {}).get("ble")
        if address:
            mapping[address] = device.get("name", device_id)
    return mapping


def _scrub(node, mapping):
    if not mapping:
        return node
    if isinstance(node, dict):
        return {key: _scrub(value, mapping) for key, value in node.items()}
    if isinstance(node, list):
        return [_scrub(value, mapping) for value in node]
    if isinstance(node, str):
        for address, label in mapping.items():
            node = node.replace(address, label)
    return node


def build_modes_module(registry, config_root):
    """Wire a modes module for one studio: plug-map power, BLE lights, later phases None."""
    plugs = _plug_drivers(registry, config_root)
    power = PowerModule("power", registry, plugs) if plugs else None
    return ModesModule("modes", registry, power=power, lights=_light_drivers(registry))


@router.get("/api/studios/{studio_id}/modes")
def modes_list(studio_id: str, request: Request):
    """List a studio's modes plus its default mode id."""
    registry: Registry = _registry_or_404(request, studio_id)
    return {
        "default": registry.studio.get("defaultMode"),
        "modes": [
            {"id": mode_id, "label": registry.get_mode(mode_id).get("label", mode_id)}
            for mode_id in registry.mode_ids
        ],
    }


@router.post("/api/studios/{studio_id}/modes/{mode_id}/apply")
def modes_apply(studio_id: str, mode_id: str, request: Request):
    """Apply a mode across power, lights, OBS, and screens. Staged, gated."""
    registry: Registry = _registry_or_404(request, studio_id)
    module = build_modes_module(registry, request.app.state.config_root)
    log.info("mode apply", extra={"fields": {"studio": studio_id, "mode": mode_id}})
    try:
        result = module.apply_mode(mode_id)
    except UnknownModeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (UnknownDeviceError, UnknownPowerGroupError, ModesError, SafetyViolation) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _scrub(result, _address_labels(registry))


@router.get("/api/studios/{studio_id}/presets")
def presets_list(studio_id: str, request: Request):
    """List a studio's presets."""
    registry: Registry = _registry_or_404(request, studio_id)
    return {
        "presets": [
            {"id": preset_id, "label": registry.get_preset(preset_id).get("label", preset_id)}
            for preset_id in registry.preset_ids
        ]
    }


@router.post("/api/studios/{studio_id}/presets/{preset_id}/apply")
def presets_apply(studio_id: str, preset_id: str, request: Request):
    """Apply a preset's lights (and screen when the screen module lands)."""
    registry: Registry = _registry_or_404(request, studio_id)
    module = build_modes_module(registry, request.app.state.config_root)
    log.info("preset apply", extra={"fields": {"studio": studio_id, "preset": preset_id}})
    try:
        result = module.apply_preset(preset_id)
    except UnknownPresetError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (UnknownDeviceError, UnknownPowerGroupError, ModesError, SafetyViolation) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _scrub(result, _address_labels(registry))
