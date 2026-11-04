# SPDX-License-Identifier: AGPL-3.0-or-later
"""Modality endpoints: list/read/save/delete + run/status/stop.

Storage is modalities.json per studio (local studios writable, tracked
examples read-only). Runs execute on a background thread (beats hold
for seconds); status/stop mirror the presentation contract.
"""

import json
import os
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..config.loader import ConfigError, check_references, load_profiles, load_studio, validate
from ..core.logging import get_logger
from ..core.safety import SafetyViolation
from ..modules.modality import ModalityError, apply_modality, plan_modality
from ..modules.modes import ModesError
from ..registry import Registry, RegistryError
from .modes import build_modes_module, record_action

log = get_logger("api")
router = APIRouter()


class SaveBody(BaseModel):
    label: str
    kind: str = "custom"
    notes: str | None = None
    beats: list


def _registry_or_404(request, studio_id):
    try:
        return request.app.state.registries[studio_id]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown studio: {studio_id}") from exc


def _path(config_root, studio_id):
    return Path(config_root) / "studios.local" / studio_id / "modalities.json"


def _save_all(request, studio_id, data):
    config_root = request.app.state.config_root
    path = _path(config_root, studio_id)
    if not path.parent.is_dir():
        raise HTTPException(status_code=422, detail="modality save only for local studios")
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    try:
        loaded = load_studio(config_root, studio_id)
        profiles = load_profiles(config_root)
        check_references(loaded, profiles, studio_id)
    except ConfigError as exc:
        raise HTTPException(status_code=422, detail=f"saved modality broke studio: {exc}") from exc
    request.app.state.registries[studio_id] = Registry.from_loaded(loaded, profiles)


def _load_all_file(request, studio_id):
    path = _path(request.app.state.config_root, studio_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return {}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"modalities.json unreadable: {exc}") from exc
    return data if isinstance(data, dict) else {}


@router.get("/api/studios/{studio_id}/modalities")
def modalities_list(studio_id: str, request: Request):
    registry = _registry_or_404(request, studio_id)
    return {"modalities": [
        {"id": mid, "label": registry.get_modality(mid).get("label", mid),
         "kind": registry.get_modality(mid).get("kind", "custom"),
         "beats": len(registry.get_modality(mid).get("beats") or [])}
        for mid in registry.modality_ids]}


@router.get("/api/studios/{studio_id}/modalities/{modality_id}")
def modality_get(studio_id: str, modality_id: str, request: Request):
    registry = _registry_or_404(request, studio_id)
    try:
        return {"modality": registry.get_modality(modality_id)}
    except RegistryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/api/studios/{studio_id}/modalities/{modality_id}")
def modality_save(studio_id: str, modality_id: str, body: SaveBody, request: Request):
    registry = _registry_or_404(request, studio_id)
    modality = {"label": body.label, "kind": body.kind, "beats": body.beats}
    if body.notes:
        modality["notes"] = body.notes
    try:
        validate(modality, "modality", f"modality {modality_id}")
        plan_modality(registry, modality_id, modality)
    except (ConfigError, ModalityError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    data = _load_all_file(request, studio_id)
    data[modality_id] = modality
    _save_all(request, studio_id, data)
    log.info("modality save", extra={"fields": {"studio": studio_id, "modality": modality_id}})
    return {"modality": modality_id, "saved": True}


@router.delete("/api/studios/{studio_id}/modalities/{modality_id}")
def modality_delete(studio_id: str, modality_id: str, request: Request):
    _registry_or_404(request, studio_id)
    data = _load_all_file(request, studio_id)
    if modality_id not in data:
        raise HTTPException(status_code=404, detail=f"unknown modality: {modality_id}")
    del data[modality_id]
    _save_all(request, studio_id, data)
    return {"modality": modality_id, "deleted": True}


def _slot(request, studio_id):
    return request.app.state.modality_runs.get(studio_id)


@router.post("/api/studios/{studio_id}/modalities/{modality_id}/run")
def modality_run(studio_id: str, modality_id: str, request: Request):
    registry = _registry_or_404(request, studio_id)
    try:
        modality = registry.get_modality(modality_id)
    except RegistryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    existing = _slot(request, studio_id)
    if existing and existing["state"] == "running":
        raise HTTPException(status_code=409, detail="a modality is already running for this studio")
    module = build_modes_module(registry, request.app.state.config_root)
    try:
        plan_modality(registry, modality_id, modality)
    except ModalityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    slot = {"id": modality_id, "state": "running", "beat": 0, "beats": len(modality["beats"]),
            "ok": True, "stop": threading.Event(), "result": None}
    request.app.state.modality_runs[studio_id] = slot

    def work():
        import time as _time

        try:
            result = apply_modality(module, modality_id, modality, sleep=_time.sleep,
                                    should_stop=slot["stop"].is_set)
            slot["result"] = result
            slot["ok"] = result["ok"]
            slot["state"] = "done"
            record_action(request, studio_id, "modality", modality_id, None,
                          {"ok": result["ok"], "subsystems": {}})
        except (ModalityError, ModesError, SafetyViolation, RegistryError) as exc:
            slot["state"] = "failed"
            slot["ok"] = False
            slot["result"] = {"error": str(exc)}

    threading.Thread(target=work, daemon=True).start()
    log.info("modality run", extra={"fields": {"studio": studio_id, "modality": modality_id}})
    return {"modality": modality_id, "state": "running", "beats": slot["beats"]}


@router.get("/api/studios/{studio_id}/modalities/{modality_id}/status")
def modality_status(studio_id: str, modality_id: str, request: Request):
    _registry_or_404(request, studio_id)
    slot = _slot(request, studio_id)
    if slot is None or slot["id"] != modality_id:
        return {"modality": modality_id, "state": "idle", "ok": True}
    return {"modality": modality_id, "state": slot["state"], "ok": slot["ok"], "result": slot["result"]}


@router.post("/api/studios/{studio_id}/modalities/{modality_id}/stop")
def modality_stop(studio_id: str, modality_id: str, request: Request):
    _registry_or_404(request, studio_id)
    slot = _slot(request, studio_id)
    if slot is None or slot["id"] != modality_id or slot["state"] != "running":
        return {"modality": modality_id, "state": "idle"}
    slot["stop"].set()
    return {"modality": modality_id, "state": "stopping to final beat"}
