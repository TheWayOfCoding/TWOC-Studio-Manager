# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static regression tests for the Test Bench page structure (no JS harness)."""

from pathlib import Path

import pytest

BENCH_HTML = Path(__file__).resolve().parents[2] / "backend" / "app" / "static" / "bench.html"


def _bench_html():
    # File read, not HTTP: marker regression must run without a socket bind.
    return BENCH_HTML.read_text(encoding="utf-8")


@pytest.mark.unit
def test_kasa_list_survives_toggle():
    """The plug list lives in its own container; results go elsewhere."""
    html = _bench_html()
    assert 'id="kasa-list"' in html
    assert 'id="kasa-out"' in html
    assert 'out("kasa",' not in html  # the old list-wiping result call


@pytest.mark.unit
def test_kasa_row_actions_present():
    """Rows keep On/Off/State actions plus list-level refresh."""
    html = _bench_html()
    for marker in ("kSet(", "kState(", "kRefreshAll()", "renderKasaList()"):
        assert marker in html


@pytest.mark.unit
def test_obs_screen_cards_present():
    """Scaffold cards exist with their readout containers and API calls."""
    html = _bench_html()
    for marker in (
        'id="obs"',
        'id="scr"',
        "obsSwitch(",
        "obsStream(",
        "scrPush()",
        "/api/testbench/obs/switch",
        "/api/testbench/screen/push",
        'value="kqc-screen"',
    ):
        assert marker in html


@pytest.mark.unit
def test_shows_card_present():
    """Presentations card lists, runs (with scene confirm), stops, polls."""
    html = _bench_html()
    for marker in (
        'id="show-studio"',
        'id="show-sel"',
        'id="show-confirm"',
        "showList()",
        "showRun()",
        "showStop()",
        "showStatus()",
        "/shows/",
    ):
        assert marker in html


@pytest.mark.unit
def test_gvm_hsi_controls_present():
    """GVM card drives hue/saturation and announces the hsi page."""
    html = _bench_html()
    for marker in (
        'id="gvm-hue"',
        'id="gvm-sat"',
        "gvmHue()",
        "gvmSat()",
        '<option value="hsi">hsi</option>',
    ):
        assert marker in html


@pytest.mark.unit
def test_dmx_fixture_ui_present():
    """DMX card leads with per-light panels; raw channels stay for bring-up."""
    html = _bench_html()
    for marker in (
        'id="dmx"',
        'id="dmx-lights"',
        "dmxLightsLoad(",
        "dmxLightSet(",
        "dmxLightLook(",
        "/api/testbench/dmx/fixtures",
        "/api/testbench/dmx/light",
        "allow-bright",
        "dmxBrightOk(",
        "dmxWhole(",
        "dwell_s",
        "per step",
        "dmxSweep()",
        "dmxBlackout(",
        "/api/testbench/dmx/set",
    ):
        assert marker in html


@pytest.mark.unit
def test_post_helper_tolerates_non_json():
    assert "(non-JSON reply" in _bench_html()


@pytest.mark.unit
def test_link_card_present():
    """PC-link card wraps TX + RX in one-click buttons (WoL stays API-only)."""
    html = _bench_html()
    for marker in (
        'id="link"',
        'id="link-cmd"',
        "linkDevices()",
        "linkInputs()",
        "linkTest()",
        "linkSend()",
        "linkSendWait()",
        "linkListen()",
        "/api/link/devices",
        "/api/link/inputs",
        "/api/link/test",
        "/api/link/send",
        "/api/link/send-and-wait",
        "/api/link/listen",
        'id="link-tx"',
        'id="link-rx"',
        "linkPortsLoad()",
        "linkPortsSave()",
        "/api/link/config",
        'id="link-studio"',
        'id="link-scene"',
        'id="link-collection"',
        'id="link-boot-human"',
        "linkBoot()",
        "linkBootStatus()",
        "linkBootStop()",
        "renderBootHuman(",
        "/obs/boot",
    ):
        assert marker in html
    assert "/api/link/wol" not in html


@pytest.mark.unit
def test_screen_theme_select_ttl_and_clear_present():
    """Theme is a curated dropdown; text can be timed and cleared."""
    html = _bench_html()
    for marker in (
        '<select id="scr-theme">',
        '<option value="live"',
        '<option value="work"',
        '<option value="focus"',
        '<option value="warm"',
        '<option value="candle"',
        '<option value="sunset"',
        '<option value="ocean"',
        '<option value="slate"',
        '<option value="rose"',
        'id="scr-ttl"',
        '<select id="scr-anim">',
        '<option value="fireplace"',
        '<option value="fishtank"',
        "scrClear()",
        "text_ttl_s",
    ):
        assert marker in html
    assert 'type="text" id="scr-theme"' not in html


@pytest.mark.unit
def test_screen_track_select_and_live_volume_present():
    """Track is a picker (not a text box); volume adjusts while playing."""
    html = _bench_html()
    for marker in (
        '<select id="scr-track">',
        '<option value="bed"',
        '<option value="piano"',
        '<option value="eurobeat"',
        '<option value="dance"',
        '<option value="synthwave"',
        '<option value="tone"',
        'id="scr-vol-n"',
        "scrVolume()",
        "scrPlaying",
        'id="scr-vol" min="0" max="100" value="100"',
    ):
        assert marker in html
    assert 'type="text" id="scr-track"' not in html


@pytest.mark.unit
def test_screen_audio_volume_controls_present():
    """Screen card drives bed volume (uncapped: the synth runs quiet)."""
    html = _bench_html()
    for marker in (
        'id="scr-vol"',
        "scrAudio(",
        "/api/testbench/screen/audio",
    ):
        assert marker in html
    assert "scr-loud" not in html
