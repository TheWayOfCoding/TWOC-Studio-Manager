# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the registry addressable view."""

from pathlib import Path

import pytest

from app.config.loader import load_all
from app.registry import Registry, UnknownDeviceError, UnknownZoneError

CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config"


@pytest.fixture()
def registry():
    data = load_all(CONFIG_ROOT)
    return Registry.from_loaded(data["studios"]["example-studio"], data["profiles"])


@pytest.mark.unit
def test_ids(registry):
    assert registry.device_ids == ["demo-cam", "demo-key", "demo-rgb", "demo-screen"]
    assert registry.zone_ids == ["bench", "desk"]
    assert registry.mode_ids == ["demo_stream", "demo_work"]
    assert registry.preset_ids == ["default"]


@pytest.mark.unit
def test_effective_device_merges_profile(registry):
    merged = registry.effective_device("demo-key")
    assert merged["name"] == "Demo key light"
    assert merged["spec"]["maxWatts"] == 60
    assert merged["capabilities"]["cct"] is True
    assert merged["effects"] == [{"name": "steady", "photoRisk": "none"}]


@pytest.mark.unit
def test_effective_device_without_profile(registry):
    merged = registry.effective_device("demo-cam")
    assert merged["spec"] == {}
    assert merged["effects"] == []
    assert merged["exposure"]["iso"] == 400


@pytest.mark.unit
def test_unknown_device(registry):
    with pytest.raises(UnknownDeviceError):
        registry.get_device("nope")


@pytest.mark.unit
def test_devices_in_zone(registry):
    assert sorted(d["id"] for d in registry.devices_in_zone("bench")) == ["demo-cam", "demo-key", "demo-rgb"]
    assert [d["id"] for d in registry.devices_in_zone("desk")] == ["demo-screen"]
    with pytest.raises(UnknownZoneError):
        registry.devices_in_zone("nope")
