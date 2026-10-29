# SPDX-License-Identifier: AGPL-3.0-or-later
"""REST routes: health, studios, and the Test Bench surface."""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import __version__
from ..core.logging import bind_context, get_logger
from ..drivers.mock import MockDriver
from ..registry import Registry, RegistryError

log = get_logger("api")
router = APIRouter()

MOCK_DRIVERS = {f"mock-{kind}": MockDriver(f"mock-{kind}") for kind in ("kasa", "dmx", "gvm", "ir", "obs", "screen")}


class ApplyBody(BaseModel):
    command: dict


def _registries(request):
    return request.app.state.registries


@router.get("/api/health")
def health(request: Request):
    """Liveness plus config-load status. Never fails when config is broken."""
    status = request.app.state.health
    log.debug("health check", extra={"fields": {"status": status["status"]}})
    return {"version": __version__, **status}


@router.get("/api/studios")
def studios(request: Request):
    """List studios as {id, label, source} entries."""
    return request.app.state.studio_list


@router.get("/api/studios/{studio_id}/devices")
def studio_devices(studio_id: str, request: Request):
    """List effective devices (instance merged with profile) for a studio."""
    registries = _registries(request)
    if studio_id not in registries:
        raise HTTPException(status_code=404, detail=f"unknown studio: {studio_id}")
    registry = registries[studio_id]
    return [registry.effective_device(device_id) for device_id in registry.device_ids]


@router.get("/api/testbench/drivers")
def bench_drivers():
    """List Test Bench drivers (mock hardware in Phase 0)."""
    return [driver.as_dict() for driver in MOCK_DRIVERS.values()]


def _bench_driver_or_404(driver_id):
    try:
        return MOCK_DRIVERS[driver_id]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown bench driver: {driver_id}") from exc


@router.post("/api/testbench/{driver_id}/probe")
def bench_probe(driver_id: str):
    """Run a driver's probe routine and return the raw result."""
    return asdict(_bench_driver_or_404(driver_id).probe())


@router.post("/api/testbench/{driver_id}/apply")
def bench_apply(driver_id: str, body: ApplyBody):
    """Apply a raw command dict to a bench driver. Mock-only in Phase 0."""
    return asdict(_bench_driver_or_404(driver_id).apply(body.command))


@router.post("/api/testbench/{driver_id}/safe-stop")
def bench_safe_stop(driver_id: str):
    """Bring a bench driver to its safe resting state."""
    return asdict(_bench_driver_or_404(driver_id).safe_stop())
