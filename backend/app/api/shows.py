# SPDX-License-Identifier: AGPL-3.0-or-later
"""Presentation sequence endpoints: list shows, run/stop one at a time."""

import threading

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..core.logging import get_logger
from ..modules.kiosk import ensure_kiosk_for_show
from ..modules.shows import ShowsError, list_shows, load_show, run_show
from .modes import _screens_applier, build_modes_module

log = get_logger("api")
router = APIRouter()

_run_lock = threading.Lock()


class RunBody(BaseModel):
    confirm_scenes: bool = False


def _registry_or_404(request, studio_id):
    try:
        return request.app.state.registries[studio_id]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown studio: {studio_id}") from exc


def _show_or_404(request, studio_id, show_id):
    try:
        return load_show(request.app.state.config_root, studio_id, show_id)
    except ShowsError as exc:
        if str(exc).startswith("unknown show"):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _slot(request, studio_id):
    runs = request.app.state.show_runs
    return runs.get(studio_id)


def _public_status(show_id, slot):
    if slot is None:
        return {"show": show_id, "state": "idle", "step": 0, "steps": 0, "ok": True, "failed": []}
    with slot["guard"]:
        return {
            "show": slot["show"],
            "state": slot["state"],
            "step": slot["step"],
            "steps": slot["steps"],
            "label": slot["label"],
            "ok": slot["ok"],
            "caption": slot["caption"],
            "failed": list(slot["failed"]),
        }


@router.get("/api/studios/{studio_id}/shows")
def shows_list(studio_id: str, request: Request):
    """List a studio's presentation sequences."""
    _registry_or_404(request, studio_id)
    return {"shows": list_shows(request.app.state.config_root, studio_id)}


@router.get("/api/studios/{studio_id}/shows/{show_id}/status")
def shows_status(studio_id: str, show_id: str, request: Request):
    """Current run slot (one show at a time per studio)."""
    _registry_or_404(request, studio_id)
    _show_or_404(request, studio_id, show_id)
    return _public_status(show_id, _slot(request, studio_id))


@router.post("/api/studios/{studio_id}/shows/{show_id}/run")
def shows_run(studio_id: str, show_id: str, body: RunBody, request: Request):
    """Start a show in the background. 409 while another runs in the studio."""
    registry = _registry_or_404(request, studio_id)
    show = _show_or_404(request, studio_id, show_id)
    if any(step["scenes"] for step in show["steps"]) and not body.confirm_scenes:
        raise HTTPException(status_code=422, detail=f"{show_id} includes scene steps: confirm scenes to run it")
    with _run_lock:
        slot = _slot(request, studio_id)
        if slot is not None and slot["state"] == "running":
            raise HTTPException(status_code=409, detail=f"show {slot['show']} already running")
        stop = threading.Event()
        slot = {
            "show": show_id,
            "state": "running",
            "step": 0,
            "steps": len(show["steps"]),
            "label": show["label"],
            "ok": True,
            "caption": "",
            "failed": [],
            "stop": stop,
            "guard": threading.Lock(),
            "thread": None,
        }
        request.app.state.show_runs[studio_id] = slot
    modes = build_modes_module(registry, request.app.state.config_root)
    screens = _screens_applier(registry)
    log.info("show run", extra={"fields": {"studio": studio_id, "show": show_id}})
    kiosk_note = ensure_kiosk_for_show(show)
    log.info(
        "show kiosk",
        extra={"fields": {"studio": studio_id, "show": show_id, "kiosk": kiosk_note}},
    )

    def progress(index, record):
        with slot["guard"]:
            slot["step"] = index
            slot["caption"] = record["caption"]
            slot["ok"] = slot["ok"] and record["ok"]
            if not record["ok"]:
                slot["failed"].append(record["label"])
        log.info(
            "show step",
            extra={"fields": {"studio": studio_id, "show": show_id, "step": index,
                              "label": record["label"], "ok": record["ok"],
                              "caption": record["caption"]}},
        )

    def work():
        try:
            outcome = run_show(modes, screens, show, stop=stop,
                               confirm_scenes=body.confirm_scenes, on_step=progress)
            final = outcome["state"]
            ok = outcome["ok"]
        except ShowsError as exc:
            log.warning("show failed: %s", exc)
            final, ok = "failed", False
            with slot["guard"]:
                slot["caption"] = str(exc)
        except Exception as exc:  # noqa: BLE001 - a show must never kill its thread silently
            log.warning("show crashed: %s", exc)
            final, ok = "failed", False
            with slot["guard"]:
                slot["caption"] = f"crashed: {exc}"
        with slot["guard"]:
            slot["state"] = final
            slot["ok"] = slot["ok"] and ok

    thread = threading.Thread(target=work, daemon=True, name=f"show-{studio_id}-{show_id}")
    slot["thread"] = thread
    thread.start()
    return _public_status(show_id, slot)


@router.post("/api/studios/{studio_id}/shows/{show_id}/stop")
def shows_stop(studio_id: str, show_id: str, request: Request):
    """Stop the running show (it lands on its final step). Idle when none runs."""
    _registry_or_404(request, studio_id)
    _show_or_404(request, studio_id, show_id)
    slot = _slot(request, studio_id)
    if slot is not None and slot["state"] == "running":
        slot["stop"].set()
    return _public_status(show_id, slot)
