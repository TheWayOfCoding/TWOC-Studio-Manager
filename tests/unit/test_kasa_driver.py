# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the Kasa driver with a faked python-kasa library."""

import pytest

import kasa
from app.drivers.kasa import KasaDriver, discover_plugs


class FakeDevice:
    fail_ack = False

    def __init__(self, host, on=False):
        self.host = host
        self._on = on
        self.alias = f"plug@{host}"
        self.model = "HS103"

    @property
    def is_on(self):
        return self._on

    async def update(self):
        return None

    async def turn_on(self):
        self._on = True
        if FakeDevice.fail_ack:
            raise Exception("Communication error on system:set_relay_state")
        return {}

    async def turn_off(self):
        self._on = False
        if FakeDevice.fail_ack:
            raise Exception("Communication error on system:set_relay_state")
        return {}


class FakeDiscover:
    devices = {}
    missing = False

    @staticmethod
    async def discover_single(host, **kwargs):
        if FakeDiscover.missing:
            return None
        return FakeDiscover.devices.setdefault(host, FakeDevice(host))

    @staticmethod
    async def discover(**kwargs):
        return dict(FakeDiscover.devices)


@pytest.fixture(autouse=True)
def fake_kasa(monkeypatch):
    FakeDiscover.devices = {}
    FakeDiscover.missing = False
    FakeDevice.fail_ack = False
    monkeypatch.setattr(kasa, "Discover", FakeDiscover)
    yield


@pytest.mark.unit
def test_get_state_shape():
    result = KasaDriver("k1", "192.0.2.11").get_state()
    assert result.ok
    assert result.state["power"] == "off"
    assert result.state["model"] == "HS103"


@pytest.mark.unit
def test_apply_on_off_forms():
    driver = KasaDriver("k2", "192.0.2.12")
    assert driver.apply({"on": True}).state["power"] == "on"
    assert driver.apply({"power": "off"}).state["power"] == "off"


@pytest.mark.unit
def test_apply_invalid_command():
    driver = KasaDriver("k3", "192.0.2.13")
    assert not driver.apply({"blink": True}).ok
    assert not driver.apply(["on"]).ok


@pytest.mark.unit
def test_missing_device_reports_error():
    FakeDiscover.missing = True
    result = KasaDriver("k4", "192.0.2.14").get_state()
    assert not result.ok
    assert "192.0.2.14" in result.error


@pytest.mark.unit
def test_safe_stop_changes_nothing():
    driver = KasaDriver("k5", "192.0.2.15")
    driver.apply({"on": True})
    result = driver.safe_stop()
    assert result.ok
    assert result.state["power"] == "on"


@pytest.mark.unit
def test_ack_quirk_verified_by_reread():
    FakeDevice.fail_ack = True
    result = KasaDriver("k6", "192.0.2.16").apply({"on": True})
    assert result.ok
    assert result.state["power"] == "on"
    assert "re-read" in result.state["note"]


@pytest.mark.unit
def test_discover_lists_plugs_sorted():
    FakeDiscover.devices = {"192.0.2.9": FakeDevice("192.0.2.9"), "192.0.2.7": FakeDevice("192.0.2.7")}
    plugs = discover_plugs()
    assert [plug["host"] for plug in plugs] == ["192.0.2.7", "192.0.2.9"]
    assert plugs[0]["model"] == "HS103"
