# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bench endpoints for Ninja BLE bring-up (Weeylite/Viltrox broadcast control).

Gentle range, enforced here (mirrors DMX): brightness at or below 10%
passes freely; anything brighter needs allow_bright. Scenes need
allow_intense until verified gentle (D-PHOTO). One driver instance is
cached per address so looks and the broadcast counter continue across
calls; without the cache every send would restart from defaults.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..core.logging import get_logger
from ..drivers.ninja_ble import (
    GENTLE_PERCENT_MAX,
    NinjaBleDriver,
    NinjaError,
    address_byte,
    scene_index,
)
from ..modules.ninja import NinjaModule, NinjaModuleError, scene_risk
from ..registry import UnknownDeviceError

router = APIRouter()
log = get_logger("api")

NINJA_DRIVER = "ninja"

_DRIVERS = {}


class TargetBody(BaseModel):
    light: str | None = None
    channel: int | None = None
    group: str | None = None
    mock: bool = False


class SetBody(TargetBody):
    power: str | None = None
    brightness: int | None = None
    cct: int | None = None
    gm: int | None = None
    preset: int | None = None
    scene: int | str | None = None
    repeats: int = 2
    allow_bright: bool = False
    allow_intense: bool = False


class RawBody(TargetBody):
    payload: str
    repeats: int = 2


def _ninja_devices(request):
    """Yield (studio_id, device) for Ninja-configured lights, sorted."""
    registries = request.app.state.registries
    for studio_id in sorted(registries):
        registry = registries[studio_id]
        for device_id in registry.device_ids:
            device = registry.get_device(device_id)
            if device.get("driver") != NINJA_DRIVER:
                continue
            addr = device.get("driverAddress") or {}
            if addr.get("channel") is None or addr.get("group") is None:
                continue
            yield studio_id, device


def _fixed_cct(spec):
    """colorTempK for fixed-temp profiles, else None. Ignores malformed
    values (a bad profile must never break a request)."""
    if not isinstance(spec, dict):
        return None
    if spec.get("fixedTemp") is not True:
        return None
    kelvin = spec.get("colorTempK")
    if isinstance(kelvin, bool) or not isinstance(kelvin, int):
        return None
    if not 2500 <= kelvin <= 8500 or kelvin % 100:
        return None
    return kelvin


def _resolve_target(body, request):
    """Resolve light-or-address to (channel, group, fixed_cct). No secrets
    involved: channel/group are plain user config, safe to echo."""
    if body.light is not None and (body.channel is not None or body.group is not None):
        raise HTTPException(status_code=422, detail="light or channel/group, not both")
    if body.light is not None:
        try:
            studio_id, device_id = body.light.split("/", 1)
        except ValueError:
            raise HTTPException(status_code=404, detail=f"unknown light: {body.light}") from None
        registry = request.app.state.registries.get(studio_id)
        if registry is None:
            raise HTTPException(status_code=404, detail=f"unknown light: {body.light}")
        try:
            device = registry.get_device(device_id)
        except UnknownDeviceError:
            raise HTTPException(status_code=404, detail=f"unknown light: {body.light}") from None
        addr = device.get("driverAddress") or {}
        if addr.get("channel") is None or addr.get("group") is None:
            raise HTTPException(
                status_code=404, detail=f"light has no Ninja address: {body.light}"
            )
        spec = registry.effective_device(device_id).get("spec", {})
        return addr["channel"], addr["group"], _fixed_cct(spec)
    if body.channel is not None and body.group is not None:
        return body.channel, body.group, None
    raise HTTPException(status_code=422, detail="light or channel+group required")


def _check_percent(pct, allow_bright=False):
    if isinstance(pct, bool) or not isinstance(pct, int):
        raise HTTPException(status_code=422, detail=f"power must be a whole number 0..100: {pct!r}")
    if not 0 <= pct <= 100:
        raise HTTPException(status_code=422, detail=f"power out of range 0..100%: {pct!r}")
    if pct > GENTLE_PERCENT_MAX and not allow_bright:
        raise HTTPException(
            status_code=422,
            detail=f"power {pct}% is above the gentle range ({GENTLE_PERCENT_MAX}%): "
            "pass allow_bright to confirm",
        )


def _check_repeats(repeats):
    if isinstance(repeats, bool) or not isinstance(repeats, int) or not 1 <= repeats <= 5:
        raise HTTPException(status_code=422, detail=f"repeats must be an int 1..5: {repeats!r}")


def shared_driver(channel, group, default_cct=None, mock=False, repeats=2):
    """Cached driver per address: look + counter survive across calls.

    Shared by the bench and the modes engine so a step walks from the
    last-sent look instead of restarting from defaults. Raises NinjaError
    on a bad address (callers translate to their own error shape).
    """
    address_byte(channel, group)  # validates now, fails fast
    key = (channel, str(group).upper(), bool(mock))
    driver = _DRIVERS.get(key)
    if driver is None:
        driver = NinjaBleDriver(
            f"ninja-ble-{channel}-{group}", channel, group,
            backend="mock" if mock else "auto",
        )
        _DRIVERS[key] = driver
    if default_cct is not None:
        driver.seed_default_cct(default_cct)
    driver.repeats = repeats
    return driver


def cached_driver(channel, group):
    """Production-cache driver for one address, or None (never created).

    Snapshots read through this so they never fabricate a pristine driver:
    no cache entry means nothing was ever sent to the address.
    """
    try:
        address_byte(channel, group)
    except NinjaError:
        return None
    return _DRIVERS.get((channel, str(group).upper(), False))


def _module(channel, group, mock, repeats, default_cct=None):
    """Bench module over the shared cached driver."""
    try:
        driver = shared_driver(channel, group, default_cct, mock, repeats)
    except NinjaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return NinjaModule("ninja", {"ble": driver})


@router.get("/api/testbench/ninja/lights")
def ninja_lights(request: Request):
    """Configured lights as [{id, name, studio, channel, group}]."""
    lights = []
    for studio_id, device in _ninja_devices(request):
        addr = device.get("driverAddress") or {}
        lights.append(
            {
                "id": f"{studio_id}/{device['id']}",
                "name": device.get("name", device["id"]),
                "studio": studio_id,
                "channel": addr["channel"],
                "group": addr["group"],
            }
        )
    return {"lights": lights}


@router.get("/api/testbench/ninja/scenes")
def ninja_scenes():
    """Scene index (0-9) + name + photo-risk table from the capture."""
    from ..drivers.ninja_ble import SCENES

    return {
        "scenes": [
            {"index": i, "name": name, "photo_risk": scene_risk(i)}
            for i, name in enumerate(SCENES)
        ]
    }


@router.post("/api/testbench/ninja/probe")
def ninja_probe(body: TargetBody, request: Request):
    """Backend availability. Never transmits."""
    channel, group, default_cct = _resolve_target(body, request)
    _check_repeats(2)
    module = _module(channel, group, body.mock, 2, default_cct)
    result = module.drivers["ble"].probe()
    return {"ok": result.ok, "state": result.state, "error": result.error}


@router.post("/api/testbench/ninja/state")
def ninja_state(body: TargetBody, request: Request):
    """Last-sent look + counter for this address. Never transmits."""
    channel, group, default_cct = _resolve_target(body, request)
    _check_repeats(2)
    module = _module(channel, group, body.mock, 2, default_cct)
    result = module.drivers["ble"].get_state()
    return {"ok": result.ok, "state": result.state, "error": result.error}


@router.post("/api/testbench/ninja/set")
def ninja_set(body: SetBody, request: Request):
    """Apply a look in power, brightness, cct, gm, preset, scene order.

    Explicit human trial (Bench). Brightness past 10% needs allow_bright;
    scenes need allow_intense. mock=true dry-runs (returns the payload
    that would send). Every send is one complete broadcast look.
    """
    channel, group, default_cct = _resolve_target(body, request)
    _check_repeats(body.repeats)
    if body.brightness is not None:
        _check_percent(body.brightness, body.allow_bright)
    if body.power is not None and body.power not in ("on", "off"):
        raise HTTPException(status_code=422, detail='power needs "on"|"off"')
    if body.scene is not None:
        try:
            resolved_scene = scene_index(body.scene)
        except NinjaError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    else:
        resolved_scene = None
    module = _module(channel, group, body.mock, body.repeats, default_cct)
    requested = []
    if body.power is not None:
        requested.append(("power", lambda: module.set_power(body.power == "on")))
    look = {}
    if body.brightness is not None:
        look["brightness"] = body.brightness
    if body.cct is not None:
        look["cct"] = body.cct
    if body.gm is not None:
        look["gm"] = body.gm
    if body.preset is not None:
        look["preset"] = body.preset
    if look:
        requested.append(("look", lambda: module.set_look(**look)))
    if resolved_scene is not None:
        requested.append(
            ("scene", lambda: module.set_scene(resolved_scene, allow_intense=body.allow_intense))
        )
    if not requested:
        raise HTTPException(status_code=422, detail="nothing to set")
    applied = []
    failed = False
    for name, run in requested:
        try:
            result = run()
        except (NinjaModuleError, NinjaError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        applied.append(
            {"command": name, "ok": result.ok, "state": result.state, "error": result.error}
        )
        if not result.ok:
            failed = True
            break
    state = {"applied": applied}
    if resolved_scene is not None:
        state["photo_risk"] = scene_risk(resolved_scene)
    return {"ok": not failed, "state": state, "error": None if not failed else "a command failed"}


@router.post("/api/testbench/ninja/raw")
def ninja_raw(body: RawBody, request: Request):
    """Send 27 verbatim payload bytes (hex). Bring-up only; prefer /set."""
    channel, group, default_cct = _resolve_target(body, request)
    _check_repeats(body.repeats)
    module = _module(channel, group, body.mock, body.repeats, default_cct)
    try:
        result = module.drivers["ble"].apply({"raw": body.payload})
    except NinjaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not result.ok and result.error and "raw must be" in result.error:
        raise HTTPException(status_code=422, detail=result.error)
    return {"ok": result.ok, "state": result.state, "error": result.error}
