# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bench endpoints for GVM BLE bring-up (1500D panels)."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..core.logging import get_logger
from ..drivers.gvm_ble import GvmBleDriver, scan_devices
from ..modules.gvm import GvmModule, GvmModuleError, scene_risk
from ..registry import UnknownDeviceError

router = APIRouter()
log = get_logger("api")

GVM_DRIVER = "gvm"


class ScanBody(BaseModel):
    timeout: int = 8


class AddrBody(BaseModel):
    address: str | None = None
    panel: str | None = None
    timeout: int = 15


class SetBody(AddrBody):
    power: str | None = None
    brightness: int | None = None
    cct: int | None = None
    scene: int | None = None
    page: str | int | None = None
    allow_intense: bool = False


def _gvm_devices(request):
    """Yield (studio_id, device) for BLE-configured panels, sorted."""
    registries = request.app.state.registries
    for studio_id in sorted(registries):
        registry = registries[studio_id]
        for device_id in registry.device_ids:
            device = registry.get_device(device_id)
            if device.get("driver") != GVM_DRIVER:
                continue
            if not (device.get("driverAddress") or {}).get("ble"):
                continue
            yield studio_id, device


def _resolve_target(body, request):
    """Resolve panel-or-address to (ble_address, label_or_None for scrubbing)."""
    if body.panel and body.address:
        raise HTTPException(status_code=422, detail="panel or address, not both")
    if body.panel is not None:
        try:
            studio_id, device_id = body.panel.split("/", 1)
        except ValueError:
            raise HTTPException(status_code=404, detail=f"unknown panel: {body.panel}") from None
        registry = request.app.state.registries.get(studio_id)
        if registry is None:
            raise HTTPException(status_code=404, detail=f"unknown panel: {body.panel}")
        try:
            device = registry.get_device(device_id)
        except UnknownDeviceError:
            raise HTTPException(status_code=404, detail=f"unknown panel: {body.panel}") from None
        address = (device.get("driverAddress") or {}).get("ble")
        if not address:
            raise HTTPException(status_code=404, detail=f"panel has no BLE address: {body.panel}")
        return address, device.get("name", device_id)
    if body.address is not None:
        return body.address, None
    raise HTTPException(status_code=422, detail="panel or address required")


def _scrub(node, address, label):
    """Stream-safe output: swap address echoes for the panel label."""
    if label is None:
        return node
    if isinstance(node, dict):
        return {
            ("panel" if key == "address" else key): (
                label if key == "address" else _scrub(value, address, label)
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_scrub(value, address, label) for value in node]
    if isinstance(node, str) and address in node:
        return node.replace(address, label)
    return node


def _module(address, timeout):
    return GvmModule("gvm", {"ble": GvmBleDriver("gvm-ble", address, timeout)})


@router.get("/api/testbench/gvm/panels")
def gvm_panels(request: Request):
    """Configured panels as [{id, name, studio}]. Never exposes addresses."""
    return {
        "panels": [
            {
                "id": f"{studio_id}/{device['id']}",
                "name": device.get("name", device["id"]),
                "studio": studio_id,
            }
            for studio_id, device in _gvm_devices(request)
        ]
    }


@router.post("/api/testbench/gvm/scan")
def gvm_scan(body: ScanBody):
    """BLE scan. Returns [{address, name, rssi}] for nearby devices."""
    log.debug("gvm scan")
    try:
        devices = scan_devices(max(body.timeout, 1))
    except Exception as exc:  # noqa: BLE001 - Bench reports, never 500s on hardware
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"devices": devices}


@router.post("/api/testbench/gvm/probe")
def gvm_probe(body: AddrBody, request: Request):
    """Connect and dump the panel's GATT table + resolved control UUID."""
    address, label = _resolve_target(body, request)
    result = GvmBleDriver("gvm-ble", address, body.timeout).probe()
    return {
        "ok": result.ok,
        "state": _scrub(result.state, address, label),
        "error": _scrub(result.error, address, label),
    }


@router.post("/api/testbench/gvm/state")
def gvm_state(body: AddrBody, request: Request):
    """Reachability check. The panel reports nothing; open loop."""
    address, label = _resolve_target(body, request)
    result = GvmBleDriver("gvm-ble", address, body.timeout).get_state()
    return {
        "ok": result.ok,
        "state": _scrub(result.state, address, label),
        "error": _scrub(result.error, address, label),
    }


@router.post("/api/testbench/gvm/set")
def gvm_set(body: SetBody, request: Request):
    """Apply CCT/scene commands in page, power, brightness, cct, scene order.

    Explicit human trial (Bench), no policy gate — except scenes, which need
    allow_intense until verified gentle (D-PHOTO).
    """
    address, label = _resolve_target(body, request)
    module = _module(address, body.timeout)
    requested = []
    if body.page is not None:
        requested.append(("page", lambda: module.announce_page(body.page)))
    if body.power is not None:
        if body.power not in ("on", "off"):
            raise HTTPException(status_code=422, detail='power needs "on"|"off"')
        requested.append(("power", lambda: module.set_power(body.power == "on")))
    if body.brightness is not None:
        requested.append(("brightness", lambda: module.set_brightness(body.brightness)))
    if body.cct is not None:
        requested.append(("cct", lambda: module.set_cct(body.cct)))
    if body.scene is not None:
        requested.append(
            ("scene", lambda: module.set_scene(body.scene, allow_intense=body.allow_intense))
        )
    if not requested:
        raise HTTPException(status_code=422, detail="nothing to set")
    applied = []
    failed = False
    for name, run in requested:
        try:
            result = run()
        except GvmModuleError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        applied.append(
            {
                "command": name,
                "ok": result.ok,
                "state": _scrub(result.state, address, label),
                "error": _scrub(result.error, address, label),
            }
        )
        if not result.ok:
            failed = True
            break
    state = {"applied": applied}
    if body.scene is not None:
        state["photo_risk"] = scene_risk(body.scene)
    return {"ok": not failed, "state": state, "error": None if not failed else "a command failed"}
