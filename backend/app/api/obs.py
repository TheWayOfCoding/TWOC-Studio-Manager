# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bench + studio endpoints for OBS. Passwords pass through, never stored."""

import json
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..core.logging import get_logger
from ..core.safety import SafetyViolation
from ..drivers.obs_ws import DEFAULT_PORT, ObsWsDriver
from ..modules.obsshow import ObsModuleError, ObsShowModule
from ..modules.pcboot import DEFAULT_SCENE, RUNNING, new_job, run_boot
from ..registry import UnknownModeError

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


class StudioSwitchBody(BaseModel):
    scene: str


class StudioStreamBody(BaseModel):
    action: str
    confirmed: bool = False


def _registry_or_404(request, studio_id):
    try:
        return request.app.state.registries[studio_id]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown studio: {studio_id}") from exc


def _studio_obs(request, studio_id):
    """Studio OBS module + safe scene, or None when unconfigured.

    Reads hub-local studios.local/obs.json (same file the modes applier
    uses). The safe scene is the studio's break-mode scene when it names
    one; without it a failed switch just fails.
    """
    registry = request.app.state.registries.get(studio_id)
    if registry is None:
        return None
    path = Path(request.app.state.config_root) / "studios.local" / "obs.json"
    try:
        entries = json.loads(path.read_text(encoding="utf-8")).get("studios") or {}
    except (OSError, ValueError):
        return None
    cfg = entries.get(studio_id) or {}
    if not isinstance(cfg, dict) or not cfg.get("host"):
        return None
    try:
        brb = registry.get_mode("brb")
    except UnknownModeError:
        brb = None
    safe = brb.get("obsScene") if isinstance(brb, dict) else None
    driver = ObsWsDriver(
        "obs-studio", cfg["host"], cfg.get("port", DEFAULT_PORT), cfg.get("password")
    )
    return ObsShowModule("obs", {"ws": driver}), safe


@router.get("/api/studios/{studio_id}/obs/status")
def obs_studio_status(studio_id: str, request: Request):
    """Studio OBS at a glance: configured, connected, current scene, streaming."""
    _registry_or_404(request, studio_id)
    found = _studio_obs(request, studio_id)
    if found is None:
        return {
            "configured": False,
            "connected": False,
            "current": None,
            "streaming": None,
            "scenes": [],
            "collection": None,
            "collections": [],
        }
    module, _safe = found
    try:
        scenes = module.list_scenes()
        # One probe when OBS is down (each refused probe burns ~10s on a
        # filtered port); the follow-ups only run when the socket answers.
        stream = module.stream_status() if scenes.ok else None
        collections = module.list_collections() if scenes.ok else None
    except Exception as exc:  # noqa: BLE001 - report, never 500 on hardware
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    names = []
    current = None
    if scenes.ok:
        for entry in (scenes.state or {}).get("scenes") or []:
            if isinstance(entry, dict) and entry.get("sceneName"):
                names.append(entry["sceneName"])
        current = (scenes.state or {}).get("currentProgramSceneName")
    streaming = (stream.state or {}).get("outputActive") if stream is not None and stream.ok else None
    coll_names = []
    coll_current = None
    if collections is not None and collections.ok:
        coll_names = list((collections.state or {}).get("sceneCollections") or [])
        coll_current = (collections.state or {}).get("currentSceneCollectionName")
    return {
        "configured": True,
        "connected": bool(scenes.ok and stream is not None and stream.ok),
        "current": current,
        "streaming": streaming,
        "scenes": names,
        "collection": coll_current,
        "collections": coll_names,
    }


@router.post("/api/studios/{studio_id}/obs/switch")
def obs_studio_switch(studio_id: str, body: StudioSwitchBody, request: Request):
    """Cut the studio program feed to a scene (safe-scene fallback armed)."""
    _registry_or_404(request, studio_id)
    found = _studio_obs(request, studio_id)
    if found is None:
        raise HTTPException(
            status_code=422,
            detail=f"obs not configured for studio {studio_id} (studios.local/obs.json)",
        )
    module, safe = found
    try:
        result = module.switch_scene(body.scene, safe)
    except ObsModuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - report, never 500 on hardware
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": result.ok, "state": result.state, "error": result.error}


@router.post("/api/studios/{studio_id}/obs/collection")
def obs_studio_collection(studio_id: str, body: StudioCollectionBody, request: Request):
    """Switch the studio to a named scene collection."""
    _registry_or_404(request, studio_id)
    found = _studio_obs(request, studio_id)
    if found is None:
        raise HTTPException(
            status_code=422,
            detail=f"obs not configured for studio {studio_id} (studios.local/obs.json)",
        )
    module, _safe = found
    try:
        result = module.set_collection(body.collection)
    except ObsModuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - report, never 500 on hardware
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": result.ok, "state": result.state, "error": result.error}


@router.post("/api/studios/{studio_id}/obs/stream")
def obs_studio_stream(studio_id: str, body: StudioStreamBody, request: Request):
    """Start/stop the studio stream. Refuses without explicit confirmation."""
    _registry_or_404(request, studio_id)
    found = _studio_obs(request, studio_id)
    if found is None:
        raise HTTPException(
            status_code=422,
            detail=f"obs not configured for studio {studio_id} (studios.local/obs.json)",
        )
    module, _safe = found
    try:
        result = module.set_streaming(body.action, body.confirmed)
    except (ObsModuleError, SafetyViolation) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - report, never 500 on hardware
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ok": result.ok, "state": result.state, "error": result.error}


class BootBody(BaseModel):
    scene: str | None = None
    collection: str | None = None


class StudioCollectionBody(BaseModel):
    collection: str


class _BootLink:
    """In-process link transport for the boot job (no self-HTTP)."""

    def __init__(self, config_root):
        self.config_root = config_root

    def send_wait(self, command, wait_s):
        from ..drivers.link_audio import LinkError, listen_until, send_tones
        from ..linkproto import frame_for
        from .link import EXPECTED_ACKS, _link_config

        cfg = _link_config(self.config_root)
        try:
            send_tones(frame_for(command), device=cfg.get("audio_out"))
        except LinkError as exc:
            raise LinkError(f"boot TX failed: {exc}") from exc
        expected = EXPECTED_ACKS[command]
        try:
            heard = listen_until(wait_s, expected, device=cfg.get("audio_in"))
        except LinkError as exc:
            return {"heard": None, "others": [], "waited_s": 0.0, "listen_error": str(exc)}
        names = list(heard["commands"])
        first = next((name for name in names if name in expected), None)
        others = list(names)
        if first is not None:
            others.remove(first)
        return {"heard": first, "others": others, "waited_s": heard["seconds"]}

    def wol(self):
        from ..drivers.wol import WolError, wake
        from .link import _link_config

        mac = _link_config(self.config_root).get("wol_mac")
        if not mac:
            return "no wol_mac configured; relying on plug power"
        try:
            return f"{wake(mac)} magic packets sent"
        except WolError as exc:
            raise RuntimeError(f"wol failed: {exc}") from exc


def _boot_jobs(request):
    return request.app.state.__dict__.setdefault("boot_jobs", {})


def _boot_stops(request):
    return request.app.state.__dict__.setdefault("boot_stops", {})


def _job_snapshot(job):
    return {**job, "phases": list(job["phases"]), "running": job["state"] in RUNNING}


def _run_boot_thread(request, studio_id):
    jobs, stops = _boot_jobs(request), _boot_stops(request)
    job = jobs.get(studio_id)
    stop = stops.get(studio_id)
    if job is None or stop is None:
        return

    def obs_set(scene):
        found = _studio_obs(request, studio_id)
        if found is None:
            return False, f"obs not configured for studio {studio_id} (studios.local/obs.json)"
        module, safe = found
        try:
            result = module.switch_scene(scene, safe)
        except Exception as exc:  # noqa: BLE001 - driver faults read as a failed cut
            return False, str(exc)
        return bool(result.ok), result.error or ""

    def collect_set(collection):
        found = _studio_obs(request, studio_id)
        if found is None:
            return False, f"obs not configured for studio {studio_id} (studios.local/obs.json)"
        module, _safe = found
        try:
            result = module.set_collection(collection)
        except Exception as exc:  # noqa: BLE001 - driver faults read as a failed cut
            return False, str(exc)
        return bool(result.ok), result.error or ""

    log.info("obs boot job running", extra={"fields": {"studio": studio_id, "job": job["id"]}})
    run_boot(job, _BootLink(request.app.state.config_root), obs_set, stop=stop, collect_set=collect_set)
    log.info("obs boot job ended", extra={"fields": {"studio": studio_id, "job": job["id"], "state": job["state"]}})


def boot_try_start(request, studio_id, scene=None, collection=None):
    """Start the studio's boot job, or return the running one.

    Shared by the boot endpoint (which 409s on a running job), zone-On,
    and mode auto-OBS: those attach to the running job instead of failing.
    Returns (job snapshot, started_this_call).
    """
    jobs, stops = _boot_jobs(request), _boot_stops(request)
    current = jobs.get(studio_id)
    if current is not None and current["state"] in RUNNING:
        return _job_snapshot(current), False
    job = new_job(studio_id, scene, collection)
    stop = threading.Event()
    jobs[studio_id] = job
    stops[studio_id] = stop
    thread = threading.Thread(target=_run_boot_thread, args=(request, studio_id), daemon=True)
    thread.start()
    return _job_snapshot(job), True


@router.post("/api/studios/{studio_id}/obs/boot")
def obs_boot_start(studio_id: str, body: BootBody, request: Request):
    """Boot the main PC's OBS over the audio wire (async job, poll status).

    Pings until the listener answers, sends the obs launch, waits for
    obs_ready, then cuts to collection (when named) and scene (default
    Face Full View). One job per studio; 409 while one runs.
    """
    _registry_or_404(request, studio_id)
    job, started = boot_try_start(request, studio_id, body.scene, body.collection)
    if not started:
        raise HTTPException(status_code=409, detail=f"obs boot already running (job {job['id']})")
    return {"ok": True, "job": job}


@router.get("/api/studios/{studio_id}/obs/boot/status")
def obs_boot_status(studio_id: str, request: Request):
    """The studio's boot job (404 when none has ever run)."""
    _registry_or_404(request, studio_id)
    job = _boot_jobs(request).get(studio_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no obs boot job for studio {studio_id}")
    return {"job": _job_snapshot(job)}


@router.post("/api/studios/{studio_id}/obs/boot/stop")
def obs_boot_stop(studio_id: str, request: Request):
    """Stop the studio's boot job (takes effect at the next window edge)."""
    _registry_or_404(request, studio_id)
    jobs, stops = _boot_jobs(request), _boot_stops(request)
    job = jobs.get(studio_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"no obs boot job for studio {studio_id}")
    stop = stops.get(studio_id)
    if stop is not None:
        stop.set()
    return {"ok": True, "job": _job_snapshot(job)}
