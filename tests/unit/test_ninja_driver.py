# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the Ninja BLE driver (frame vectors from live captures)."""

import pytest

from app.drivers.ninja_ble import (
    MockAdvertiser,
    NinjaBleDriver,
    NinjaError,
    WinRtAdvertiser,
    address_byte,
    build_look,
    build_scene,
    manufacturer_payload,
    scene_index,
)


def test_address_byte_verified_points():
    assert address_byte(3, "A") == 0x19
    assert address_byte(3, "B") == 0x1A
    assert address_byte(3, "C") == 0x1B
    assert address_byte(5, "A") == 0x29
    assert address_byte(3, "a") == 0x19


def test_address_byte_rejects():
    for bad in (0, 20, True, "3", None):
        with pytest.raises(NinjaError):
            address_byte(bad, "A")
    for bad in ("", "G", "AA", None, 1):
        with pytest.raises(NinjaError):
            address_byte(3, bad)


def test_build_look_capture_vector():
    """Run7 power-off broadcast, byte-exact (counter 0x5c)."""
    payload = build_look(3, "A", 0, cct=5500, gm=0, preset=3, counter=0x5C)
    assert payload.hex() == "1aff4c000215ef1900370003000a0000000002fe5c00000a006ec5"


def test_build_look_preset_defaults():
    assert build_look(3, "A", 5, cct=5500)[11] == 0x03  # tile match -> index
    assert build_look(3, "A", 5, cct=4500)[11] == 0x64  # slider value -> custom
    assert build_look(3, "A", 5, cct=5500, preset=0)[11] == 0x00  # explicit wins


def test_build_look_validates():
    with pytest.raises(NinjaError):
        build_look(3, "A", 101)
    with pytest.raises(NinjaError):
        build_look(3, "A", True)
    with pytest.raises(NinjaError):
        build_look(3, "A", 5, cct=5550)
    with pytest.raises(NinjaError):
        build_look(3, "A", 5, cct=2400)
    with pytest.raises(NinjaError):
        build_look(3, "A", 5, gm=11)
    with pytest.raises(NinjaError):
        build_look(3, "A", 5, preset=6)


def test_manufacturer_payload_keeps_beacon_prefix():
    """Phone capture proved fixtures need the 0215 bytes on air."""
    frame = build_look(3, "D", 5, cct=5500, gm=0, preset=3, counter=0)
    assert manufacturer_payload(frame).hex() == "0215ef1c00370503000a0000000002fe0005000a006ec5"
    with pytest.raises(NinjaError):
        manufacturer_payload(b"short")


def test_scene_index_names_and_numbers():
    assert scene_index(0) == 0
    assert scene_index(9) == 9
    assert scene_index("TV") == 9
    assert scene_index("cct loop") == 8
    with pytest.raises(NinjaError):
        scene_index(10)
    with pytest.raises(NinjaError):
        scene_index("disco")


def test_build_scene_capture_vector():
    """Run5 TV scene on CH5-A, byte-exact (counter 0x3b)."""
    payload = build_scene(5, "A", "TV", 10, counter=0x3B)
    assert payload.hex() == "1aff4c000215ef2902090a0209000000000202fe3b0a000a006ec5"


def test_driver_merges_look_and_counts():
    driver = NinjaBleDriver("n", 3, "A", backend="mock", repeats=2)
    assert driver.apply({"brightness": 7}).ok
    result = driver.apply({"cct": 5500})
    assert result.ok
    assert driver.look == {"brightness": 7, "cct": 5500, "gm": 0, "preset": None, "scene": None}
    assert [s["counter"] for s in result.state["sends"]] == [2, 3]
    assert len(driver.advertiser.published) == 4


def test_driver_counter_wraps():
    driver = NinjaBleDriver("n", 3, "A", backend="mock", repeats=1)
    driver.counter = 0xFF
    result = driver.apply({"brightness": 5})
    assert [s["counter"] for s in result.state["sends"]] == [0xFF]
    assert driver.counter == 0x00


def test_driver_power_off_on_restores():
    driver = NinjaBleDriver("n", 3, "A", backend="mock", repeats=1)
    driver.apply({"brightness": 7, "cct": 5500})
    off = driver.apply({"power": "off"})
    assert bytes.fromhex(off.state["sends"][0]["payload"])[10] == 0
    on = driver.apply({"power": "on"})
    assert bytes.fromhex(on.state["sends"][0]["payload"])[10] == 7
    assert driver.apply({"power": "maybe"}).ok is False


def test_driver_pristine_needs_brightness_once():
    driver = NinjaBleDriver("n", 3, "A", backend="mock", repeats=1)
    result = driver.apply({"cct": 5500})
    assert result.ok is False
    assert "brightness unknown" in result.error
    assert driver.advertiser.published == []
    assert driver.apply({"brightness": 7, "cct": 5500}).ok is True
    assert driver.apply({"cct": 5600}).ok is True  # later partials fine


def test_driver_pristine_allows_power_and_raw():
    assert NinjaBleDriver("n", 3, "A", backend="mock", repeats=1).apply({"power": "on"}).ok
    good = "1aff4c000215ef1900370703000a0000000002fe5c07000a006ec5"
    assert NinjaBleDriver("n", 3, "A", backend="mock", repeats=1).apply({"raw": good}).ok


def test_driver_raw_and_rejects():
    driver = NinjaBleDriver("n", 3, "A", backend="mock", repeats=1)
    good = "1aff4c000215ef1900370703000a0000000002fe5c07000a006ec5"
    assert driver.apply({"raw": good}).ok
    assert driver.apply({"raw": "zz"}).ok is False
    assert driver.apply({"raw": "00"}).ok is False
    assert driver.apply({"nope": 1}).ok is False
    assert driver.apply("x").ok is False


def test_driver_probe_state_never_transmit():
    driver = NinjaBleDriver("n", 3, "A", backend="mock")
    assert driver.probe().ok
    assert driver.get_state().ok
    assert isinstance(driver.advertiser, MockAdvertiser)
    assert driver.advertiser.published == []


class _FakePublisher:
    """Stand-in for the WinRT publisher (no radio, no winsdk needed)."""

    def __init__(self, status="started", fail_start=None):
        self.status = status
        self.fail_start = fail_start
        self.stopped = False

    def start(self):
        if self.fail_start is not None:
            raise self.fail_start

    def stop(self):
        self.stopped = True


def test_winrt_attempt_reports_mode(monkeypatch):
    import app.drivers.ninja_ble as ninja_ble

    adv = WinRtAdvertiser()
    monkeypatch.setattr(adv, "_new_publisher", lambda a, e, n: _FakePublisher())
    assert adv._attempt(None, b"x" * 27, 0, True, True)["mode"] == "extended"
    assert adv._attempt(None, b"x" * 27, 0, False, False)["mode"] == "legacy"


def test_winrt_attempt_abort_raises(monkeypatch):
    adv = WinRtAdvertiser()
    monkeypatch.setattr(adv, "_new_publisher", lambda a, e, n: _FakePublisher(status="aborted"))
    with pytest.raises(NinjaError, match="aborted"):
        adv._attempt(None, b"x" * 27, 0, False, False)


def test_publish_falls_back_to_legacy(monkeypatch):
    import app.drivers.ninja_ble as ninja_ble

    adv = WinRtAdvertiser()
    monkeypatch.setattr(ninja_ble, "winrt_available", lambda: None)
    monkeypatch.setattr(adv, "_build_adv", lambda payload: None)
    calls = []

    def fake_attempt(adv_obj, payload, hold_s, extended, anonymous):
        calls.append((extended, anonymous))
        if extended or anonymous:
            raise NinjaError("advertiser failed to start: not supported")
        return {"mode": "legacy"}

    monkeypatch.setattr(adv, "_attempt", fake_attempt)
    result = adv.publish(b"x" * 27, 0)
    assert result["mode"] == "legacy"
    assert "fallback" in result
    assert calls == [(True, True), (False, False)]


def test_publish_reports_double_failure(monkeypatch):
    import app.drivers.ninja_ble as ninja_ble

    adv = WinRtAdvertiser()
    monkeypatch.setattr(ninja_ble, "winrt_available", lambda: None)
    monkeypatch.setattr(adv, "_build_adv", lambda payload: None)
    monkeypatch.setattr(
        adv, "_attempt",
        lambda a, p, h, e, n: (_ for _ in ()).throw(NinjaError("down")),
    )
    with pytest.raises(NinjaError, match="legacy retry also failed"):
        adv.publish(b"x" * 27, 0)


def test_driver_safe_stop_darkens():
    driver = NinjaBleDriver("n", 3, "A", backend="mock", repeats=1)
    driver.apply({"brightness": 9})
    result = driver.safe_stop()
    assert result.ok
    assert bytes.fromhex(result.state["sends"][0]["payload"])[10] == 0


def test_ctor_seeds_default_cct():
    driver = NinjaBleDriver("n", 6, "A", backend="mock", default_cct=5600)
    assert driver.get_state().state["look"]["cct"] == 5600
    assert driver.get_state().state["transmitted"] is False


def test_ctor_rejects_bad_default_cct():
    with pytest.raises(NinjaError):
        NinjaBleDriver("n", 6, "B", backend="mock", default_cct=5550)


def test_seed_applies_while_pristine_only():
    driver = NinjaBleDriver("n", 6, "C", backend="mock")
    driver.seed_default_cct(5600)
    assert driver.look["cct"] == 5600
    driver.apply({"brightness": 7})
    driver.seed_default_cct(4500)  # transmitted look wins
    assert driver.look["cct"] == 5600


def test_safe_stop_carries_default_cct():
    driver = NinjaBleDriver("n", 6, "D", backend="mock", repeats=1, default_cct=5600)
    payload = bytes.fromhex(driver.safe_stop().state["sends"][0]["payload"])
    assert payload[9] == 56  # fixed-daylight temp, brightness 0 still means off
    assert payload[10] == 0


def test_apply_many_merges_one_broadcast():
    driver = NinjaBleDriver("n", 3, "A", backend="mock")
    outcomes = driver.apply_many([{"brightness": 6}, {"cct": 3200}])
    assert len(outcomes) == 2
    assert all(outcome.ok for outcome in outcomes)
    assert len(driver.advertiser.published) == 2  # repeats of ONE look, not two
    assert driver.look["brightness"] == 6
    assert driver.look["cct"] == 3200


def test_apply_many_empty_sends_nothing():
    driver = NinjaBleDriver("n", 3, "A", backend="mock")
    assert driver.apply_many([]) == []
    assert driver.advertiser.published == []
