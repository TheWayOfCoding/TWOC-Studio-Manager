# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ordering admin API: read/save custom list order per studio.

Ordering lives in studios.local/<id>/ordering.json (id arrays only).
Reads merge the saved order with live ids (missing ids append
alphabetically); saves validate every id and write atomically, then
reload the studio registry so every list reflects it immediately.
"""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..config.loader import check_references, load_profiles, load_studio
from ..config.ordering import (
    PANELS,
    OrderingError,
    load_ordering,
    ordered_ids,
    save_ordering,
)
from ..core.logging import get_logger
from ..registry import Registry

log = get_logger("api")
router = APIRouter()


class OrderingBody(BaseModel):
    ordering: dict


def _registry_or_404(request, studio_id):
    try:
        return request.app.state.registries[studio_id]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown studio: {studio_id}") from exc


def _plug_ids(config_root):
    path = Path(config_root) / "studios.local" / "plug-map.json"
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))["plugs"]
    except (OSError, ValueError, KeyError):
        return []
    return [e["plug"] for e in entries if isinstance(e, dict) and e.get("plug")]


def _catalog(request, studio_id):
    """Every orderable collection as [{id, label}] in effective order."""
    registry = _registry_or_404(request, studio_id)
    config_root = request.app.state.config_root
    ordering = load_ordering(config_root, studio_id)

    def entries(collection, ids, label_of):
        labels = {i: label_of(i) for i in ids}
        return [{"id": i, "label": labels[i]} for i in ordered_ids(ordering.get(collection), ids, labels)]

    plug_labels = {}
    try:
        raw = json.loads((Path(config_root) / "studios.local" / "plug-map.json").read_text(encoding="utf-8"))["plugs"]
        plug_labels = {e["plug"]: e.get("alias") or e["plug"] for e in raw if isinstance(e, dict) and e.get("plug")}
    except (OSError, ValueError, KeyError):
        pass
    return {
        "zones": entries("zones", registry.zone_ids, lambda i: registry.get_zone(i).get("label", i)),
        "devices": entries("devices", registry.device_ids, lambda i: registry.get_device(i).get("name", i)),
        "modes": entries("modes", registry.mode_ids, lambda i: registry.get_mode(i).get("label", i)),
        "presets": entries("presets", registry.preset_ids, lambda i: registry.get_preset(i).get("label", i)),
        "plugs": entries("plugs", _plug_ids(config_root), lambda i: plug_labels.get(i, i)),
        "panels": entries("panels", [pid for pid, _ in PANELS], lambda i: dict(PANELS).get(i, i))
        if ordering.get("panels")
        else [{"id": pid, "label": label} for pid, label in PANELS],
        "modalities": entries("modalities", registry.modality_ids, lambda i: registry.get_modality(i).get("label", i)),
    }


@router.get("/api/studios/{studio_id}/ordering")
def ordering_get(studio_id: str, request: Request):
    """Current effective order for every collection (custom + fallback)."""
    catalog = _catalog(request, studio_id)
    return {"studio": studio_id, "collections": catalog,
            "saved": load_ordering(request.app.state.config_root, studio_id)}


@router.put("/api/studios/{studio_id}/ordering")
def ordering_save(studio_id: str, body: OrderingBody, request: Request):
    """Persist a full ordering (local studios only) and reload the registry."""
    registry = _registry_or_404(request, studio_id)
    config_root = request.app.state.config_root
    valid = {
        "zones": set(registry.zone_ids),
        "devices": set(registry.device_ids),
        "modes": set(registry.mode_ids),
        "presets": set(registry.preset_ids),
        "plugs": set(_plug_ids(config_root)),
        "panels": {pid for pid, _ in PANELS},
        "modalities": set(registry.modality_ids),
    }
    log.info("ordering save", extra={"fields": {"studio": studio_id, "collections": sorted(body.ordering)}})
    try:
        clean = save_ordering(config_root, studio_id, body.ordering, valid)
    except OrderingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        loaded = load_studio(config_root, studio_id)
        profiles = load_profiles(config_root)
        check_references(loaded, profiles, studio_id)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"saved ordering broke studio: {exc}") from exc
    request.app.state.registries[studio_id] = Registry.from_loaded(loaded, profiles)
    return {"studio": studio_id, "collections": _catalog(request, studio_id), "saved": clean}
