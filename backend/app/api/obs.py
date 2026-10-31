# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bench endpoints for OBS bring-up. Passwords pass through, never stored."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core.logging import get_logger
from ..core.safety import SafetyViolation
from ..drivers.obs_ws import DEFAULT_PORT, ObsWsDriver
from ..modules.obsshow import ObsModuleError, ObsShowModule

log = get_logger("api")
router = APIRouter()


class ObsBody(BaseModel):
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    password: str | None = None


class SwitchBody(ObsBody):
    scene: str


class StreamBody(ObsBody):
    action: str
    confirmed: bool = False


def _module(body):
    return ObsShowModule("obs", {"ws": ObsWsDriver("obs-bench", body.host, body.port, body.password)})


@router.post("/api/testbench/obs/state")
def obs_state(body: ObsBody):
    """Connection state. Explicit human trial (Bench)."""
    log.debug("obs state")
    result = _module(body).state_info()
    return {"ok": result.ok, "state": result.state, "error": result.error}


@router.post("/api/testbench/obs/scenes")
def obs_scenes(body: ObsBody):
    """List scenes plus the current program scene."""
    try:
        result = _module(body).list_scenes()
    except Exception as exc:  # noqa: BLE001 - Bench reports, never 500s on hardware
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": result.ok, "state": result.state, "error": result.error}


@router.post("/api/testbench/obs/switch")
def obs_switch(body: SwitchBody):
    """Cut the program feed to a named scene."""
    try:
        result = _module(body).switch_scene(body.scene)
    except ObsModuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - Bench reports, never 500s on hardware
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": result.ok, "state": result.state, "error": result.error}


@router.post("/api/testbench/obs/stream")
def obs_stream(body: StreamBody):
    """Start/stop streaming. Refuses without explicit confirmation."""
    try:
        result = _module(body).set_streaming(body.action, body.confirmed)
    except (ObsModuleError, SafetyViolation) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - Bench reports, never 500s on hardware
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": result.ok, "state": result.state, "error": result.error}
