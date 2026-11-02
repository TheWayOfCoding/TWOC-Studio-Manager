# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static regression tests for the kiosk backdrop page (no JS harness)."""

from pathlib import Path

import pytest

KIOSK_HTML = Path(__file__).resolve().parents[2] / "backend" / "app" / "static" / "screen.html"
HUB_KIOSK = Path(__file__).resolve().parents[2] / "tools" / "hub-kiosk.py"
TAURI_CAP = (
    Path(__file__).resolve().parents[2]
    / "kiosk-shell"
    / "src-tauri"
    / "capabilities"
    / "main.json"
)


@pytest.mark.unit
def test_kiosk_feed_and_caption():
    """Kiosk polls the state feed and renders theme/text/audio."""
    html = KIOSK_HTML.read_text(encoding="utf-8")
    for marker in (
        "/api/screens/",
        'id="theme"',
        'id="text"',
        'id="audio"',
        "setInterval(tick, 500)",
    ):
        assert marker in html


@pytest.mark.unit
def test_hub_launcher_supports_tauri_shell():
    """install-app points the logon .bat at our fixed exe, not a browser."""
    helper = HUB_KIOSK.read_text(encoding="utf-8")
    for marker in (
        "install-app",
        "launch",
        "schtasks",
        "studio-kiosk.exe",
        "C:\\studio\\kiosk",
    ):
        assert marker in helper


@pytest.mark.unit
def test_kiosk_double_esc_exit():
    """Double-ESC asks the Tauri shell to close; browsers ignore it."""
    html = KIOSK_HTML.read_text(encoding="utf-8")
    for marker in (
        'id="exit"',
        "__TAURI_INTERNALS__",
        "plugin:window|close",
    ):
        assert marker in html
    assert "core:window:allow-close" in TAURI_CAP.read_text(encoding="utf-8")


@pytest.mark.unit
def test_kiosk_hides_cursor():
    """Cursor stays hidden on the fullscreen backdrop (keyboard exit unaffected)."""
    html = KIOSK_HTML.read_text(encoding="utf-8")
    assert "cursor: none" in html


@pytest.mark.unit
def test_kiosk_wash_and_candle_flicker():
    """Wash layer tints behind the caption; candle theme breathes."""
    html = KIOSK_HTML.read_text(encoding="utf-8")
    for marker in (
        'id="wash"',
        "s.wash",
        "flicker",
        's.theme === "candle"',
    ):
        assert marker in html
