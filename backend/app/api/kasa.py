# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bench endpoints for real Kasa bring-up. Credentials pass through, never stored.

Two surfaces share this file:
- Bench (host-addressed): discover/state/set for bring-up by LAN host.
- Official plugs (plug-id-addressed): the plug-map's named plugs
  ("Studio Plug N") with zone/group, for the bench Kasa section and the
  control page. Hosts resolve by alias (cached discovery), never echoed
  beyond the plug's live state.
"""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..core.logging import get_logger

try:
    from ..drivers.kasa import KasaDriver, discover_plugs, prime_plugs, resolve_host

    KASA_AVAILABLE = True
except ImportError:
    KASA_AVAILABLE = False

log = get_logger("api")
router = APIRouter()


class DiscoverBody(BaseModel):
    username: str | None = None
    password: str | None = None
    discovery_timeout: int = 8


class PrimeBody(BaseModel):
    username: str | None = None
    password: str | None = None
    force: bool = False


class PlugBody(BaseModel):
    host: str
    username: str | None = None
    password: str | None = None


class PlugSetBody(PlugBody):
    on: bool


class OfficialSetBody(BaseModel):
    on: bool
    username: str | None = None
    password: str | None = None


def _need_kasa():
    if not KASA_AVAILABLE:
        raise HTTPException(status_code=503, detail="kasa backend unavailable (python-kasa not installed)")


def _plug_map(config_root):
    path = Path(config_root) / "studios.local" / "plug-map.json"
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))["plugs"]
    except (OSError, ValueError, KeyError):
        return []
    return [e for e in entries if isinstance(e, dict) and e.get("plug")]


def _official_entry(request, plug_id):
    for entry in _plug_map(request.app.state.config_root):
        if entry["plug"] == plug_id:
            return entry
    raise HTTPException(status_code=404, detail=f"unknown plug: {plug_id}")


def _official_driver(request, plug_id, username=None, password=None):
    entry = _official_entry(request, plug_id)
    user = username if username is not None else entry.get("username")
    pw = password if password is not None else entry.get("password")
    host = entry.get("host")
    if entry.get("alias"):
        host = resolve_host(entry["alias"], user, pw, fallback=host)
    if not host:
        raise HTTPException(status_code=422, detail=f"plug {plug_id} has no reachable host (discover first)")
    return entry, KasaDriver(plug_id, host, user, pw)


@router.post("/api/testbench/kasa/discover")
def kasa_discover(body: DiscoverBody):
    """Broadcast-discover plugs. Returns hosts/aliases/models, never secrets."""
    _need_kasa()
    log.debug("kasa discover")
    try:
        plugs = discover_plugs(body.username, body.password, body.discovery_timeout)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"plugs": plugs}


@router.post("/api/testbench/kasa/prime")
def kasa_prime(body: PrimeBody):
    """Warm the alias cache with one broadcast (called on page load).

    Debounced server-side: repeat loads inside the window reuse the last
    discovery, so priming is cheap and the first real switch is fast.
    """
    _need_kasa()
    try:
        plugs = prime_plugs(body.username, body.password, force=body.force)
    except Exception as exc:  # noqa: BLE001 - priming failure must not break the page
        return {"ok": False, "plugs": [], "error": str(exc)}
    return {"ok": True, "plugs": plugs}


@router.get("/api/kasa/plugs")
def kasa_official_plugs(request: Request):
    """Official named plugs from the plug-map, in configured order.

    Static map only (no hardware calls): alias, model, zone, power group.
    Live on/off state comes from the per-plug state endpoint.
    """
    from ..config.ordering import load_ordering, ordered_ids

    entries = _plug_map(request.app.state.config_root)
    ordering = load_ordering(request.app.state.config_root, _default_studio(request))
    ids = [e["plug"] for e in entries]
    labels = {e["plug"]: e.get("alias") or e["plug"] for e in entries}
    by_id = {e["plug"]: e for e in entries}
    plugs = []
    for plug_id in ordered_ids(ordering.get("plugs"), ids, labels):
        e = by_id[plug_id]
        plugs.append({
            "plug": plug_id,
            "alias": e.get("alias"),
            "model": e.get("model"),
            "zone": e.get("zone"),
            "powerGroup": e.get("powerGroup"),
        })
    return {"plugs": plugs}


def _default_studio(request):
    studios = getattr(request.app.state, "studio_list", []) or []
    for entry in studios:
        if entry.get("id") == "home-studio":
            return "home-studio"
    return studios[0]["id"] if studios else "home-studio"


@router.get("/api/kasa/plugs/{plug_id}/state")
def kasa_official_state(plug_id: str, request: Request):
    """Live state of one official plug (alias-resolved host)."""
    _need_kasa()
    _entry, driver = _official_driver(request, plug_id)
    result = driver.get_state()
    return {"plug": plug_id, "ok": result.ok, "state": result.state, "error": result.error}


@router.post("/api/kasa/plugs/{plug_id}/set")
def kasa_official_set(plug_id: str, body: OfficialSetBody, request: Request):
    """Switch one official plug by id. Explicit human action, no policy gate."""
    _need_kasa()
    _entry, driver = _official_driver(request, plug_id, body.username, body.password)
    result = driver.apply({"on": body.on})
    return {"plug": plug_id, "ok": result.ok, "state": result.state, "error": result.error}


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
