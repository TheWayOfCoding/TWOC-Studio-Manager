# SPDX-License-Identifier: AGPL-3.0-or-later
"""AI creative director endpoint: one prompt + target selector, validated."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..ai.director import apply_look
from ..ai.mock import MockProvider
from ..core.logging import get_logger
from ..core.safety import SafetyViolation
from ..modules.modes import ModesError
from .modes import _address_labels, _scrub, build_modes_module

log = get_logger("api")
router = APIRouter()


class LookBody(BaseModel):
    prompt: str = ""
    targets: str = "both"


@router.post("/api/studios/{studio_id}/ai/look")
def ai_look(studio_id: str, body: LookBody, request: Request):
    """Prompted look on lights/screen/both: direct -> validate -> critique -> apply.

    Mock provider in the scaffold; the pipeline (and its gate) is the real one.
    """
    try:
        registry = request.app.state.registries[studio_id]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown studio: {studio_id}") from exc
    module = build_modes_module(registry, request.app.state.config_root)
    log.info("ai look", extra={"fields": {"studio": studio_id, "targets": body.targets}})
    try:
        outcome = apply_look(MockProvider(), module, body.prompt, body.targets)
    except (ModesError, SafetyViolation) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _scrub(outcome, _address_labels(registry))
