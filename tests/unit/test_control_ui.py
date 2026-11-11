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


def _function_slice(html, start_marker, end_marker):
    """Source slice of one page function (static structure test, no JS harness)."""
    start = html.index(start_marker)
    end = html.index(end_marker, start)
    return html[start:end]


@pytest.mark.unit
def test_control_brightness_leaves_gvm_page_alone():
    """Brightness must not force the CCT page: the panel dims in any mode."""
    html = _control_html()
    bright_fn = _function_slice(html, "async function sendBright(", "async function sendCct(")
    assert "/api/testbench/gvm/set" in bright_fn
    assert "page" not in bright_fn


@pytest.mark.unit
def test_control_cct_and_color_announce_gvm_page():
    """CCT and HSI actions each announce their own panel page."""
    html = _control_html()
    cct_fn = _function_slice(html, "async function sendCct(", "async function sendHsi(")
    assert 'page: "cct"' in cct_fn
    hsi_fn = _function_slice(html, "async function sendHsi(", "async function lightPower(")
    assert 'page: "hsi"' in hsi_fn


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
        '<option value="ocean"',
        "<hr>",
        "screenVolume()",
        "screenPlaying",
        "body.track",
        'sliderRow("c-vol", "Audio volume", 0, 100, 1, initVal(d.id, "volume", 100), "%"',
    ):
        assert marker in html


@pytest.mark.unit
def test_control_session_memory_and_default_fallback_present():
    """Card inputs seed from tab session, then the studio default set."""
    html = _control_html()
    for marker in (
        "sessionStorage",
        "twoc.control.",
        "sessRead(",
        "sessWrite(",
        "initVal(",
        "/defaults",
        "DEFAULT_SET",
        'initVal(d.id, "brightness", 5)',
        'initVal(d.id, "cct", 5600)',
        'initVal(d.id, "hue", 220)',
        'initVal(d.id, "saturation", 60)',
    ):
        assert marker in html


@pytest.mark.unit
def test_control_reset_actions_present():
    """One-click reset per asset, per plug, and everything-at-once."""
    html = _control_html()
    for marker in (
        "resetDevice()",
        "resetAll()",
        "plugReset(",
        "Reset to default",
        "Reset everything to studio default",
        'id="reset-all"',
        'id="reset-plugs"',
        "also reset Kasa plugs",
        "plugs held (tick the plug box)",
    ):
        assert marker in html


@pytest.mark.unit
def test_control_picker_lists_drivable_only():
    """Manual assets stay off the Control picker (details live on Equipment)."""
    html = _control_html()
    picker_fn = _function_slice(html, "async function loadDevices()", "function sliderRow(")
    assert "filter((d) => drivable(d))" in picker_fn
    assert "(manual)" not in picker_fn
    assert "disabled" not in picker_fn


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
