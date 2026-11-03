# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hardware snapshots: save the live rig look as a preset.

Reads hub-known state only (last-sent looks, universe buffer, screen
state) — fixtures never report back, so anything uncommanded since hub
start is skipped and named, never guessed. The snapshot saves through
the same strict gate as hand-written presets.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..core.logging import get_logger
from ..drivers.dmx_light import DmxLightDriver
from ..drivers.dmx_usb import get_shared_driver
from ..drivers.gvm_ble import panel_look
from ..drivers.screen import screen_state
from .ai import PresetConflictError, PresetSaveError, save_preset
from .ninja import _fixed_cct, cached_driver

log = get_logger("api")
router = APIRouter()

# Driver-side GVM look keys to generic preset keys (page keeps HSI context).
GVM_SNAPSHOT_KEYS = {
    "power": "power",
    "brightness": "intensity",
    "cct": "cct",
    "hue": "hue",
    "saturation": "saturation",
    "page": "page",
}


class FromHardwareBody(BaseModel):
    preset_id: str
    label: str | None = None
    lights: list | None = None  # null = all controllable lights
    screens: list | None = None  # null = all screens
    overwrite: bool = False


def _is_dmx_channel(channel):
    return isinstance(channel, int) and not isinstance(channel, bool) and 1 <= channel <= 512


def _snapshot_gvm(device_id, device, skipped):
    """Generic look from the panel's last-sent BLE echoes (or a skip note)."""
    address = (device.get("driverAddress") or {}).get("ble")
    if not address:
        skipped.append({"target": device_id, "reason": f"{device_id} has no BLE address"})
        return None
    look = panel_look(address)
    if not look:
        skipped.append({"target": device_id, "reason": f"{device_id} untouched since hub start"})
        return None
    if "scene" in look:
        skipped.append(
            {"target": device_id, "reason": f"{device_id} is in scene {look['scene']} (scenes don't snapshot)"}
        )
        return None
    return {GVM_SNAPSHOT_KEYS[key]: look[key] for key in look if key in GVM_SNAPSHOT_KEYS}


def _snapshot_ninja(device_id, device, registry, skipped):
    """Generic look from the shared broadcast cache (or a skip note)."""
    address = device.get("driverAddress") or {}
    driver = cached_driver(address.get("channel"), address.get("group"))
    if driver is None:
        skipped.append({"target": device_id, "reason": f"{device_id} has no sent look (bad map or untouched)"})
        return None
    state = driver.get_state().state
    if not state.get("transmitted"):
        skipped.append({"target": device_id, "reason": f"{device_id} untouched since hub start"})
        return None
    look = state.get("look") or {}
    if look.get("scene") is not None:
        skipped.append({"target": device_id, "reason": f"{device_id} is in a scene (scenes don't snapshot)"})
        return None
    spec = registry.effective_device(device_id).get("spec") or {}
    generic = {"intensity": look.get("brightness", 0)}
    if spec.get("fixedTemp") is not True:
        generic["cct"] = look.get("cct", _fixed_cct(spec) or 5600)
    if look.get("gm") or look.get("preset") is not None:
        skipped.append(
            {"target": f"{device_id}.gm-preset", "reason": f"{device_id}: gm/preset are bench-only (not snapshotted)"}
        )
    return generic


def _snapshot_dmx(device_id, device, registry, skipped):
    """Generic look from the shared universe buffer (or a skip note)."""
    address = (device.get("driverAddress") or {}).get("dmx") or {}
    if not _is_dmx_channel(address.get("dimmer")):
        skipped.append({"target": device_id, "reason": f"{device_id} has a bad DMX channel map"})
        return None
    universe = get_shared_driver()
    if not getattr(universe, "emitted", False):
        skipped.append({"target": device_id, "reason": f"{device_id}: universe never emitted"})
        return None
    if getattr(universe, "blackout", False):
        skipped.append({"target": device_id, "reason": f"{device_id}: universe blacked out (live levels are 0)"})
        return None
    spec = registry.effective_device(device_id).get("spec") or {}
    bounds = spec.get("cctRange")
    cct_range = (
        bounds
        if address.get("cct") is not None and isinstance(bounds, list) and len(bounds) == 2
        else None
    )
    light = DmxLightDriver(device_id, universe, address.get("dimmer"), address.get("cct"), cct_range)
    return dict(light.get_state().state)


def _snapshot_screen(screen_id, skipped):
    """Theme/text/wash from hub screen state (or a skip note)."""
    state = screen_state(screen_id)
    if not state:
        skipped.append({"target": screen_id, "reason": f"{screen_id} untouched since hub start"})
        return None
    if state.get("audio") == "play":
        skipped.append(
            {"target": f"{screen_id}.audio", "reason": f"{screen_id}: audio bed not snapshotted (presets carry theme/text)"}
        )
    return {key: state[key] for key in ("theme", "text", "wash") if key in state}


@router.post("/api/studios/{studio_id}/presets/from-hardware")
def presets_from_hardware(studio_id: str, body: FromHardwareBody, request: Request):
    """Snapshot the live rig look and save it as a preset (local studios only).

    Null lights/screens select everything snapshottable; explicit lists
    422 on unknown ids. Uncommanded/unreadable devices are skipped and
    named. The snapshot saves through the strict preset gate.
    """
    try:
        registry = request.app.state.registries[studio_id]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown studio: {studio_id}") from exc
    if body.lights is None:
        want_lights = [
            device_id
            for device_id in registry.device_ids
            if registry.get_device(device_id).get("type") in ("light", "lightstrip")
            and registry.get_device(device_id).get("controllable", True) is not False
        ]
    else:
        unknown = [item for item in body.lights if item not in registry.device_ids]
        if unknown:
            raise HTTPException(status_code=422, detail=f"unknown snapshot lights: {unknown}")
        want_lights = list(body.lights)
    screen_ids = [
        device_id
        for device_id in registry.device_ids
        if registry.get_device(device_id).get("driver") == "screen"
    ]
    if body.screens is None:
        want_screens = list(screen_ids)
    else:
        unknown = [item for item in body.screens if item not in registry.device_ids]
        if unknown:
            raise HTTPException(status_code=422, detail=f"unknown snapshot screens: {unknown}")
        want_screens = list(body.screens)
    lights, screens, skipped = {}, {}, []
    for device_id in want_lights:
        device = registry.get_device(device_id)
        if device.get("type") not in ("light", "lightstrip"):
            skipped.append({"target": device_id, "reason": f"{device_id} is not a light"})
            continue
        if device.get("controllable", True) is False:
            skipped.append({"target": device_id, "reason": f"{device_id} is not controllable"})
            continue
        family = device.get("driver")
        if family == "gvm":
            snap = _snapshot_gvm(device_id, device, skipped)
        elif family == "ninja":
            snap = _snapshot_ninja(device_id, device, registry, skipped)
        elif family == "dmx":
            snap = _snapshot_dmx(device_id, device, registry, skipped)
        elif family == "ir":
            snap = None
            skipped.append({"target": device_id, "reason": f"{device_id}: strip has no readable state"})
        else:
            snap = None
            skipped.append({"target": device_id, "reason": f"{device_id}: no snapshot for driver family {family!r}"})
        if snap:
            lights[device_id] = snap
    for screen_id in want_screens:
        if registry.get_device(screen_id).get("driver") != "screen":
            skipped.append({"target": screen_id, "reason": f"{screen_id} is not a screen"})
            continue
        snap = _snapshot_screen(screen_id, skipped)
        if snap:
            screens[screen_id] = snap
    if not lights and not screens:
        detail = "; ".join(item["reason"] for item in skipped) or "nothing selected"
        raise HTTPException(status_code=422, detail=f"nothing snapshottable ({detail})")
    preset = {"label": body.label or body.preset_id, "lights": lights, "screen": screens}
    log.info("preset from-hardware", extra={"fields": {"studio": studio_id, "preset": body.preset_id}})
    try:
        reloaded, overwrote = save_preset(
            request.app.state.config_root, registry, body.preset_id, preset, body.overwrite
        )
    except PresetConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PresetSaveError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    request.app.state.registries[studio_id] = reloaded
    return {
        "preset": body.preset_id,
        "label": preset["label"],
        "overwrote": overwrote,
        "lights": lights,
        "screens": screens,
        "skipped": skipped,
    }
