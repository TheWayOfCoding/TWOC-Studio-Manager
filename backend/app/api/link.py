# SPDX-License-Identifier: AGPL-3.0-or-later
"""PC-link endpoints: audio tones + Wake-on-LAN between hub and main PC.

Primary path is hub TX -> main RX (wake the PC, play framed tone commands
down the wire). A second cable gives main TX -> hub RX: the hub captures a
short window and decodes reverse acks (ack/obs_ready/obs_fail). Effects
still confirm through normal channels too (e.g. OBS on its websocket).

Local config lives in hub-local studios.local/link.json (git-ignored):
{"wol_mac": "AA:BB:CC:DD:EE:FF", "audio_out": "Line Out", "audio_in": "Mic In"}.
MACs are never echoed in responses.
"""

import json
import math
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..core.logging import get_logger
from ..drivers.link_audio import (
    LinkError,
    device_state,
    input_state,
    listen_all,
    listen_once,
    play_samples,
    send_tones,
)
from ..drivers.wol import WolError, wake
from ..linkproto import COMMANDS, frame_for

log = get_logger("api")
router = APIRouter()


class SendBody(BaseModel):
    command: str
    device: str | int | None = None


class TestBody(BaseModel):
    seconds: float = 1.0
    freq: float = 1000.0
    device: str | int | None = None


class ListenBody(BaseModel):
    seconds: float = 2.0
    device: str | int | None = None


class SendWaitBody(BaseModel):
    command: str
    wait_s: float | None = None
    device: str | int | None = None
    listen_device: str | int | None = None


class LinkConfigBody(BaseModel):
    audio_out: str | int | None = None
    audio_in: str | int | None = None


# Hub TX vocabulary for send-and-wait, with the reverse acks that confirm
# each: ping wants the listener's auto-ack, obs/obs_bench want the OBS
# watcher's verdict (obs_ready/obs_fail) — a bare ack means "received,
# outcome unknown". The two launch tones address different seats on the
# same wire (show vs test bench); each seat allows only its own.
EXPECTED_ACKS = {"ping": ["ack"], "obs": ["obs_ready", "obs_fail"], "obs_bench": ["obs_ready", "obs_fail"], "shutdown": ["ack"]}
DEFAULT_WAIT_S = {"ping": 8.0, "obs": 60.0, "obs_bench": 60.0, "shutdown": 8.0}


def _link_config(config_root):
    path = Path(config_root) / "studios.local" / "link.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _check_device_want(want, devices, kind):
    """Validate a TX/RX assignment against the live direction list.

    Strings are substring matches (stable across index shifts); ints are
    exact indexes. An empty live list accepts blindly (headless hosts can
    see nothing) and reports unverified instead of refusing.
    """
    if want is None:
        return None, True
    if isinstance(want, bool):
        raise LinkError(f"audio {kind} must be a device name or index: {want!r}")
    if not devices:
        return want, False
    if isinstance(want, int):
        if any(item["index"] == want for item in devices):
            return want, True
        raise LinkError(f"unknown audio {kind} index (see /api/link/{'devices' if kind == 'out' else 'inputs'}): {want!r}")
    lowered = str(want).lower()
    matches = [item for item in devices if lowered in str(item.get("name") or "").lower()]
    if not matches:
        raise LinkError(f"unknown audio {kind} (see /api/link/{'devices' if kind == 'out' else 'inputs'}): {want!r}")
    return want, True


def _save_link_config(config_root, body):
    """Validate sent TX/RX assignments and persist them to link.json.

    Only keys present in the request change (explicit null clears); the WoL
    MAC and anything else in the file are preserved.
    """
    from ..drivers.link_audio import input_devices, output_devices

    path = Path(config_root) / "studios.local" / "link.json"
    data = _link_config(config_root)
    verified = {}
    if "audio_out" in body.model_fields_set:
        checked, ok = _check_device_want(body.audio_out, output_devices(), "out")
        data["audio_out"] = checked
        verified["audio_out"] = ok
    if "audio_in" in body.model_fields_set:
        checked, ok = _check_device_want(body.audio_in, input_devices(), "in")
        data["audio_in"] = checked
        verified["audio_in"] = ok
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp_path.replace(path)
    return {"audio_out": data.get("audio_out"), "audio_in": data.get("audio_in"), "verified": verified}


@router.get("/api/link/devices")
def link_devices(request: Request):
    """Hub audio outputs (pick one for the wire). Empty under backendless hosts."""
    result = device_state()
    return {"ok": result.ok, "state": result.state, "error": result.error}


@router.get("/api/link/inputs")
def link_inputs(request: Request):
    """Hub audio inputs (pick one for the reverse ack wire)."""
    result = input_state()
    return {"ok": result.ok, "state": result.state, "error": result.error}


@router.get("/api/link/config")
def link_config_get(request: Request):
    """Saved TX/RX port assignments. The WoL MAC is never echoed."""
    cfg = _link_config(request.app.state.config_root)
    return {
        "audio_out": cfg.get("audio_out"),
        "audio_in": cfg.get("audio_in"),
        "wol_configured": bool(cfg.get("wol_mac")),
    }


@router.post("/api/link/config")
def link_config_set(body: LinkConfigBody, request: Request):
    """Assign the TX (playback) and RX (capture) ports for the wires.

    Values are substring matches against the live direction lists (ints
    are exact indexes); explicit null clears. Anything else in link.json
    (notably wol_mac) is preserved.
    """
    log.info("link config", extra={"fields": {"fields": sorted(body.model_fields_set)}})
    try:
        saved = _save_link_config(request.app.state.config_root, body)
    except LinkError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "state": saved}


@router.post("/api/link/listen")
def link_listen(body: ListenBody, request: Request):
    """Capture a short window on a hub input and decode one reverse ack.

    Returns command None when the window holds silence, noise, or an
    unknown-but-valid code — an honest empty, not an error.
    """
    if not 0.5 <= body.seconds <= 6:
        raise HTTPException(status_code=422, detail="seconds must be 0.5..6")
    cfg = _link_config(request.app.state.config_root)
    try:
        heard = listen_once(body.seconds, device=body.device or cfg.get("audio_in"))
    except LinkError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    log.info("link listen", extra={"fields": {"command": heard.get("command")}})
    return {"ok": True, "state": heard}


@router.post("/api/link/send-and-wait")
def link_send_and_wait(body: SendWaitBody, request: Request):
    """Send one command, then listen one window for its reverse ack.

    One call for the scripted flow (ping→ack loop test, obs→obs_ready
    launch confirm). The wait is a single capture so straddled bursts
    can't be missed; confirmed is True only when an expected ack lands.
    """
    if body.command not in EXPECTED_ACKS:
        raise HTTPException(
            status_code=422,
            detail=f"send-and-wait is for hub TX commands {sorted(EXPECTED_ACKS)} (got {body.command!r})",
        )
    wait = DEFAULT_WAIT_S[body.command] if body.wait_s is None else body.wait_s
    if not 2 <= wait <= 90:
        raise HTTPException(status_code=422, detail="wait_s must be 2..90")
    cfg = _link_config(request.app.state.config_root)
    try:
        sent = send_tones(frame_for(body.command), device=body.device or cfg.get("audio_out"))
    except LinkError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        heard = listen_all(wait, device=body.listen_device or cfg.get("audio_in"))
    except LinkError as exc:
        return {
            "ok": True,
            "state": {
                "command": body.command,
                "sent": sent,
                "confirmed": False,
                "heard": None,
                "others": [],
                "waited_s": 0.0,
                "listen_error": str(exc),
            },
        }
    expected = EXPECTED_ACKS[body.command]
    names = heard["commands"]
    first = next((name for name in names if name in expected), None)
    others = list(names)
    if first is not None:
        others.remove(first)
    log.info("link send-and-wait", extra={"fields": {"command": body.command, "heard": first}})
    return {
        "ok": True,
        "state": {
            "command": body.command,
            "sent": sent,
            "expected": expected,
            "heard": first,
            "others": others,
            "waited_s": heard["seconds"],
            "confirmed": first is not None,
        },
    }


@router.post("/api/link/test")
def link_test(body: TestBody, request: Request):
    """Play a pure calibration tone (level-setting the wire path)."""
    if not 0.1 <= body.seconds <= 10:
        raise HTTPException(status_code=422, detail="seconds must be 0.1..10")
    if not 20 <= body.freq <= 20000:
        raise HTTPException(status_code=422, detail="freq must be 20..20000 Hz")
    rate = 44100
    n = int(rate * body.seconds)
    samples = [0.4 * math.sin(2 * math.pi * body.freq * i / rate) for i in range(n)]
    cfg = _link_config(request.app.state.config_root)
    try:
        seconds = play_samples(samples, rate, body.device or cfg.get("audio_out"))
    except LinkError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "state": {"seconds": round(seconds, 2), "freq": body.freq}}


@router.post("/api/link/send")
def link_send(body: SendBody, request: Request):
    """Play one framed command down the wire (fire-and-forget)."""
    if body.command not in COMMANDS:
        raise HTTPException(
            status_code=422, detail=f"unknown link command {body.command!r} (want one of {sorted(COMMANDS)})"
        )
    cfg = _link_config(request.app.state.config_root)
    try:
        state = send_tones(frame_for(body.command), device=body.device or cfg.get("audio_out"))
    except LinkError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    log.info("link send", extra={"fields": {"command": body.command}})
    return {"ok": True, "state": {"command": body.command, **state}}


@router.post("/api/link/wol")
def link_wol(request: Request):
    """Wake the main PC (magic packet; needs BIOS + NIC wake enabled there)."""
    mac = _link_config(request.app.state.config_root).get("wol_mac")
    if not mac:
        raise HTTPException(
            status_code=422, detail="no wol_mac in studios.local/link.json (ipconfig /all on the target)"
        )
    try:
        packets = wake(mac)
    except WolError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "state": {"packets": packets}}
