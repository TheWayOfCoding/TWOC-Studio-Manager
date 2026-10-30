# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit: GVM frame construction matches captured app bytes; validation holds."""

import sys
from types import SimpleNamespace

import pytest

from app.drivers.gvm_ble import (
    GvmBleDriver,
    GvmError,
    _resolve_control,
    brightness_frame,
    build_frame,
    cct_frame,
    crc_xmodem,
    page_frame,
    power_frame,
    scan_devices,
    scene_frame,
)
from app.drivers.mock import MockDriver
from app.modules.gvm import GvmModule, GvmModuleError, scene_risk


def test_crc_matches_captured_frames():
    assert crc_xmodem(bytes.fromhex("4c540900305700020110")) == 0x4EAF
    assert crc_xmodem(bytes.fromhex("4c540900305700030133")) == 0x6D9E
    assert crc_xmodem(bytes.fromhex("4c540900305700070103")) == 0x870D
    assert crc_xmodem(bytes.fromhex("4c540900305700000100")) == 0x32FE
    assert crc_xmodem(bytes.fromhex("4c540900305700000101")) == 0x22DF


def test_frames_match_captured_bytes():
    assert brightness_frame(16).hex() == "4c5409003057000201104eafff"
    assert cct_frame(5100).hex() == "4c5409003057000301336d9eff"
    assert scene_frame(3).hex() == "4c540900305700070103870dff"
    assert power_frame(False).hex() == "4c54090030570000010032feff"
    assert power_frame(True).hex() == "4c54090030570000010122dfff"
    assert page_frame("cct").hex() == "4c540900305700060101907fff"


def test_build_frame_rejects_non_bytes():
    for bad in (-1, 256, "x", None, True):
        with pytest.raises(GvmError):
            build_frame(bad, 1)
        with pytest.raises(GvmError):
            build_frame(2, bad)


def test_value_ranges_rejected():
    for bad in (0, 101, -5, "16", None):
        with pytest.raises(GvmError):
            brightness_frame(bad)
    for bad in (3100, 5700, 4350, 3200.0, None):
        with pytest.raises(GvmError):
            cct_frame(bad)
    for bad in (0, 19, -1, "3", None):
        with pytest.raises(GvmError):
            scene_frame(bad)
    with pytest.raises(GvmError):
        page_frame("bogus")


def test_cct_endpoints():
    assert cct_frame(3200)[9] == 0x20
    assert cct_frame(5600)[9] == 0x38


def test_driver_rejects_bad_commands_without_touching_ble():
    driver = GvmBleDriver("g", "AA:BB:CC:DD:EE:FF")
    assert driver.apply("power").ok is False
    assert driver.apply({}).ok is False
    assert driver.apply({"brightness": 500}).ok is False
    assert driver.apply({"power": "maybe"}).ok is False
    assert driver.capabilities()["family"] == "gvm"


def test_safe_stop_never_raises():
    result = GvmBleDriver("g", "AA:BB:CC:DD:EE:00").safe_stop()
    assert result.error is not None or result.ok


def test_scene_gate():
    assert scene_risk(3) == "intense"
    module = GvmModule("gvm", {"ble": MockDriver("mock-gvm")})
    with pytest.raises(GvmModuleError):
        module.set_scene(3)
    assert module.set_scene(3, allow_intense=True).ok
    assert module.describe()["photo_risk"]["scene"] == "intense-until-verified"


def test_scan_reads_rssi_from_advertisement_data(monkeypatch):
    """Bleak >= 1.0 removed BLEDevice.rssi; RSSI lives on the advertisement data."""
    captured = {}

    async def fake_discover(timeout, return_adv=False):
        captured["return_adv"] = return_adv
        device = SimpleNamespace(address="AA:BB:CC:DD:EE:01", name="BT_LED01")
        return {"AA:BB:CC:DD:EE:01": (device, SimpleNamespace(rssi=-60))}

    fake_bleak = SimpleNamespace(BleakScanner=SimpleNamespace(discover=fake_discover))
    monkeypatch.setitem(sys.modules, "bleak", fake_bleak)
    assert scan_devices(timeout=1) == [
        {"address": "AA:BB:CC:DD:EE:01", "name": "BT_LED01", "rssi": -60}
    ]
    assert captured["return_adv"] is True


def test_scan_falls_back_to_legacy_device_rssi(monkeypatch):
    """Old Bleak returned a bare device list with .rssi on each device."""
    async def fake_discover(timeout, return_adv=False):
        return [SimpleNamespace(address="AA:BB:CC:DD:EE:02", name=None, rssi=-70)]

    fake_bleak = SimpleNamespace(BleakScanner=SimpleNamespace(discover=fake_discover))
    monkeypatch.setitem(sys.modules, "bleak", fake_bleak)
    assert scan_devices(timeout=1) == [
        {"address": "AA:BB:CC:DD:EE:02", "name": None, "rssi": -70}
    ]


def test_resolve_control_matches_uuid_not_handle():
    char = SimpleNamespace(handle=16, uuid="00010203-0405-0607-0809-0a0b0c0d2b10")
    other = SimpleNamespace(handle=2, uuid="00002a00-0000-1000-8000-00805f9b34fb")
    service = SimpleNamespace(characteristics=[other, char])
    client = SimpleNamespace(services=[service])
    assert _resolve_control(client) == char.uuid


def test_resolve_control_missing_raises():
    client = SimpleNamespace(services=[SimpleNamespace(characteristics=[])])
    with pytest.raises(GvmError):
        _resolve_control(client)
