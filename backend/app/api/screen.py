# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bench endpoints for screen/AV bring-up plus the kiosk state feed.

Bed volume is 0..100 with no loudness cap: the synth runs quiet even at
100% (user-verified on the hub speakers), so no consent flag is needed.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..core.logging import get_logger
from ..drivers.screen import DEFAULT_VOLUME, ScreenDriver, screen_state
from ..modules.screens import ScreensModule

log = get_logger("api")
router = APIRouter()


class PushBody(BaseModel):
    screen: str
    theme: str | None = None
    text: str | None = None
    text_ttl_s: int | None = None
    animation: str | None = None


class AudioBody(BaseModel):
    screen: str
    action: str
    track: str | None = None
    volume: int | None = None


class StateBody(BaseModel):
    screen: str


def _module(screen):
    return ScreensModule("screens", {screen: ScreenDriver(f"screen-{screen}", screen)})


def _check_volume(volume):
    if volume is None:
        return DEFAULT_VOLUME
    if isinstance(volume, bool) or not isinstance(volume, int):
        raise HTTPException(status_code=422, detail=f"volume must be a whole number 0..100: {volume!r}")
    if not 0 <= volume <= 100:
        raise HTTPException(status_code=422, detail=f"volume out of range 0..100: {volume}")
    return volume


@router.post("/api/testbench/screen/push")
def screen_push(body: PushBody):
    """Push theme/text to one backdrop screen. Explicit human trial (Bench)."""
    log.debug("screen push")
    result = _module(body.screen).push(
        body.screen, body.theme, body.text, body.text_ttl_s, body.animation
    )
    return {"ok": result.ok, "state": result.state, "error": result.error}


@router.post("/api/testbench/screen/audio")
def screen_audio(body: AudioBody):
    """Play/stop the audio bed on one screen's speakers (volume 0..100, uncapped)."""
    volume = _check_volume(body.volume) if body.action == "play" else None
    result = _module(body.screen).audio(body.screen, body.action, body.track, volume)
    return {"ok": result.ok, "state": result.state, "error": result.error}


@router.post("/api/testbench/screen/state")
def screen_bench_state(body: StateBody):
    """Read one screen's current scaffold state."""
    result = _module(body.screen)._driver(body.screen).get_state()
    return {"ok": result.ok, "state": result.state, "error": result.error}


@router.get("/api/screens/{screen_id}/state")
def screen_kiosk_state(screen_id: str, request: Request):
    """Kiosk feed: the state /screen/{id} pages poll. Never 404s on new ids.

    Carries the hub boot id so kiosks reload themselves after a deploy.
    """
    if not screen_id:
        raise HTTPException(status_code=422, detail="screen id required")
    return {"screen": screen_id, **screen_state(screen_id), "hub": request.app.state.boot_id}
