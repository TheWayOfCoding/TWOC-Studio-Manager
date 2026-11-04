# SPDX-License-Identifier: AGPL-3.0-or-later
"""Golden regression: the example studio always loads to the same shape."""

from pathlib import Path

import pytest

from app.config.loader import load_all

CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config"


@pytest.mark.regression
def test_example_studio_snapshot():
    data = load_all(CONFIG_ROOT)
    assert "example-studio" in data["studios"]
    assert sorted(data["profiles"]) == [
        "bytech-strip",
        "crapy-pt-15b",
        "demo-flood",
        "demo-rgb",
        "gvm-1500d",
        "viltrox-ninja-10",
        "viltrox-ninja-20",
        "viltrox-ninja-400",
    ]
    loaded = data["studios"]["example-studio"]
    assert [d["id"] for d in loaded["devices"]] == ["demo-key", "demo-rgb", "demo-cam", "demo-screen"]
    assert [z["id"] for z in loaded["zones"]] == ["bench", "desk"]
    assert [m["id"] for m in loaded["modes"]] == ["demo_work", "demo_stream"]
    assert sorted(loaded["presets"]) == ["default"]
    assert loaded["studio"]["defaultMode"] == "demo_work"


@pytest.mark.regression
def test_example_studio_registry_order_is_alphabetical_without_custom_order():
    """No ordering.json: registry lists fall back to id order (golden)."""
    from app.registry import Registry

    data = load_all(CONFIG_ROOT)
    registry = Registry.from_loaded(data["studios"]["example-studio"], data["profiles"])
    assert registry.ordering == {}
    assert registry.device_ids == ["demo-cam", "demo-key", "demo-rgb", "demo-screen"]
    assert registry.zone_ids == ["bench", "desk"]
    assert registry.mode_ids == ["demo_stream", "demo_work"]
    assert [d["id"] for d in registry.devices_in_zone("bench")] == ["demo-cam", "demo-key", "demo-rgb"]
