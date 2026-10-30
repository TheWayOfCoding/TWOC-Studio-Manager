# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hardware: GVM BLE probe + dim CCT trial on the Hub host (needs Bleak + OS Bluetooth)."""

import os

import pytest

from app.drivers.gvm_ble import CONTROL_UUID, GvmBleDriver, scan_devices

pytestmark = pytest.mark.hardware


def _need_hw():
    if os.environ.get("STUDIO_HW") != "1":
        pytest.skip("hardware tests run on the Hub host with STUDIO_HW=1")


def test_scan_sees_panel():
    _need_hw()
    devices = scan_devices(timeout=10)
    names = [(dev["address"], dev.get("name")) for dev in devices]
    print("ble:", names)
    assert any((dev.get("name") or "").startswith("BT_LED") for dev in devices), (
        "no BT_LED panel advertising; panel must be on and in APP mode, "
        "and the phone app must be disconnected (one BLE link at a time)"
    )


def test_probe_finds_control_handle():
    _need_hw()
    address = os.environ.get("GVM_ADDRESS")
    if not address:
        pytest.skip("set GVM_ADDRESS to the panel MAC to enable the probe test")
    result = GvmBleDriver("gvm", address).probe()
    assert result.ok, result.error
    assert result.state["control_uuid"] is not None, "control characteristic missing"


def test_dim_cct_trial():
    _need_hw()
    address = os.environ.get("GVM_ADDRESS")
    if not address:
        pytest.skip("set GVM_ADDRESS to the panel MAC to enable the trial test")
    driver = GvmBleDriver("gvm", address)
    assert CONTROL_UUID == "00010203-0405-0607-0809-0a0b0c0d2b10"
    dim = driver.apply({"brightness": 5})
    assert dim.ok, dim.error
    warm = driver.apply({"cct": 4300})
    assert warm.ok, warm.error
    print("trial:", dim.state, warm.state)
