# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static regression tests for the default-sets admin card (no JS harness)."""

from pathlib import Path

import pytest

EQUIPMENT_HTML = Path(__file__).resolve().parents[2] / "backend" / "app" / "static" / "equipment.html"


def _equipment_html():
    # File read, not HTTP: marker regression must run without a socket bind.
    return EQUIPMENT_HTML.read_text(encoding="utf-8")


@pytest.mark.unit
def test_equipment_default_sets_admin_present():
    """Admin card lists, creates, edits, deletes, and assigns default sets."""
    html = _equipment_html()
    for marker in (
        "Default sets",
        'id="ds-default"',
        'id="ds-list"',
        'id="ds-new-id"',
        'id="ds-new-devices"',
        'id="ds-new-plugs"',
        'id="ds-edit"',
        'id="ds-out"',
        "dsLoad()",
        "dsCreate()",
        "dsEdit(",
        "dsSave()",
        "dsDelete(",
        "dsAssign()",
        "dsClear()",
        "/defaults/assign",
        "window.confirm(",
        "await dsLoad()",
    ):
        assert marker in html


@pytest.mark.unit
def test_equipment_default_sets_builder_present():
    """Dropdown builder adds devices/plugs with range-aware inputs + quick assign."""
    html = _equipment_html()
    for marker in (
        'id="ds-add-device"',
        'id="ds-add-plug"',
        'id="ds-builder-new"',
        'id="ds-builder-edit"',
        'id="ds-plugs-new"',
        'id="ds-quick-prop"',
        'id="ds-quick-value"',
        'id="ds-strip-colors"',
        "dsBuilderAdd(",
        "dsBuilderAddAll(",
        "dsPlugAdd(",
        "dsQuickApply(",
        "dsRangesLoad()",
        "/ranges",
        "Add all controllable",
        "Apply to added lights",
        "Advanced JSON",
        "dsSlugLabel()",
        'id="ds-new-id-show"',
        'id="ds-screen-themes"',
        "dsChoiceRow(",
        'value="transport"',
        "fixed ",
        "no CCT control",
        "interface",
    ):
        assert marker in html


@pytest.mark.unit
def test_control_cct_slider_uses_shared_ranges():
    """Control page renders each fixture's own CCT span (no hardcoded copy)."""
    control = (EQUIPMENT_HTML.parent / "control.html").read_text(encoding="utf-8")
    for marker in ("cctBounds(", "/ranges", "this fixture's span",
                   "effFamily(", "sendTransport(", "refreshKeepSelection(",
                   "interface "):
        assert marker in control


@pytest.mark.unit
def test_equipment_per_device_reset_present():
    """Device card shows the saved default values plus a one-click reset."""
    html = _equipment_html()
    for marker in (
        "dsResetRow()",
        "eqReset()",
        "Reset to studio default",
        "no saved default for this asset",
        "twoc.control.",
        "/api/testbench/gvm/set",
        "/api/testbench/ninja/set",
        "/api/testbench/dmx/light",
        "/api/testbench/ir/send",
        "/api/testbench/screen/push",
    ):
        assert marker in html
