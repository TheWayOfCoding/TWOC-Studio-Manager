# SPDX-License-Identifier: AGPL-3.0-or-later
"""Studio power surfaces: zone listing/switching and Emergency All Off."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..core.logging import get_logger
from ..modules.power import PowerError, PowerModule, UnknownPowerGroupError
from ..registry import RegistryError
from .modes import _plug_drivers, record_action

log = get_logger("api")
router = APIRouter()


class ZonePowerBody(BaseModel):
    on: bool


class AllOffBody(BaseModel):
    include_manual: bool = False


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
            {"id": zone_id, "label": zone.get("label", zone_id), "groups": zone.get("powerGroups", []), "plugs": plugs}
        )
    return {"zones": zones}


@router.post("/api/studios/{studio_id}/zones/{zone_id}/power")
def zone_power(zone_id: str, studio_id: str, body: ZonePowerBody, request: Request):
    """Switch a whole zone on/off through the safety gate."""
    registry, module = _power_module(request, studio_id)
    if zone_id not in registry.zone_ids:
        raise HTTPException(status_code=404, detail=f"unknown zone: {zone_id}")
    log.info("zone power", extra={"fields": {"studio": studio_id, "zone": zone_id, "on": body.on}})
    try:
        results = module.set_zone_power(zone_id, body.on, sleep=lambda s: None)
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
    return {"ok": ok, "applied": results}


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
