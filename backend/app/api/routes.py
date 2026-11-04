# SPDX-License-Identifier: AGPL-3.0-or-later
"""REST routes: health, studios, and the Test Bench surface."""

import os
import subprocess
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


MAIN_TASK = "StudioHub"
HELPER_TASK = "StudioHubRestartOnce"


class RestartBody(BaseModel):
    confirm: bool = False


def _launch_hub_restart(runner=subprocess.run):
    """Arm a one-shot helper task that restarts the main hub task.

    The restart must be driven from OUTSIDE this process (ending the
    StudioHub task kills us mid-request), so the helper is its own
    scheduled task: it waits, ends the main task, starts it again,
    then deletes itself. Raises RuntimeError on any failed step.
    """
    helper_cmd = (
        "cmd /c ping -n 4 127.0.0.1 >nul & schtasks /End /TN " + MAIN_TASK
        + " & ping -n 6 127.0.0.1 >nul & schtasks /Run /TN " + MAIN_TASK
        + " & schtasks /Delete /TN " + HELPER_TASK + " /F"
    )
    create = runner(
        ["schtasks", "/Create", "/TN", HELPER_TASK, "/TR", helper_cmd,
         "/SC", "DAILY", "/ST", "00:00", "/RU", "SYSTEM", "/RL", "HIGHEST", "/F"],
        capture_output=True, timeout=30, check=False,
    )
    if create.returncode != 0:
        raise RuntimeError("could not create the restart helper task")
    run = runner(
        ["schtasks", "/Run", "/TN", HELPER_TASK],
        capture_output=True, timeout=30, check=False,
    )
    if run.returncode != 0:
        raise RuntimeError("could not start the restart helper task")
    return True


@router.post("/api/hub/restart")
def hub_restart(body: RestartBody):
    """Restart the hub program (website + Bluetooth) via its task.

    The site drops for ~30 s and in-memory kiosk state clears. This is
    the recovery for a wedged Bluetooth stack: fresh process, fresh
    radio. Needs an explicit confirm so it never fires by accident.
    """
    if body.confirm is not True:
        raise HTTPException(status_code=422, detail="tick confirm first (confirm: true)")
    if os.name != "nt":
        raise HTTPException(status_code=503, detail="hub restart is Windows-only")
    try:
        _launch_hub_restart()
    except Exception as exc:  # noqa: BLE001 - reported honestly, never faked
        raise HTTPException(status_code=503, detail=f"restart not started: {exc}") from exc
    log.warning("hub restart requested via API")
    return {"ok": True, "restarting": True, "note": "hub restarts now; site returns in ~30s"}


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
