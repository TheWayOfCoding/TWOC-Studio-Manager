# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the Kasa driver with a faked python-kasa library."""

import pytest

import kasa
from app.drivers.kasa import KasaDriver, discover_plugs


class FakeDevice:
    fail_ack = False
    update_failures = 0

    def __init__(self, host, on=False):
        self.host = host
        self._on = on
        self.alias = f"plug@{host}"
        self.model = "HS103"

    @property
    def is_on(self):
        return self._on

    async def update(self):
        if FakeDevice.update_failures > 0:
            FakeDevice.update_failures -= 1
            raise Exception("Device response did not match our challenge")
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
    FakeDevice.update_failures = 0
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


@pytest.mark.unit
def test_first_contact_challenge_healed_by_retry():
    FakeDevice.update_failures = 1
    result = KasaDriver("k7", "192.0.2.17").get_state()
    assert result.ok
    assert result.state["model"] == "HS103"


@pytest.mark.unit
def test_discover_retries_flaky_update():
    FakeDevice.update_failures = 1
    FakeDiscover.devices = {"192.0.2.18": FakeDevice("192.0.2.18")}
    plugs = discover_plugs()
    assert plugs[0]["model"] == "HS103"
    assert plugs[0]["on"] is False


@pytest.mark.unit
def test_discover_heals_first_contact_via_single_fallback():
    """Broadcast update fumbles twice, but a fresh single-connect heals it.

    Regression for the bench: Discover showed challenge-mismatch on all
    plugs while Refresh-all (discover_single per host) went green.
    """
    FakeDevice.update_failures = 2
    FakeDiscover.devices = {"192.0.2.21": FakeDevice("192.0.2.21")}
    plugs = discover_plugs()
    assert plugs[0].get("error") is None
    assert plugs[0]["model"] == "HS103"
    assert plugs[0]["on"] is False


@pytest.mark.unit
def test_retry_exhaustion_reports_error():
    FakeDevice.update_failures = 99
    result = KasaDriver("k8", "192.0.2.19").get_state()
    assert not result.ok
    assert "challenge" in result.error


@pytest.mark.unit
def test_slow_failure_skips_retry(monkeypatch):
    import app.drivers.kasa as kasa_mod

    monkeypatch.setattr(kasa_mod, "KASA_RETRY_SLOW_S", 0)
    FakeDevice.update_failures = 1
    result = KasaDriver("k9", "192.0.2.20").get_state()
    assert not result.ok
    assert "challenge" in result.error


@pytest.mark.unit
def test_resolve_host_prefers_live_alias():
    """Alias resolution returns the live host, ignoring the stored one."""
    import app.drivers.kasa as kasa_mod

    kasa_mod._alias_cache.clear()
    FakeDiscover.devices = {"192.0.2.21": FakeDevice("192.0.2.21")}
    assert kasa_mod.resolve_host("plug@192.0.2.21", fallback="192.0.2.99") == "192.0.2.21"


@pytest.mark.unit
def test_resolve_host_falls_back_to_stored():
    """An unseen alias keeps the stored host (offline-safe)."""
    import app.drivers.kasa as kasa_mod

    kasa_mod._alias_cache.clear()
    FakeDiscover.devices = {}
    assert kasa_mod.resolve_host("Studio Plug 1", fallback="192.0.2.99") == "192.0.2.99"


@pytest.mark.unit
def test_resolve_host_caches_discovery():
    """One broadcast refreshes every alias; repeats come from the cache."""
    import app.drivers.kasa as kasa_mod

    kasa_mod._alias_cache.clear()
    FakeDiscover.devices = {"192.0.2.22": FakeDevice("192.0.2.22")}
    assert kasa_mod.resolve_host("plug@192.0.2.22", fallback="192.0.2.99") == "192.0.2.22"
    FakeDiscover.devices = {}  # gone from the LAN, still cached
    assert kasa_mod.resolve_host("plug@192.0.2.22", fallback="192.0.2.99") == "192.0.2.22"
