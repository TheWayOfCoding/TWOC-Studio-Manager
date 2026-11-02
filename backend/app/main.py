# SPDX-License-Identifier: AGPL-3.0-or-later
"""Studio Hub entrypoint: FastAPI app serving the API and web UI."""

import os
import uuid
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse

from . import __version__
from .core.logging import bind_context, configure_logging, get_logger

# Configure before importing routes: get_logger() calls at import time must
# already see the final log directory.
configure_logging(log_dir=os.environ.get("STUDIO_LOG_DIR", "logs"))

from .api.routes import router  # noqa: E402
from .api.kasa import router as kasa_router  # noqa: E402
from .api.ir import router as ir_router  # noqa: E402
from .api.gvm import router as gvm_router  # noqa: E402
from .api.modes import router as modes_router  # noqa: E402
from .api.ninja import router as ninja_router  # noqa: E402
from .api.obs import router as obs_router  # noqa: E402
from .api.power import router as power_router  # noqa: E402
from .api.screen import router as screen_router  # noqa: E402
from .api.dmx import router as dmx_router  # noqa: E402
from .api.equipment import router as equipment_router  # noqa: E402
from .api.ai import router as ai_router  # noqa: E402
from .api.shows import router as shows_router  # noqa: E402
from .config.loader import ConfigError, list_studios, load_all  # noqa: E402
from .registry import Registry  # noqa: E402

STATIC_DIR = Path(__file__).parent / "static"


def config_root():
    """Resolve the config directory: STUDIO_CONFIG env or repo config/."""
    default = Path(__file__).resolve().parents[2] / "config"
    return Path(os.environ.get("STUDIO_CONFIG", str(default)))


def create_app():
    """Build the app. Broken config yields degraded health, never a crash."""
    log = get_logger("core")
    app = FastAPI(title="TWOC Studio Hub", version=__version__)
    app.state.boot_id = uuid.uuid4().hex[:12]  # kiosks reload when this changes (every restart)

    @app.middleware("http")
    async def add_request_id(request, call_next):
        request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:12])
        bind_context(request_id=request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    try:
        data = load_all(config_root())
        registries = {
            studio_id: Registry.from_loaded(loaded, data["profiles"])
            for studio_id, loaded in data["studios"].items()
        }
        app.state.config_root = config_root()
        app.state.registries = registries
        app.state.studio_list = list_studios(config_root())
        app.state.health = {"status": "ok", "studios": sorted(registries)}
        log.info("config loaded", extra={"fields": {"studios": sorted(registries)}})
    except ConfigError as exc:
        app.state.registries = {}
        app.state.studio_list = []
        app.state.health = {"status": "degraded", "studios": [], "error": str(exc)}
        log.error("config load failed: %s", exc)
    app.state.show_runs = {}
    app.state.action_log = {}  # studio_id -> [mode/preset/AI applies], undo replays the tail

    # Specific bench routers first: the generic /api/testbench/{driver_id}/
    # routes below would otherwise shadow real-driver endpoints like gvm/probe.
    app.include_router(kasa_router)
    app.include_router(ir_router)
    app.include_router(gvm_router)
    app.include_router(modes_router)
    app.include_router(ninja_router)
    app.include_router(obs_router)
    app.include_router(power_router)
    app.include_router(screen_router)
    app.include_router(dmx_router)
    app.include_router(equipment_router)
    app.include_router(ai_router)
    app.include_router(shows_router)
    app.include_router(router)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/bench", include_in_schema=False)
    def bench():
        return FileResponse(STATIC_DIR / "bench.html")

    @app.get("/equipment", include_in_schema=False)
    def equipment():
        return FileResponse(STATIC_DIR / "equipment.html")

    @app.get("/screen/{screen_id}", include_in_schema=False)
    def screen_page(screen_id: str):
        return FileResponse(STATIC_DIR / "screen.html")

    return app


app = create_app()
