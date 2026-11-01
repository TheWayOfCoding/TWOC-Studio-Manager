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
from ..drivers.dmx_light import DmxLightDriver
from ..drivers.dmx_usb import get_shared_driver
from ..drivers.gvm_ble import GvmBleDriver
from ..drivers.kasa import KasaDriver
from ..drivers.obs_ws import DEFAULT_PORT as OBS_DEFAULT_PORT
from ..drivers.obs_ws import ObsWsDriver
from ..drivers.screen import ScreenDriver
from ..modules.screens import ScreensModule
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
    """Claim plug-map entries whose zone exists in this studio. Hosts stay server-side.

    Entries may carry optional "username"/"password" for account-locked plugs
    (hub-local file, never committed, never echoed in responses).
    """
    zone_ids = set(registry.zone_ids)
    drivers = {}
    for entry in _plug_map(config_root):
        if not isinstance(entry, dict):
            continue
        plug, host, zone = entry.get("plug"), entry.get("host"), entry.get("zone")
        if not plug or not host or zone not in zone_ids:
            continue
        drivers[plug] = KasaDriver(plug, host, entry.get("username"), entry.get("password"))
    return drivers


def _light_drivers(registry):
    """One BLE driver per addressed GVM panel, one DMX adapter per mapped light.

    DMX adapters share the bench's universe driver, so fades walk from the
    same buffered levels on every surface. Bad channel maps still wire here;
    the planner names them (degraded, never silent). Never exposes addresses.
    """
    drivers = {}
    universe = None
    for device_id in registry.device_ids:
        device = registry.get_device(device_id)
        family = device.get("driver")
        if family == "gvm":
            address = (device.get("driverAddress") or {}).get("ble")
            if not address:
                continue
            drivers[device_id] = GvmBleDriver(device_id, address)
        elif family == "dmx":
            if not device.get("controllable", True):
                continue
            if universe is None:
                universe = get_shared_driver()
            address = (device.get("driverAddress") or {}).get("dmx") or {}
            spec = registry.effective_device(device_id).get("spec") or {}
            bounds = spec.get("cctRange")
            cct_range = (
                bounds
                if address.get("cct") is not None and isinstance(bounds, list) and len(bounds) == 2
                else None
            )
            drivers[device_id] = DmxLightDriver(
                device_id, universe, address.get("dimmer"), address.get("cct"), cct_range
            )
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


def _obs_applier(registry, config_root):
    """Per-request OBS scene applier from hub-local obs.json. None when unconfigured.

    obs.json maps studio ids to {"host", "port"?, "password"?}; a studio
    without an entry keeps the honest "obs applier not wired" skip. A missing
    file is normal (OBS not configured yet); only malformed JSON warns.
    Passwords are passed to the driver, never echoed in responses.
    """
    path = Path(config_root) / "studios.local" / "obs.json"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        entries = json.loads(raw).get("studios") or {}
    except ValueError as exc:
        log.warning("obs.json malformed: %s", exc)
        return None
    cfg = entries.get(registry.studio.get("id")) or {}
    if not isinstance(cfg, dict) or not cfg.get("host"):
        return None
    host, port, password = cfg["host"], cfg.get("port", OBS_DEFAULT_PORT), cfg.get("password")

    def apply_scene(scene):
        driver = ObsWsDriver("obs-modes", host, port, password)
        try:
            return driver.apply({"action": "switch", "scene": scene})
        finally:
            driver.safe_stop()

    return apply_scene


def _screens_applier(registry):
    """Screens applier over screen-driver devices. None when the studio has none."""
    drivers = {}
    for device_id in registry.device_ids:
        if registry.get_device(device_id).get("driver") == "screen":
            drivers[device_id] = ScreenDriver(f"screen-{device_id}", device_id)
    if not drivers:
        return None
    return ScreensModule("screens", drivers).apply_mapping


def build_modes_module(registry, config_root):
    """Wire a modes module: plug-map power, BLE + DMX lights, OBS, screens."""
    plugs = _plug_drivers(registry, config_root)
    power = PowerModule("power", registry, plugs) if plugs else None
    return ModesModule(
        "modes",
        registry,
        power=power,
        lights=_light_drivers(registry),
        obs=_obs_applier(registry, config_root),
        screens=_screens_applier(registry),
    )


def _apply_fade(body):
    """Optional apply-time fade override. None means the content's own."""
    if body is None:
        return None
    return body.get("fade_s")


def _apply_power_delay(body):
    """Optional apply-time power stagger (0..5s). None means instant."""
    if body is None:
        return None
    return body.get("power_delay_s")


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
def modes_apply(studio_id: str, mode_id: str, request: Request, body: dict | None = None):
    """Apply a mode across power, lights, OBS, and screens. Staged, gated.

    Optional body {"fade_s": dwell, "power_delay_s": secs} fades every
    light and staggers plug steps for this apply only.
    """
    registry: Registry = _registry_or_404(request, studio_id)
    module = build_modes_module(registry, request.app.state.config_root)
    log.info("mode apply", extra={"fields": {"studio": studio_id, "mode": mode_id}})
    try:
        result = module.apply_mode(mode_id, fade_s=_apply_fade(body), power_delay_s=_apply_power_delay(body))
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
def presets_apply(studio_id: str, preset_id: str, request: Request, body: dict | None = None):
    """Apply a preset's lights (and screen when the screen module lands).

    Optional body {"fade_s": dwell} fades every light for this apply only
    (power_delay_s is accepted but presets never touch power).
    """
    registry: Registry = _registry_or_404(request, studio_id)
    module = build_modes_module(registry, request.app.state.config_root)
    log.info("preset apply", extra={"fields": {"studio": studio_id, "preset": preset_id}})
    try:
        result = module.apply_preset(preset_id, fade_s=_apply_fade(body), power_delay_s=_apply_power_delay(body))
    except UnknownPresetError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (UnknownDeviceError, UnknownPowerGroupError, ModesError, SafetyViolation) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _scrub(result, _address_labels(registry))
