# SPDX-License-Identifier: AGPL-3.0-or-later
"""Studio power surfaces: zone listing/switching and Emergency All Off."""

import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..config.loader import (
    ConfigError,
    check_references,
    load_profiles,
    load_studio,
    validate,
)
from ..core.logging import get_logger
from ..core.safety import SafetyViolation
from ..modules.power import PowerError, PowerModule, UnknownPowerGroupError
from ..registry import Registry, RegistryError
from .modes import _plug_drivers, record_action
from .obs import boot_try_start

log = get_logger("api")
router = APIRouter()


class ZonePowerBody(BaseModel):
    on: bool


class AllOffBody(BaseModel):
    include_manual: bool = False


class ZoneConfigBody(BaseModel):
    autoStartObs: bool


class ZoneSaveError(Exception):
    """Unusable zone save (non-local studio, unknown zone, broken file)."""


def save_zone_flag(config_root, studio_id, zone_id, flag):
    """Persist a zone's autoStartObs flag, reload registry.

    Only studios with a studios.local directory can receive it; tracked
    example studios are refused. Returns the reloaded Registry.
    """
    zones_path = Path(config_root) / "studios.local" / studio_id / "zones.json"
    if not zones_path.exists():
        raise ZoneSaveError(f"zone save only for local studios (no studios.local/{studio_id})")
    try:
        stored = json.loads(zones_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ZoneSaveError(f"cannot read {zones_path}: {exc}") from exc
    if not isinstance(stored, list):
        raise ZoneSaveError(f"{zones_path} must hold a zone list")
    matches = [item for item in stored if isinstance(item, dict) and item.get("id") == zone_id]
    if not matches:
        raise ZoneSaveError(f"zone {zone_id} not in {zones_path}")
    matches[0]["autoStartObs"] = flag
    try:
        validate(matches[0], "zone", f"zone {zone_id}")
    except ConfigError as exc:
        raise ZoneSaveError(str(exc)) from exc
    tmp_path = zones_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(stored, indent=2), encoding="utf-8")
    os.replace(tmp_path, zones_path)
    try:
        loaded = load_studio(config_root, studio_id)
        profiles = load_profiles(config_root)
        check_references(loaded, profiles, studio_id)
    except ConfigError as exc:
        raise ZoneSaveError(f"saved flag broke studio references: {exc}") from exc
    return Registry.from_loaded(loaded, profiles)


def _registry_or_404(request, studio_id):
    try:
        return request.app.state.registries[studio_id]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown studio: {studio_id}") from exc


def _power_module(request, studio_id):
    registry = _registry_or_404(request, studio_id)
    return registry, PowerModule(
        "power", registry, _plug_drivers(registry, request.app.state.config_root)
    )


@router.get("/api/studios/{studio_id}/zones")
def zones_list(studio_id: str, request: Request):
    """Zones with their power groups and plugs (static map, no hardware calls)."""
    registry = _registry_or_404(request, studio_id)
    zones = []
    for zone_id in registry.zone_ids:
        zone = registry.get_zone(zone_id)
        plugs = sorted(
            {item["powerPlug"] for item in registry.devices_in_zone(zone_id) if item.get("powerPlug")}
        )
        zones.append(
            {
                "id": zone_id,
                "label": zone.get("label", zone_id),
                "groups": zone.get("powerGroups", []),
                "plugs": plugs,
                "autoStartObs": bool(zone.get("autoStartObs")),
            }
        )
    return {"zones": zones}


@router.post("/api/studios/{studio_id}/zones/{zone_id}/power")
def zone_power(zone_id: str, studio_id: str, body: ZonePowerBody, request: Request):
    """Switch a whole zone on/off through the safety gate.

    Powering on a zone flagged autoStartObs also kicks the OBS boot job
    (async; the response carries its id for status polling).
    """
    registry, module = _power_module(request, studio_id)
    if zone_id not in registry.zone_ids:
        raise HTTPException(status_code=404, detail=f"unknown zone: {zone_id}")
    log.info("zone power", extra={"fields": {"studio": studio_id, "zone": zone_id, "on": body.on}})
    try:
        results = module.set_zone_power(zone_id, body.on, sleep=lambda s: None)
    except SafetyViolation as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PowerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    ok = all(item["ok"] for item in results)
    record_action(
        request,
        studio_id,
        "power",
        f"zone:{zone_id}:{'on' if body.on else 'off'}",
        None,
        {"ok": ok, "subsystems": {"power": {"status": "ready" if ok else "degraded"}}},
    )
    obs_boot = None
    if body.on and registry.get_zone(zone_id).get("autoStartObs"):
        if ok:
            job, started = boot_try_start(request, studio_id)
            obs_boot = {"job": job["id"], "started": started, "state": job["state"], "scene": job["scene"]}
        else:
            obs_boot = {"skipped": "power did not come up clean; obs boot not started"}
    return {"ok": ok, "applied": results, "obs_boot": obs_boot}


@router.put("/api/studios/{studio_id}/zones/{zone_id}/config")
def zone_config_save(studio_id: str, zone_id: str, body: ZoneConfigBody, request: Request):
    """Set a zone's autoStartObs flag (local studios only)."""
    _registry_or_404(request, studio_id)
    if zone_id not in request.app.state.registries[studio_id].zone_ids:
        raise HTTPException(status_code=404, detail=f"unknown zone: {zone_id}")
    log.info("zone config save", extra={"fields": {"studio": studio_id, "zone": zone_id}})
    try:
        reloaded = save_zone_flag(request.app.state.config_root, studio_id, zone_id, body.autoStartObs)
    except ZoneSaveError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    request.app.state.registries[studio_id] = reloaded
    return {"zone": zone_id, "autoStartObs": body.autoStartObs}


@router.post("/api/studios/{studio_id}/power/all-off")
def power_all_off(studio_id: str, body: AllOffBody, request: Request):
    """Emergency All Off: every auto-policy plug off; manual-only needs the flag.

    `never` devices never switch programmatically, flag or not.
    """
    _, module = _power_module(request, studio_id)
    log.info(
        "all off", extra={"fields": {"studio": studio_id, "include_manual": body.include_manual}}
    )
    try:
        results, skipped = module.set_all_power(False, include_manual=body.include_manual, sleep=lambda s: None)
    except (PowerError, UnknownPowerGroupError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    ok = all(item["ok"] for item in results)
    record_action(
        request,
        studio_id,
        "power",
        "all-off",
        None,
        {"ok": ok, "subsystems": {"power": {"status": "ready" if ok else "degraded"}}},
    )
    return {"ok": ok, "applied": results, "skipped": skipped}
