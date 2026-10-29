# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for staged zone power plans and execution."""

from pathlib import Path

import pytest

from app.config.loader import load_all
from app.core.safety import SafetyViolation
from app.registry import Registry
from app.drivers.mock import MockDriver
from app.modules.power import PowerError, PowerModule
from app.registry import Registry

CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config"


@pytest.fixture()
def power():
    data = load_all(CONFIG_ROOT)
    registry = Registry.from_loaded(data["studios"]["example-studio"], data["profiles"])
    drivers = {plug: MockDriver(plug) for plug in ("plug-1", "plug-2", "plug-3")}
    return PowerModule("power", registry, drivers)


@pytest.mark.unit
def test_plan_on_order_ascending(power):
    plan = power.plan_zone_power("bench", True)
    assert [step["device"] for step in plan] == ["demo-cam", "demo-key"]
    assert all(step["on"] for step in plan)


@pytest.mark.unit
def test_plan_off_order_descending(power):
    plan = power.plan_zone_power("bench", False)
    assert [step["device"] for step in plan] == ["demo-key", "demo-cam"]


@pytest.mark.unit
def test_plan_delays_scale_with_watts(power):
    plan = {step["device"]: step["delay_after_s"] for step in power.plan_zone_power("bench", True)}
    assert plan["demo-cam"] == 2
    assert plan["demo-key"] == 2
    loaded = {
        "studio": {"id": "s", "label": "s"},
        "devices": [
            {"id": "mid", "zone": "z", "powerPlug": "plug-1", "bootOrder": 1, "estWatts": 350},
            {"id": "huge", "zone": "z", "powerPlug": "plug-2", "bootOrder": 2, "estWatts": 5000},
        ],
        "zones": [{"id": "z", "label": "z"}],
        "modes": [],
        "presets": {},
    }
    big = PowerModule("power", Registry.from_loaded(loaded, {}), power.drivers)
    delays = {step["device"]: step["delay_after_s"] for step in big.plan_zone_power("z", True)}
    assert delays == {"mid": 5, "huge": 10}


@pytest.mark.unit
def test_plan_off_refused_for_manual_only(power):
    with pytest.raises(SafetyViolation, match="demo-screen"):
        power.plan_zone_power("desk", False)


@pytest.mark.unit
def test_plan_missing_driver(power):
    del power.drivers["plug-1"]
    with pytest.raises(PowerError, match="plug-1"):
        power.plan_zone_power("bench", True)


@pytest.mark.unit
def test_execute_records_and_sleeps(power):
    sleeps = []
    results = power.set_zone_power("bench", True, sleep=sleeps.append)
    assert [item["device"] for item in results] == ["demo-cam", "demo-key"]
    assert all(item["ok"] for item in results)
    assert sleeps == [2, 2]
    assert power.drivers["plug-1"].state["on"] is True
