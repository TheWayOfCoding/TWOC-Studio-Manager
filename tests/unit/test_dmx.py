# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the USB-DMX driver (fake serial handle, no hardware)."""

import time

import pytest

from app.drivers import dmx_usb
from app.drivers.dmx_usb import DmxError, DmxUsbDriver, dmx_for_cct, dmx_for_percent


class FakeHandle:
    """Recording stand-in for a pyserial handle."""

    last_open = None

    def __init__(self, port="COM4"):
        self.port = port
        self.breaks = []
        self.writes = []
        self.break_condition = False
        self.rts = False
        self.closed = False
        FakeHandle.last_open = self

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def flush(self):
        pass

    def close(self):
        self.closed = True


@pytest.fixture()
def fake_port(monkeypatch):
    def fake_open(port, timeout=2.0):
        assert port == "COM4"
        return FakeHandle(port)

    monkeypatch.setattr(dmx_usb, "open_port", fake_open)
    monkeypatch.setattr(dmx_usb.time, "sleep", lambda s: None)
    return FakeHandle


@pytest.fixture()
def driver():
    return DmxUsbDriver("dmx", port="COM4", frames_per_apply=2)


@pytest.mark.unit
def test_apply_merges_and_frames(fake_port, driver):
    result = driver.apply({"channels": {"1": 64, "3": 12}})
    assert result.ok is True
    assert result.state["channels"] == {"1": 64, "3": 12}
    handle = FakeHandle.last_open
    assert len(handle.writes) == 2
    assert len(handle.writes[0]) == 513
    assert handle.writes[0][0] == 0
    assert handle.writes[0][1] == 64
    assert handle.writes[0][3] == 12
    assert handle.closed is True


@pytest.mark.unit
def test_apply_single_channel_form(fake_port, driver):
    assert driver.apply({"channel": 20, "value": 77}).ok is True
    assert driver.channels == {20: 77}


@pytest.mark.unit
def test_apply_rejects_out_of_range(fake_port, driver):
    assert driver.apply({"channel": 0, "value": 1}).ok is False
    assert driver.apply({"channel": 513, "value": 1}).ok is False
    assert driver.apply({"channel": 1, "value": 256}).ok is False
    assert driver.apply({"nonsense": True}).ok is False
    assert driver.channels == {}


@pytest.mark.unit
def test_blackout_holds_buffer(fake_port, driver):
    driver.apply({"channel": 1, "value": 64})
    assert driver.apply({"blackout": True}).state["blackout"] is True
    assert FakeHandle.last_open.writes[-1][1] == 0
    assert driver.channels == {1: 64}
    assert driver.apply({"blackout": False}).state["blackout"] is False
    assert FakeHandle.last_open.writes[-1][1] == 64


@pytest.mark.unit
def test_safe_stop_blackouts(fake_port, driver):
    driver.apply({"channel": 2, "value": 40})
    assert driver.safe_stop().state == {"port": "COM4", "blackout": True}
    assert FakeHandle.last_open.writes[-1][2] == 0


@pytest.mark.unit
def test_probe_opens_without_emitting(fake_port, driver):
    assert driver.probe().state == {"port": "COM4", "open": True, "universe": 0}
    assert FakeHandle.last_open.writes == []


@pytest.mark.unit
def test_hold_streams_until_deadline(fake_port, driver):
    assert driver.hold({5: 10}, 0) == 0
    assert driver.hold({5: 10}, 0.05) >= 1
    assert FakeHandle.last_open.writes[-1][5] == 10


@pytest.mark.unit
def test_second_apply_preserves_unmentioned_channels(fake_port, driver):
    """Transitions merge: setting ch3 never blanks ch1 (no dip to zero)."""
    driver.apply({"channel": 1, "value": 64})
    result = driver.apply({"channel": 3, "value": 12})
    assert result.ok is True
    frame = FakeHandle.last_open.writes[-1]
    assert (frame[1], frame[3]) == (64, 12)


@pytest.mark.unit
def test_fade_ramps_without_blackout(fake_port, driver):
    driver.apply({"channels": {"1": 10, "20": 10}})
    sent = driver.fade({"1": 20, "20": 0}, 0.2, fps=10)
    assert sent == 3
    frames = FakeHandle.last_open.writes[-3:]
    assert [frame[1] for frame in frames] == [10, 15, 20]
    assert [frame[20] for frame in frames] == [10, 5, 0]
    assert driver.channels == {1: 20, 20: 0}


@pytest.mark.unit
def test_fade_clears_blackout(fake_port, driver):
    driver.apply({"channel": 1, "value": 30})
    driver.apply({"blackout": True})
    assert driver.fade({"1": 30}, 0) == 1
    assert driver.blackout is False
    assert FakeHandle.last_open.writes[-1][1] == 30


@pytest.mark.unit
def test_fade_rejects_bad_target(fake_port, driver):
    with pytest.raises(DmxError):
        driver.fade({"1": 999}, 1)
    with pytest.raises(DmxError):
        driver.fade("nope", 1)
    with pytest.raises(DmxError, match="units"):
        driver.fade({"1": 5}, 1, units="kelvin")
    with pytest.raises(DmxError, match="percent"):
        driver.fade({"1": 101}, 1, units="percent")


@pytest.mark.unit
def test_fade_percent_covers_every_shown_value(fake_port, driver):
    driver.apply({"channel": 1, "value": 3})
    driver.fade({"1": 10}, 0.5, fps=40, units="percent")
    shown = sorted({min(100, frame[1] * 100 // 255) for frame in FakeHandle.last_open.writes})
    assert shown == list(range(1, 11))
    assert driver.channels == {1: 26}


@pytest.mark.unit
def test_fade_percent_dwells_evenly_per_shown_value(fake_port, driver):
    driver.apply({"channel": 1, "value": 3})
    driver.fade({"1": 3}, 0.3, fps=30, units="percent")
    values = [frame[1] for frame in FakeHandle.last_open.writes]
    assert values == [3] * 4 + [6] * 3 + [8] * 3


@pytest.mark.unit
def test_dmx_for_percent_matches_display_floor():
    assert dmx_for_percent(0) == 0
    assert dmx_for_percent(1) == 3
    assert dmx_for_percent(10) == 26
    assert dmx_for_percent(100) == 255


@pytest.mark.unit
def test_dmx_for_cct_linear_over_range():
    assert dmx_for_cct(2500, 2500, 8500) == 0
    assert dmx_for_cct(8500, 2500, 8500) == 255
    assert dmx_for_cct(5500, 2500, 8500) == 128
    assert dmx_for_cct(2000, 2500, 8500) == 0
    assert dmx_for_cct(9000, 2500, 8500) == 255
    with pytest.raises(DmxError):
        dmx_for_cct(5000, 8500, 2500)


@pytest.mark.unit
def test_fade_tracks_wall_clock(monkeypatch):
    monkeypatch.setattr(dmx_usb, "open_port", lambda port, timeout=2.0: FakeHandle(port))
    driver = DmxUsbDriver("dmx", port="COM4")
    start = time.time()
    sent = driver.fade({"1": 26}, 0.4, fps=20)
    elapsed = time.time() - start
    assert sent == 9
    assert 0.35 <= elapsed < 1.0


@pytest.mark.unit
def test_fade_dwell_steps_each_percent_level(fake_port, driver):
    driver.apply({"channel": 1, "value": 3})
    sent = driver.fade({"1": 3}, 0, units="percent", dwell_s=0)
    assert sent == 3
    assert [frame[1] for frame in FakeHandle.last_open.writes[-3:]] == [3, 6, 8]
    assert driver.channels == {1: 8}


@pytest.mark.unit
def test_fade_dwell_walks_every_dmx_value(fake_port, driver):
    driver.apply({"channel": 1, "value": 10})
    sent = driver.fade({"1": 13}, 0, dwell_s=0)
    assert sent == 4
    assert [frame[1] for frame in FakeHandle.last_open.writes[-4:]] == [10, 11, 12, 13]
    assert driver.channels == {1: 13}


@pytest.mark.unit
def test_fade_dwell_holds_short_channel_at_final(fake_port, driver):
    driver.apply({"channels": {"1": 10, "20": 5}})
    sent = driver.fade({"1": 12, "20": 5}, 0, dwell_s=0)
    assert sent == 3
    frames = FakeHandle.last_open.writes[-3:]
    assert [frame[1] for frame in frames] == [10, 11, 12]
    assert [frame[20] for frame in frames] == [5, 5, 5]


@pytest.mark.unit
def test_fade_dwell_rejects_bad_timing(fake_port, driver):
    with pytest.raises(DmxError, match="seconds or dwell"):
        driver.fade({"1": 5}, 1, dwell_s=0.5)
    with pytest.raises(DmxError, match="dwell"):
        driver.fade({"1": 5}, 0, dwell_s=-1)


@pytest.mark.unit
def test_fade_dwell_tracks_wall_clock(monkeypatch):
    monkeypatch.setattr(dmx_usb, "open_port", lambda port, timeout=2.0: FakeHandle(port))
    driver = DmxUsbDriver("dmx", port="COM4")
    driver.apply({"channel": 1, "value": 10})
    start = time.time()
    sent = driver.fade({"1": 12}, 0, dwell_s=0.05)
    elapsed = time.time() - start
    assert sent >= 3  # each step re-emits for its whole hold (lossy USB link)
    assert sorted({frame[1] for frame in FakeHandle.last_open.writes}) == [10, 11, 12]
    assert driver.channels == {1: 12}
    assert 0.12 <= elapsed < 0.6


@pytest.mark.unit
def test_display_percent_matches_fixture_floor():
    assert dmx_usb.display_percent(0) == 0
    assert dmx_usb.display_percent(25) == 9
    assert dmx_usb.display_percent(26) == 10
    assert dmx_usb.display_percent(255) == 100


@pytest.mark.unit
def test_cct_for_dmx_inverts_dmx_for_cct():
    assert dmx_usb.cct_for_dmx(0, 2500, 8500) == 2500
    assert dmx_usb.cct_for_dmx(255, 2500, 8500) == 8500
    assert dmx_usb.cct_for_dmx(132, 2500, 8500) == 5606
    with pytest.raises(DmxError):
        dmx_usb.cct_for_dmx(0, 8500, 2500)


@pytest.mark.unit
def test_bench_driver_cached_per_port():
    from app.api import dmx as dmx_api

    dmx_usb._shared_drivers.clear()
    try:
        first = dmx_api._driver(dmx_api.PortBody(port="COM9"))
        second = dmx_api._driver(dmx_api.PortBody(port="COM9"))
        other = dmx_api._driver(dmx_api.PortBody(port="COM10"))
        assert first is second
        assert first is not other
        assert dmx_usb.get_shared_driver("COM9") is first
    finally:
        dmx_usb._shared_drivers.clear()
