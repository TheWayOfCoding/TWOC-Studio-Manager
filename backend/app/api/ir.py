# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bench endpoints for IR capture/replay (USB-serial YS-IRTM)."""

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core.logging import get_logger
from ..drivers.ir_usb_serial import IrUsbSerialDriver
from ..modules.ir import IrModule, IrModuleError

router = APIRouter()
log = get_logger("api")

DEFAULT_STUDIO = "home-studio"
DEFAULT_DEVICE = "bytech-strip"


def _config_root():
    default = Path(__file__).resolve().parents[3] / "config"
    return Path(os.environ.get("STUDIO_CONFIG", str(default)))


def _port(body):
    return body.port or os.environ.get("IR_PORT", "/dev/ttyUSB0")


def _module(body):
    store = (
        _config_root()
        / "studios.local"
        / (body.studio or DEFAULT_STUDIO)
        / "ir-codes"
        / f"{body.device or DEFAULT_DEVICE}.json"
    )
    return IrModule("ir", {"usb": IrUsbSerialDriver("ir-usb", _port(body))}, store)


class IrPortBody(BaseModel):
    port: str | None = None


class IrListenBody(IrPortBody):
    seconds: float = 5.0


class IrCaptureBody(IrListenBody):
    label: str
    studio: str | None = None
    device: str | None = None


class IrSendBody(IrPortBody):
    label: str | None = None
    code: list[int] | None = None
    times: int = 1
    gap_ms: int = 300
    studio: str | None = None
    device: str | None = None


class IrMarkBody(BaseModel):
    label: str
    works: bool | None = None
    notes: str | None = None
    studio: str | None = None
    device: str | None = None


@router.post("/api/testbench/ir/state")
def ir_state(body: IrPortBody):
    """Check the IR serial port. Opens and closes it, emits nothing."""
    result = IrUsbSerialDriver("ir-usb", _port(body)).get_state()
    return {"ok": result.ok, "state": result.state, "error": result.error}


@router.post("/api/testbench/ir/listen")
def ir_listen(body: IrListenBody):
    """Listen for remote presses. Returns the codes heard (nothing saved)."""
    return IrUsbSerialDriver("ir-usb", _port(body)).listen(body.seconds)


@router.post("/api/testbench/ir/capture")
def ir_capture(body: IrCaptureBody):
    """Capture one button press and save it under a label. Press only once."""
    log.debug("ir capture", extra={"fields": {"label": body.label}})
    try:
        return _module(body).capture(body.label, body.seconds)
    except IrModuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/testbench/ir/codes")
def ir_codes(studio: str = DEFAULT_STUDIO, device: str = DEFAULT_DEVICE):
    """List captured label -> code mappings."""
    store = _config_root() / "studios.local" / studio / "ir-codes" / f"{device}.json"
    return IrModule("ir", {"usb": IrUsbSerialDriver("ir-usb")}, store).codes()


@router.post("/api/testbench/ir/send")
def ir_send(body: IrSendBody):
    """Replay a saved label or a raw 3-byte code, optionally repeated."""
    module = _module(body)
    times = min(max(body.times, 1), 100)
    gap_s = max(body.gap_ms, 0) / 1000.0
    try:
        if body.label:
            result = module.send_label(body.label, times, gap_s)
        elif body.code is not None:
            result = module.send_code(body.code, times, gap_s)
        else:
            raise HTTPException(status_code=422, detail="need label or code")
    except IrModuleError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": result.ok, "state": result.state, "error": result.error}


@router.post("/api/testbench/ir/mark")
def ir_mark(body: IrMarkBody):
    """Record replay effectiveness (and notes) for a captured label."""
    store = (
        _config_root()
        / "studios.local"
        / (body.studio or DEFAULT_STUDIO)
        / "ir-codes"
        / f"{body.device or DEFAULT_DEVICE}.json"
    )
    try:
        return {"label": body.label, **IrModule("ir", {"usb": IrUsbSerialDriver("ir-usb")}, store).mark(
            body.label, body.works, body.notes
        )}
    except IrModuleError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
