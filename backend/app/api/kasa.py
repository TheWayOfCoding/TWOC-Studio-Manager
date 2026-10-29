# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bench endpoints for real Kasa bring-up. Credentials pass through, never stored."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core.logging import get_logger

try:
    from ..drivers.kasa import KasaDriver, discover_plugs

    KASA_AVAILABLE = True
except ImportError:
    KASA_AVAILABLE = False

log = get_logger("api")
router = APIRouter()


class DiscoverBody(BaseModel):
    username: str | None = None
    password: str | None = None
    discovery_timeout: int = 8


class PlugBody(BaseModel):
    host: str
    username: str | None = None
    password: str | None = None


class PlugSetBody(PlugBody):
    on: bool


def _need_kasa():
    if not KASA_AVAILABLE:
        raise HTTPException(status_code=503, detail="kasa backend unavailable (python-kasa not installed)")


@router.post("/api/testbench/kasa/discover")
def kasa_discover(body: DiscoverBody):
    """Broadcast-discover plugs. Returns hosts/aliases/models, never secrets."""
    _need_kasa()
    log.debug("kasa discover")
    try:
        plugs = discover_plugs(body.username, body.password, body.discovery_timeout)
    except Exception as exc:  # noqa: BLE001 - Bench reports, never 500s on hardware
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"plugs": plugs}


@router.post("/api/testbench/kasa/state")
def kasa_state(body: PlugBody):
    """Read one plug's state. Explicit human trial (Bench), no policy gate."""
    _need_kasa()
    result = KasaDriver("bench", body.host, body.username, body.password).get_state()
    return {"ok": result.ok, "state": result.state, "error": result.error}


@router.post("/api/testbench/kasa/set")
def kasa_set(body: PlugSetBody):
    """Switch one plug. Explicit human trial (Bench), no policy gate."""
    _need_kasa()
    result = KasaDriver("bench", body.host, body.username, body.password).apply({"on": body.on})
    return {"ok": result.ok, "state": result.state, "error": result.error}
