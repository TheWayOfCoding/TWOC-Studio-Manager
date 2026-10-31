# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the screens scaffold (in-memory state, no sockets)."""

import pytest

from app.drivers.screen import SCREEN_STATE, ScreenDriver
from app.modules.screens import ScreensModule


@pytest.fixture(autouse=True)
def clean_state():
    SCREEN_STATE.clear()
    yield
    SCREEN_STATE.clear()


@pytest.fixture()
def module():
    return ScreensModule("screens", {"back": ScreenDriver("screen-back", "back")})


@pytest.mark.unit
def test_push_roundtrip(module):
    result = module.push("back", theme="stream_starting", text="Hello")
    assert result.ok is True
    assert result.state == {"screen": "back", "theme": "stream_starting", "text": "Hello"}
    assert module.drivers["back"].get_state().state["text"] == "Hello"


@pytest.mark.unit
def test_push_needs_content(module):
    assert module.push("back").ok is False


@pytest.mark.unit
def test_audio_transport(module):
    played = module.audio("back", "play", track="lofi")
    assert played.ok is True
    assert played.state["track"] == "lofi"
    stopped = module.audio("back", "stop")
    assert stopped.state["audio"] == "stop"
    assert "track" not in stopped.state
    assert module.audio("back", "pause").ok is False


@pytest.mark.unit
def test_safe_stop_keeps_theme(module):
    module.push("back", theme="work")
    module.audio("back", "play")
    stopped = module.drivers["back"].safe_stop()
    assert stopped.state == {"screen": "back", "theme": "work", "audio": "stop"}


@pytest.mark.unit
def test_apply_mapping_unknown_screen(module):
    results = module.apply_mapping({"back": {"theme": "live"}, "nope": {"theme": "x"}})
    assert results["back"].ok is True
    assert results["nope"].ok is False
    assert "nope" in results["nope"].error
