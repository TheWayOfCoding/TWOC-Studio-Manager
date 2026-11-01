# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the DMX per-light adapter (stub universe, no hardware)."""

import pytest

from app.drivers.base import DriverResult
from app.drivers.dmx_light import DmxLightDriver
from app.drivers.dmx_usb import cct_for_dmx, dmx_for_cct, dmx_for_percent


class StubUniverse:
    """Recording stand-in for a shared DmxUsbDriver."""

    def __init__(self):
        self.channels = {}
        self.calls = []

    def apply(self, command):
        self.calls.append(("apply", command))
        self.channels.update(command.get("channels") or {})
        return DriverResult(ok=True, state={"channels": dict(self.channels)})

    def fade(self, target, seconds, units="dmx", dwell_s=None):
        self.calls.append(("fade", dict(target), seconds, units, dwell_s))
        if units == "percent":
            self.channels.update({ch: dmx_for_percent(pct) for ch, pct in target.items()})
        else:
            self.channels.update(dict(target))
        return 1

    def get_state(self):
        return DriverResult(ok=True, state={"channels": {str(ch): v for ch, v in self.channels.items()}})


@pytest.fixture()
def universe():
    return StubUniverse()


@pytest.fixture()
def key(universe):
    return DmxLightDriver("dmx-key", universe, dimmer=1, cct=2, cct_range=[2500, 8500])


@pytest.mark.unit
def test_apply_merges_power_and_color_in_one_call(key, universe):
    result = key.apply({"intensity": 5, "cct": 5600})
    assert result.ok is True
    assert universe.calls == [
        ("apply", {"channels": {1: dmx_for_percent(5), 2: dmx_for_cct(5600, 2500, 8500)}})
    ]


@pytest.mark.unit
def test_apply_power_key_rejected_with_hint(key):
    result = key.apply({"power": "off"})
    assert result.ok is False
    assert "intensity 0" in result.error


@pytest.mark.unit
def test_apply_cct_without_channel_rejected(universe):
    fill = DmxLightDriver("dmx-fill", universe, dimmer=20)
    result = fill.apply({"cct": 5600})
    assert result.ok is False
    assert "CCT" in result.error
    assert universe.calls == []


@pytest.mark.unit
def test_fade_steps_power_then_color(key, universe):
    result = key.fade({"intensity": 5, "cct": 5600}, 0.5)
    assert result.ok is True
    assert universe.calls == [
        ("fade", {1: 5}, 0, "percent", 0.5),
        ("fade", {2: dmx_for_cct(5600, 2500, 8500)}, 0, "dmx", 0.5),
    ]


@pytest.mark.unit
def test_get_state_reads_universe_buffer(key, universe):
    universe.channels = {1: dmx_for_percent(5), 2: dmx_for_cct(5600, 2500, 8500)}
    state = key.get_state().state
    assert state["intensity"] == 5
    assert state["cct"] == cct_for_dmx(dmx_for_cct(5600, 2500, 8500), 2500, 8500)
