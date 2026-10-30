# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bench endpoints for GVM BLE bring-up (1500D panels)."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core.logging import get_logger
from ..drivers.gvm_ble import GvmBleDriver, scan_devices
from ..modules.gvm import GvmModule, GvmModuleError, scene_risk

router = APIRouter()
log = get_logger("api")


class ScanBody(BaseModel):
    timeout: int = 8


class AddrBody(BaseModel):
    address: str
    timeout: int = 15


class SetBody(AddrBody):
    power: str | None = None
    brightness: int | None = None
    cct: int | None = None
    scene: int | None = None
    page: str | int | None = None
    allow_intense: bool = False


def _module(body):
    return GvmModule("gvm", {"ble": GvmBleDriver("gvm-ble", body.address, body.timeout)})


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
def gvm_probe(body: AddrBody):
    """Connect and dump the panel's GATT table + resolved control UUID."""
    result = GvmBleDriver("gvm-ble", body.address, body.timeout).probe()
    return {"ok": result.ok, "state": result.state, "error": result.error}


@router.post("/api/testbench/gvm/state")
def gvm_state(body: AddrBody):
    """Reachability check. The panel reports nothing; open loop."""
    result = GvmBleDriver("gvm-ble", body.address, body.timeout).get_state()
    return {"ok": result.ok, "state": result.state, "error": result.error}


@router.post("/api/testbench/gvm/set")
def gvm_set(body: SetBody):
    """Apply CCT/scene commands in page, power, brightness, cct, scene order.

    Explicit human trial (Bench), no policy gate — except scenes, which need
    allow_intense until verified gentle (D-PHOTO).
    """
    module = _module(body)
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
            {"command": name, "ok": result.ok, "state": result.state, "error": result.error}
        )
        if not result.ok:
            failed = True
            break
    state = {"applied": applied}
    if body.scene is not None:
        state["photo_risk"] = scene_risk(body.scene)
    return {"ok": not failed, "state": state, "error": None if not failed else "a command failed"}
