# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static regression tests for the Control page structure (no JS harness)."""

from pathlib import Path

import pytest

CONTROL_HTML = Path(__file__).resolve().parents[2] / "backend" / "app" / "static" / "control.html"


def _control_html():
    # File read, not HTTP: marker regression must run without a socket bind.
    return CONTROL_HTML.read_text(encoding="utf-8")


@pytest.mark.unit
def test_control_picker_and_card_present():
    """Studio + alphabetical asset picker with a per-device card and readout."""
    html = _control_html()
    for marker in (
        'id="studio"',
        'id="device"',
        'id="card"',
        'id="out"',
        "loadStudios(",
        "loadDevices(",
        "renderCard(",
        "/api/studios",
        "/devices",
    ):
        assert marker in html


@pytest.mark.unit
def test_control_light_actions_present():
    """Lights get power, brightness, CCT, and HSI actions (no scenes)."""
    html = _control_html()
    for marker in (
        "lightPower(",
        "lightBright()",
        "lightCct()",
        "lightHsi()",
        "/api/testbench/gvm/set",
        "/api/testbench/ninja/set",
        "/api/testbench/dmx/light",
        'id="c-bright"',
    ):
        assert marker in html
    assert '"scene"' not in html  # scenes stay bench-only (D-PHOTO)
    assert "set_scene" not in html


@pytest.mark.unit
def test_control_screen_theme_select_ttl_and_clear_present():
    """Control screens get the same curated themes, text timer, Clear."""
    html = _control_html()
    for marker in (
        '<select id="c-theme">',
        '<option value="live"',
        '<option value="work"',
        '<option value="focus"',
        '<option value="warm"',
        '<option value="candle"',
        '<option value="sunset"',
        '<option value="ocean"',
        '<option value="slate"',
        '<option value="rose"',
        'id="c-ttl"',
        'id="c-anim"',
        '<option value="fireplace"',
        '<option value="fishtank"',
        "screenClear()",
        "text_ttl_s",
    ):
        assert marker in html
    assert 'type="text" id="c-theme"' not in html


@pytest.mark.unit
def test_control_screen_track_select_and_live_volume_present():
    """Control screens pick a track and adjust volume while it plays."""
    html = _control_html()
    for marker in (
        'id="c-track"',
        '<option value="piano"',
        '<option value="eurobeat"',
        '<option value="dance"',
        '<option value="synthwave"',
        '<option value="tone"',
        "screenVolume()",
        "screenPlaying",
        "body.track",
        'sliderRow("c-vol", "Audio volume", 0, 100, 1, 100, "%"',
    ):
        assert marker in html


@pytest.mark.unit
def test_control_strip_and_screen_present():
    """Strip gets on/off + fixed-color swatches; screens get push + audio."""
    html = _control_html()
    for marker in (
        "irSend(",
        "loadSwatches()",
        "/api/testbench/ir/send",
        "/api/testbench/ir/codes",
        "screenPush()",
        "screenAudio(",
        "/api/testbench/screen/push",
        "/api/testbench/screen/audio",
    ):
        assert marker in html
