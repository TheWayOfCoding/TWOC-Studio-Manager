# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for config loading and validation."""

from pathlib import Path

import pytest

from app.config.loader import ConfigError, check_references, list_studios, load_all, load_studio, validate

CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config"


@pytest.mark.unit
def test_list_studios():
    assert list_studios(CONFIG_ROOT) == [{"id": "example-studio", "label": "Example Studio", "source": "studios"}]


@pytest.mark.unit
def test_load_studio_counts():
    loaded = load_studio(CONFIG_ROOT, "example-studio")
    assert len(loaded["devices"]) == 4
    assert len(loaded["zones"]) == 2
    assert len(loaded["modes"]) == 2
    assert set(loaded["presets"]) == {"default"}


@pytest.mark.unit
def test_unknown_studio():
    with pytest.raises(ConfigError, match="unknown studio"):
        load_studio(CONFIG_ROOT, "nope")


@pytest.mark.unit
def test_invalid_device_rejected():
    with pytest.raises(ConfigError):
        validate({"id": "x"}, "device", "test")


@pytest.mark.unit
def test_bad_zone_reference_rejected():
    data = load_all(CONFIG_ROOT)
    loaded = data["studios"]["example-studio"]
    loaded["devices"][0]["zone"] = "nope"
    with pytest.raises(ConfigError, match="unknown zone"):
        check_references(loaded, data["profiles"], "example-studio")


@pytest.mark.unit
def test_bad_profile_reference_rejected():
    data = load_all(CONFIG_ROOT)
    loaded = data["studios"]["example-studio"]
    loaded["devices"][0]["profile"] = "nope"
    with pytest.raises(ConfigError, match="unknown profile"):
        check_references(loaded, data["profiles"], "example-studio")
