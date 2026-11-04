# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static regression tests for the kiosk page + Tauri shell contract.

No JS harness in this repo, so these pin the page markers and the
page/shell agreement instead: the double-ESC close path, its loud
failure feedback, and the shell capability that actually authorizes it.
A repeat of the Nov 2 incident (close silently denied on the remote
kiosk URL) must fail the suite, not the glass.
"""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCREEN_HTML = ROOT / "backend" / "app" / "static" / "screen.html"
CAPABILITY = ROOT / "kiosk-shell" / "src-tauri" / "capabilities" / "main.json"
TAURI_CONF = ROOT / "kiosk-shell" / "src-tauri" / "tauri.conf.json"
MAIN_RS = ROOT / "kiosk-shell" / "src-tauri" / "src" / "main.rs"


def _screen_html():
    # File read, not HTTP: marker regression must run without a socket bind.
    return SCREEN_HTML.read_text(encoding="utf-8")


@pytest.mark.unit
def test_double_esc_close_path_present():
    """The Tauri escape hatch: two ESC presses within 2s close the window."""
    html = _screen_html()
    for marker in (
        "escAt",
        'e.key !== "Escape"',
        "window.__TAURI_INTERNALS__",
        "plugin:window|close",
        '{ label: "main" }',
        "ESC again to exit",
        "now - escAt < 2000",
    ):
        assert marker in html


@pytest.mark.unit
def test_close_failure_is_loud():
    """A denied/failed close must say so on the glass, never fail silent."""
    html = _screen_html()
    for marker in (
        "close denied (",
        "close failed (",
        ".catch(",
    ):
        assert marker in html


@pytest.mark.unit
def test_shell_authorizes_remote_close():
    """The shell capability grants window:close to the kiosk origin.

    The page loads from a remote URL (hub:8001), not the bundled dist —
    without the remote scope the runtime denies the close and ESC dies
    silently. This locks the page/shell agreement in the suite.
    """
    capability = json.loads(CAPABILITY.read_text(encoding="utf-8"))
    assert "core:window:allow-close" in capability.get("permissions", [])
    assert "main" in capability.get("windows", [])
    urls = (capability.get("remote") or {}).get("urls", [])
    assert "http://localhost:8001/*" in urls


@pytest.mark.unit
def test_shell_default_url_matches_kiosk_origin():
    """Shell default URL, window URL, and capability scope agree on 8001."""
    conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
    window_urls = [w.get("url", "") for w in conf.get("app", {}).get("windows", [])]
    assert window_urls and all("http://localhost:8001/" in url for url in window_urls)
    assert 'DEFAULT_URL: &str = "http://localhost:8001/screen/kqc-screen"' in MAIN_RS.read_text(encoding="utf-8")


@pytest.mark.unit
def test_kiosk_themes_and_remote_audio_start_present():
    """Curated theme looks + audio that arms without a glass click.

    The shell passes --autoplay-policy=no-user-gesture-required, so the
    page must also try to create/resume its AudioContext when a play
    arrives (it used to only create one inside the click handler, which
    made bench/control Play bed a no-op nobody could hear).
    """
    html = _screen_html()
    for marker in (
        "THEME_BG",
        '"live": "#2a1a1a"',
        '"work": "#1a2a1a"',
        '"focus":',
        '"warm":',
        '"candle":',
        '"sunset":',
        '"ocean":',
        '"slate":',
        '"rose":',
        "flicker 5s",
        "ensureAudio()",
        "actx.resume()",
        '"piano"',
        "startPiano",
        '"eurobeat"',
        "startEurobeat",
        '"dance"',
        "startDance",
        '"synthwave"',
        "startSynthwave",
    ):
        assert marker in html
    conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
    window = conf["app"]["windows"][0]
    assert "no-user-gesture-required" in window.get("additionalBrowserArgs", "")


@pytest.mark.unit
def test_kiosk_animation_scenes_present():
    """Looping vector scenes: canvas renderer, fireplace + fish tank.

    Fish wander (stateful steering, never the same path twice); the
    fireplace is a pure function of a 60s loop clock, so it repeats
    seamlessly forever.
    """
    html = _screen_html()
    for marker in (
        'id="anim"',
        "requestAnimationFrame",
        "LOOP_S = 60",
        "drawFireplace",
        "drawFishtank",
        "drawUnderwater",
        "drawStarfield",
        "drawNebula",
        "drawGalaxy",
        "drawCode",
        "drawArcade",
        "drawGears",
        "drawDice",
        "spikeStar",
        "startOcean",
        "startSpace",
        "startLofi",
        "startChip",
        "wander",
        "startAnim",
        "stopAnim",
    ):
        assert marker in html


@pytest.mark.unit
def test_kiosk_liveness_contract_present():
    """Poll loop, boot-id self-reload, and scheme-agnostic API fetch."""
    html = _screen_html()
    for marker in (
        "setInterval(tick, 500)",
        "seenBoot",
        "location.reload()",
        'fetch("/api/screens/"',
    ):
        assert marker in html
