# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for dated JSON-lines logging."""

import json
import logging
from datetime import date
from pathlib import Path

import pytest

from app.core.logging import DatedFileHandler, JsonFormatter, bind_context, configure_logging, get_logger, reset_logging


@pytest.fixture(autouse=True)
def clean_logging():
    reset_logging()
    yield
    reset_logging()


def _read_records(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


@pytest.mark.unit
def test_json_fields_and_context(tmp_path):
    configure_logging(log_dir=tmp_path)
    bind_context(request_id="req-1", studio="s1", mode="m1")
    log = get_logger("tfields")
    log.info("hello", extra={"fields": {"k": 1}})
    log_path = tmp_path / date.today().isoformat() / "tfields.log"
    (record,) = _read_records(log_path)
    assert record["level"] == "INFO"
    assert record["subsystem"] == "tfields"
    assert record["request_id"] == "req-1"
    assert record["studio"] == "s1"
    assert record["mode"] == "m1"
    assert record["msg"] == "hello"
    assert record["k"] == 1
    assert record["ts"].startswith(date.today().isoformat())


@pytest.mark.unit
def test_per_subsystem_files_and_levels(tmp_path):
    configure_logging(log_dir=tmp_path, default_level="INFO", levels={"chatty": "ERROR"})
    get_logger("chatty").info("suppressed")
    get_logger("loud").info("kept")
    day = date.today().isoformat()
    assert not (tmp_path / day / "chatty.log").exists()
    assert (tmp_path / day / "loud.log").exists()


@pytest.mark.unit
def test_date_rollover_opens_new_file(tmp_path):
    days = [date(2026, 1, 1), date(2026, 1, 2)]
    handler = DatedFileHandler(tmp_path, "troll", date_fn=lambda: days.pop(0) if len(days) > 1 else days[0])
    logger = logging.getLogger("studio.troll-direct")
    logger.setLevel("INFO")
    logger.addHandler(handler)
    logger.propagate = False
    logger.info("day one")
    logger.info("day two")
    handler.close()
    assert (tmp_path / "2026-01-01" / "troll.log").exists()
    assert (tmp_path / "2026-01-02" / "troll.log").exists()
