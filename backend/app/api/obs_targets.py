# SPDX-License-Identifier: AGPL-3.0-or-later
"""OBS target administration: named WebSocket endpoints + per-studio defaults.

Targets (host/port/encrypted password) live hub-local in
studios.local/obs.json; each studio defaults to one target. Every
response is redacted (``has_password`` only) — a stored password is used
server-side for Test and for studio connects, and never echoed.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..core.logging import get_logger
from ..drivers.obs_ws import DEFAULT_PORT, ObsWsDriver
from ..obs_store import (
    UNSET,
    ObsCryptoError,
    ObsCryptoUnavailable,
    ObsStoreError,
    ObsTargetError,
    TargetConflictError,
    TargetReferencedError,
    UnknownTargetError,
    create_target,
    delete_target,
    list_targets,
    resolve_studio,
    resolve_target,
    set_studio_target,
    studio_target,
    update_target,
)

log = get_logger("api")
router = APIRouter()


class TargetCreateBody(BaseModel):
    name: str
    host: str
    port: int = DEFAULT_PORT
    password: str | None = None


class TargetUpdateBody(BaseModel):
    host: str | None = None
    port: int | None = None
    password: str | None = None


class StudioTargetBody(BaseModel):
    target: str | None = None


def _config_root(request):
    return request.app.state.config_root


def _reraise_store(exc):
    """Map store errors to HTTP honestly (never leaking secrets)."""
    if isinstance(exc, UnknownTargetError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (TargetConflictError, TargetReferencedError)):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, ObsCryptoUnavailable):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if isinstance(exc, ObsCryptoError):
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if isinstance(exc, ObsTargetError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if isinstance(exc, ObsStoreError):
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    raise exc


@router.get("/api/obs/targets")
def obs_targets_list(request: Request):
    """Named OBS endpoints (redacted: hosts and ports, never passwords)."""
    try:
        return {"targets": list_targets(_config_root(request))}
    except ObsStoreError as exc:
        _reraise_store(exc)


@router.post("/api/obs/targets", status_code=201)
def obs_targets_create(body: TargetCreateBody, request: Request):
    """Add an OBS endpoint. First write also migrates legacy entries."""
    try:
        target = create_target(
            _config_root(request), body.name, body.host, body.port, body.password
        )
    except ObsStoreError as exc:
        _reraise_store(exc)
    log.info("obs target added", extra={"fields": {"target": target["name"]}})
    return {"target": target}


@router.put("/api/obs/targets/{name}")
def obs_targets_update(name: str, body: TargetUpdateBody, request: Request):
    """Patch an endpoint. Omitted password keeps; explicit null clears."""
    sent = body.model_fields_set
    try:
        target = update_target(
            _config_root(request),
            name,
            host=body.host if "host" in sent else UNSET,
            port=body.port if "port" in sent else UNSET,
            password=body.password if "password" in sent else UNSET,
        )
    except ObsStoreError as exc:
        _reraise_store(exc)
    return {"target": target}


@router.delete("/api/obs/targets/{name}")
def obs_targets_delete(name: str, request: Request):
    """Remove an endpoint. Refuses while a studio defaults to it (409)."""
    try:
        return delete_target(_config_root(request), name)
    except ObsStoreError as exc:
        _reraise_store(exc)


@router.post("/api/obs/targets/{name}/test")
def obs_targets_test(name: str, request: Request):
    """Probe an endpoint with its stored credentials (never echoed)."""
    try:
        creds = resolve_target(_config_root(request), name)
    except ObsStoreError as exc:
        _reraise_store(exc)
    if creds is None:
        raise HTTPException(status_code=404, detail=f"unknown obs target: {name!r}")
    driver = ObsWsDriver(f"obs-test-{name}", creds["host"], creds["port"], creds["password"])
    try:
        result = driver.probe()
    finally:
        driver.safe_stop()
    return {"ok": result.ok, "state": result.state, "error": result.error}


def _registry_or_404(request, studio_id):
    if studio_id not in request.app.state.registries:
        raise HTTPException(status_code=404, detail=f"unknown studio: {studio_id}")


@router.get("/api/studios/{studio_id}/obs/target")
def obs_studio_target_get(studio_id: str, request: Request):
    """A studio's selected OBS target (null when unconfigured)."""
    _registry_or_404(request, studio_id)
    try:
        current = studio_target(_config_root(request), studio_id)
        names = [t["name"] for t in list_targets(_config_root(request))]
    except ObsStoreError as exc:
        _reraise_store(exc)
    if current is not None and current not in names:
        current = None  # dangling ref reads as unconfigured, like resolve does
    return {"studio": studio_id, "target": current, "targets": names}


@router.put("/api/studios/{studio_id}/obs/target")
def obs_studio_target_put(studio_id: str, body: StudioTargetBody, request: Request):
    """Point a studio at a target (null clears it back to unconfigured)."""
    _registry_or_404(request, studio_id)
    try:
        return set_studio_target(_config_root(request), studio_id, body.target)
    except UnknownTargetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ObsStoreError as exc:
        _reraise_store(exc)


def studio_credentials(config_root, studio_id):
    """Shared resolver for the studio endpoints + modes applier.

    Returns (host, port, password) or None when unconfigured. Raises
    ObsStoreError (malformed) and ObsCryptoError (undecryptable) for the
    callers to report honestly.
    """
    found = resolve_studio(config_root, studio_id)
    if found is None:
        return None
    return found["host"], found["port"], found["password"]
