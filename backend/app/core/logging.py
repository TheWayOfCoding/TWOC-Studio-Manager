# SPDX-License-Identifier: AGPL-3.0-or-later
"""Debug-first structured logging: one JSON-lines file per subsystem per day.

Layout: <log_dir>/YYYY-MM-DD/<subsystem>.log. Every record carries a timestamp,
level, subsystem, correlation/request id, and studio/mode context.
"""

import contextvars
import json
import logging
import threading
from datetime import date
from pathlib import Path
from typing import Callable

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
_studio: contextvars.ContextVar[str] = contextvars.ContextVar("studio", default="-")
_mode: contextvars.ContextVar[str] = contextvars.ContextVar("mode", default="-")


def bind_context(*, request_id=None, studio=None, mode=None):
    """Attach correlation context to subsequent log records on this task/thread."""
    if request_id is not None:
        _request_id.set(request_id)
    if studio is not None:
        _studio.set(studio)
    if mode is not None:
        _mode.set(mode)


class JsonFormatter(logging.Formatter):
    """Render records as single-line JSON objects."""

    def format(self, record):
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "subsystem": getattr(record, "subsystem", "-"),
            "request_id": _request_id.get(),
            "studio": _studio.get(),
            "mode": _mode.get(),
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        return json.dumps(payload, default=str)


class DatedFileHandler(logging.Handler):
    """Append to <log_dir>/<today>/<subsystem>.log, rotating on day change."""

    def __init__(self, log_dir, subsystem, date_fn=lambda: date.today()):
        super().__init__()
        self.log_dir = Path(log_dir)
        self.subsystem = subsystem
        self.date_fn = date_fn
        self._day = None
        self._stream = None
        self._lock = threading.Lock()
        self.setFormatter(JsonFormatter())

    def emit(self, record):
        try:
            day = self.date_fn().isoformat()
            with self._lock:
                if day != self._day:
                    self._rotate(day)
                record.subsystem = self.subsystem
                self._stream.write(self.format(record) + "\n")
                self._stream.flush()
        except Exception:
            self.handleError(record)

    def _rotate(self, day):
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                pass
        day_dir = self.log_dir / day
        day_dir.mkdir(parents=True, exist_ok=True)
        self._stream = open(day_dir / f"{self.subsystem}.log", "a", encoding="utf-8")
        self._day = day

    def close(self):
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
        super().close()


_state = {"log_dir": Path("logs"), "default_level": "DEBUG", "levels": {}}


def configure_logging(log_dir="logs", default_level="DEBUG", levels=None):
    """Set global logging defaults. Safe to call again (e.g. in tests)."""
    _state["log_dir"] = Path(log_dir)
    _state["default_level"] = default_level
    _state["levels"] = dict(levels or {})


def get_logger(subsystem):
    """Return the logger for a subsystem, attaching its dated file handler once."""
    name = f"studio.{subsystem}"
    logger = logging.getLogger(name)
    if not any(isinstance(h, DatedFileHandler) for h in logger.handlers):
        level = _state["levels"].get(subsystem, _state["default_level"])
        logger.setLevel(level)
        logger.addHandler(DatedFileHandler(_state["log_dir"], subsystem))
        logger.propagate = False
    return logger


def reset_logging():
    """Close all studio handlers. Test helper for a clean slate between cases."""
    for name in list(logging.Logger.manager.loggerDict):
        if name == "studio" or name.startswith("studio."):
            logger = logging.getLogger(name)
            for handler in list(logger.handlers):
                if isinstance(handler, DatedFileHandler):
                    logger.removeHandler(handler)
                    handler.close()
