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
def test_hub_restart_card_present():
    """Hub-program restart: confirm-gated button calling /api/hub/restart."""
    html = _bench_html()
    for marker in (
        'id="hub-confirm"',
        'id="hub"',
        "hubRestart(",
        "/api/hub/restart",
        "Restart hub program",
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
        'id="link-target"',
        'value="obs_bench"',
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
        '<option value="piano"',
        '<option value="eurobeat"',
        '<option value="dance"',
        '<option value="synthwave"',
        '<option value="ocean"',
        'id="scr-vol-n"',
        "scrVolume()",
        "scrPlaying",
        'id="scr-vol" min="0" max="100" value="100"',
    ):
        assert marker in html
    assert 'type="text" id="scr-track"' not in html


@pytest.mark.unit
def test_bench_session_memory_and_default_fallback_present():
    """Bench device inputs seed from tab session, then the studio default set."""
    html = _bench_html()
    for marker in (
        "sessionStorage",
        "twoc.control.",
        "sessRead(",
        "sessWrite(",
        "initVal(",
        "ensureDefaults(",
        "BENCH_DEFAULTS",
        "/defaults",
    ):
        assert marker in html


@pytest.mark.unit
def test_bench_sends_record_session_state():
    """Bench device sends record what went out under Control's key scheme."""
    html = _bench_html()

    def _slice(start, end):
        return html[html.index(start) : html.index(end, html.index(start))]

    assert "sessWrite(" in _slice("async function gvmSet(", "async function gvmPower(")
    assert "sessWrite(" in _slice("async function ninjaSet(", "function ninjaBrSync(")
    assert "sessWrite(" in _slice("async function dmxLightSet(", "async function dmxLightLook(")
    assert "sessWrite(" in _slice("async function scrPush(", "async function scrClear(")
    assert "sessWrite(" in _slice("async function scrAudio(", "function scrVolume(")
    assert "sessWrite(" in _slice("async function irSendSel(", "async function irSendRaw(")
    assert "sessWrite(" in _slice("async function kOfficialSet(", "let kasaPlugs")


@pytest.mark.unit
def test_bench_inputs_seed_from_session_and_default():
    """Rendered inputs seed from session/default instead of hardcoded only."""
    html = _bench_html()

    def _slice(start, end):
        return html[html.index(start) : html.index(end, html.index(start))]

    assert "initVal(" in _slice("function dmxLightPanel(", "async function dmxLightsLoad(")
    assert "seedGvm(" in html
    assert "seedNinja(" in html
    assert "seedScr(" in html


@pytest.mark.unit
def test_bench_reset_actions_present():
    """One-click reset per device card, matching Control's contract."""
    html = _bench_html()
    for marker in (
        "gvmReset()",
        "ninjaReset()",
        "dmxFixtureReset(",
        "scrReset()",
        "irStripReset()",
        "kOfficialReset(",
        "Reset to default",
    ):
        assert marker in html


@pytest.mark.unit
def test_bench_gvm_color_pad_present():
    """GVM card gets the shared touch-friendly color pad, synced with inputs."""
    html = _bench_html()
    for marker in (
        'src="/colorpad.js"',
        'id="gvm-colorpad"',
        "ColorPad.mount(",
        "gvmPadSync(",
        "gvmColor(",
        "Apply color",
    ):
        assert marker in html


@pytest.mark.unit
def test_bench_gvm_color_applies_combined_look():
    """One tap under the pad sends hue+sat+brightness on the hsi page."""
    html = _bench_html()
    assert html.index('id="gvm-colorpad"') < html.index('onclick="gvmColor()"')
    start = html.index("async function gvmColor(")
    end = html.index("async function gvmScene(", start)
    fn = html[start:end]
    for marker in ('page: "hsi"', "brightness:", "hue:", "saturation:", "gvmSet("):
        assert marker in fn


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
