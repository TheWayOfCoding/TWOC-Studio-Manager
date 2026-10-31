# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bench endpoints for screen/AV bring-up plus the kiosk state feed."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core.logging import get_logger
from ..drivers.screen import ScreenDriver, screen_state
from ..modules.screens import ScreensModule

log = get_logger("api")
router = APIRouter()


class PushBody(BaseModel):
    screen: str
    theme: str | None = None
    text: str | None = None


class AudioBody(BaseModel):
    screen: str
    action: str
    track: str | None = None


class StateBody(BaseModel):
    screen: str


def _module(screen):
    return ScreensModule("screens", {screen: ScreenDriver(f"screen-{screen}", screen)})


@router.post("/api/testbench/screen/push")
def screen_push(body: PushBody):
    """Push theme/text to one backdrop screen. Explicit human trial (Bench)."""
    log.debug("screen push")
    result = _module(body.screen).push(body.screen, body.theme, body.text)
    return {"ok": result.ok, "state": result.state, "error": result.error}


@router.post("/api/testbench/screen/audio")
def screen_audio(body: AudioBody):
    """Play/stop the audio bed on one screen's speakers."""
    result = _module(body.screen).audio(body.screen, body.action, body.track)
    return {"ok": result.ok, "state": result.state, "error": result.error}


@router.post("/api/testbench/screen/state")
def screen_bench_state(body: StateBody):
    """Read one screen's current scaffold state."""
    result = _module(body.screen)._driver(body.screen).get_state()
    return {"ok": result.ok, "state": result.state, "error": result.error}


@router.get("/api/screens/{screen_id}/state")
def screen_kiosk_state(screen_id: str):
    """Kiosk feed: the state /screen/{id} pages poll. Never 404s on new ids."""
    if not screen_id:
        raise HTTPException(status_code=422, detail="screen id required")
    return {"screen": screen_id, **screen_state(screen_id)}
