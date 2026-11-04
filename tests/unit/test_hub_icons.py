# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static checks for the hub desktop Start/Stop shortcuts (no hub needed)."""

from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"


@pytest.mark.unit
def test_hub_stop_also_pauses_watchdog():
    """A stopped hub must stay stopped: watchdog would revive it in minutes."""
    text = (TOOLS / "hub-stop.cmd").read_text(encoding="utf-8")
    assert "/End /TN StudioHub" in text
    assert "StudioHubWatchdog" in text
    assert "/DISABLE" in text


@pytest.mark.unit
def test_hub_start_reenables_watchdog_and_waits_for_health():
    text = (TOOLS / "hub-start.cmd").read_text(encoding="utf-8")
    assert "StudioHubWatchdog" in text
    assert "/ENABLE" in text
    assert "/Run /TN StudioHub" in text
    assert "/api/health" in text


@pytest.mark.unit
def test_hub_icons_installer_desktop_and_start_menu():
    text = (TOOLS / "hub-icons.ps1").read_text(encoding="utf-8")
    for marker in (
        "Start Studio Hub",
        "Stop Studio Hub",
        "hub-start.cmd",
        "hub-stop.cmd",
        "WScript.Shell",
        "Start Menu",
        "Desktop",
        "-bor 32",  # run-as-administrator flag on the .lnk
    ):
        assert marker in text
