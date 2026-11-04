# SPDX-License-Identifier: AGPL-3.0-or-later
"""Main-PC boot-and-OBS job: ping until the listener answers, launch OBS over
the audio wire, confirm obs_ready, then cut to the opening scene.

Long-running by nature (a cold PC needs minutes): callers run run_boot in a
worker thread and poll the job record. Link, scene-setter, sleep, and stop
are injected so tests never touch audio or the network.
"""

import time
from datetime import datetime, timezone
from uuid import uuid4

DEFAULT_SCENE = "Face Full View"

PING_WINDOW_S = 8.0
PING_ATTEMPTS = 90  # ~12 minutes: cold boot + autologin + listener start
OBS_WINDOW_S = 60.0
OBS_ATTEMPTS = 3
SCENE_ATTEMPTS = 3
SCENE_RETRY_S = 2.0

RUNNING = ("pinging", "launching", "setting_collection", "setting_scene")
TERMINAL = ("ready", "timeout", "obs_failed", "collection_failed", "scene_failed", "link_error", "error", "stopped")


def new_job(studio_id, scene=None, collection=None, obs_command="obs"):
    """Fresh job record. Empty collection skips the collection cut.

    obs_command selects which seat's listener answers the launch tone
    ("obs" for the show seat, "obs_bench" for the test-bench seat).
    """
    now = datetime.now(timezone.utc).isoformat()
    return {
        "id": uuid4().hex[:8],
        "studio": studio_id,
        "scene": scene if scene is not None else DEFAULT_SCENE,
        "collection": collection or None,
        "obs_command": obs_command,
        "state": "pinging",
        "phases": [],
        "started_at": now,
        "updated_at": now,
        "error": None,
    }


def _note(job, state, **detail):
    entry = {"phase": state, "at": datetime.now(timezone.utc).isoformat()}
    entry.update(detail)
    job["phases"].append(entry)
    job["state"] = state
    job["updated_at"] = entry["at"]


def _finish(job, state, error=None):
    job["state"] = state
    job["error"] = error
    job["updated_at"] = datetime.now(timezone.utc).isoformat()
    return job


def run_boot(job, link, obs_set, sleep=time.sleep, stop=None, cfg=None, collect_set=None):
    """Drive one boot job to a terminal state. Mutates and returns the job.

    link.send_wait(command, wait_s) -> {"heard": name|None, ...}; link
    exceptions fail fast (hub-side breakage, not main-PC silence).
    obs_set(scene) -> (ok, error). collect_set(collection) -> (ok, error)
    runs first when the job names a collection (unwired skips the cut).
    stop is a threading.Event (or None). cfg overrides PING/OBS/SCENE
    attempt and window constants (tests).
    """
    cfg = cfg or {}
    ping_attempts = cfg.get("ping_attempts", PING_ATTEMPTS)
    ping_window = cfg.get("ping_window", PING_WINDOW_S)
    obs_attempts = cfg.get("obs_attempts", OBS_ATTEMPTS)
    obs_window = cfg.get("obs_window", OBS_WINDOW_S)
    scene_attempts = cfg.get("scene_attempts", SCENE_ATTEMPTS)
    scene_retry = cfg.get("scene_retry", SCENE_RETRY_S)

    def halted():
        return stop is not None and stop.is_set()

    try:
        try:
            wol_note = link.wol()
        except Exception as exc:  # noqa: BLE001 - WoL is belt-and-braces; the plug boots the PC
            wol_note = f"wol skipped: {exc}"
        _note(job, "pinging", wol=wol_note, attempt=0)
        heard = None
        for attempt in range(1, ping_attempts + 1):
            if halted():
                return _finish(job, "stopped")
            try:
                heard = link.send_wait("ping", ping_window).get("heard")
            except Exception as exc:  # noqa: BLE001 - transport failure, fail fast
                return _finish(job, "link_error", f"ping window failed: {exc}")
            _note(job, "pinging", attempt=attempt, heard=heard)
            if heard == "ack":
                break
        else:
            return _finish(job, "timeout", f"no listener ack after {ping_attempts} pings")

        obs_command = job.get("obs_command", "obs")
        for attempt in range(1, obs_attempts + 1):
            if halted():
                return _finish(job, "stopped")
            try:
                outcome = link.send_wait(obs_command, obs_window)
            except Exception as exc:  # noqa: BLE001 - transport failure, fail fast
                return _finish(job, "link_error", f"obs window failed: {exc}")
            heard = outcome.get("heard")
            _note(job, "launching", attempt=attempt, heard=heard, others=outcome.get("others", []), command=obs_command)
            if heard == "obs_ready":
                break
            if heard == "obs_fail":
                continue
        else:
            return _finish(job, "obs_failed", f"OBS never reported ready after {obs_attempts} launches")

        collection = job.get("collection")
        if collection and collect_set is not None:
            for attempt in range(1, scene_attempts + 1):
                if halted():
                    return _finish(job, "stopped")
                try:
                    ok, error = collect_set(collection)
                except Exception as exc:  # noqa: BLE001 - driver faults read as a failed cut
                    ok, error = False, str(exc)
                _note(job, "setting_collection", attempt=attempt, collection=collection, ok=ok, error=error)
                if ok:
                    break
                if attempt < scene_attempts:
                    sleep(scene_retry)
            else:
                return _finish(job, "collection_failed", f"collection {collection!r} did not land (OBS is up)")

        scene = job.get("scene")
        if scene:
            for attempt in range(1, scene_attempts + 1):
                if halted():
                    return _finish(job, "stopped")
                try:
                    ok, error = obs_set(scene)
                except Exception as exc:  # noqa: BLE001 - driver faults read as a failed cut
                    ok, error = False, str(exc)
                _note(job, "setting_scene", attempt=attempt, scene=scene, ok=ok, error=error)
                if ok:
                    break
                if attempt < scene_attempts:
                    sleep(scene_retry)
            else:
                return _finish(job, "scene_failed", f"scene {scene!r} did not land (OBS is up)")
        return _finish(job, "ready")
    except Exception as exc:  # noqa: BLE001 - the job record must always land somewhere honest
        return _finish(job, "error", str(exc))
