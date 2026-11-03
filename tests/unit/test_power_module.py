# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for zone power plans and execution (no staging pauses)."""

from pathlib import Path

import pytest

from app.config.loader import load_all
from app.core.safety import SafetyViolation
from app.registry import Registry
from app.drivers.mock import MockDriver
from app.modules.power import PowerError, PowerModule, UnknownPowerGroupError
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
def test_plan_carries_no_delays(power):
    plan = power.plan_zone_power("bench", True)
    assert all("delay_after_s" not in step for step in plan)
    assert [step["device"] for step in plan] == ["demo-cam", "demo-key"]


@pytest.mark.unit
def test_plan_off_refused_for_manual_only(power):
    with pytest.raises(SafetyViolation, match="demo-screen"):
        power.plan_zone_power("desk", False)


@pytest.mark.unit
def test_plan_all_off_skips_manual_only(power):
    plan, skipped = power.plan_all_power(False)
    assert sorted(step["plug"] for step in plan) == ["plug-1", "plug-2"]
    assert skipped == [{"device": "demo-screen", "plug": "plug-3", "reason": "manual-only (tick the box to include)"}]


@pytest.mark.unit
def test_plan_all_off_include_manual(power):
    plan, skipped = power.plan_all_power(False, include_manual=True)
    assert sorted(step["plug"] for step in plan) == ["plug-1", "plug-2", "plug-3"]
    assert skipped == []


@pytest.mark.unit
def test_plan_all_off_never_always_skipped(power):
    power.registry._devices["demo-cam"]["powerOffPolicy"] = "never"
    plan, skipped = power.plan_all_power(False, include_manual=True)
    assert "plug-2" not in [step["plug"] for step in plan]
    assert skipped == [{"device": "demo-cam", "plug": "plug-2", "reason": "never off programmatically"}]


@pytest.mark.unit
def test_plan_missing_driver(power):
    del power.drivers["plug-1"]
    with pytest.raises(PowerError, match="plug-1"):
        power.plan_zone_power("bench", True)


@pytest.mark.unit
def test_execute_records_without_sleeping(power):
    sleeps = []
    results = power.set_zone_power("bench", True, sleep=sleeps.append)
    assert [item["device"] for item in results] == ["demo-cam", "demo-key"]
    assert all(item["ok"] for item in results)
    assert sleeps == []
    assert power.drivers["plug-1"].state["on"] is True


@pytest.mark.unit
def test_execute_delay_pauses_between_steps_only(power):
    sleeps = []
    results = power.set_zone_power("bench", True, sleep=sleeps.append, delay_s=0.5)
    assert all(item["ok"] for item in results)
    assert sleeps == [0.5]
    solo = power.set_zone_power("desk", True, sleep=sleeps.append, delay_s=0.5)
    assert all(item["ok"] for item in solo)
    assert sleeps == [0.5]


@pytest.mark.unit
def test_plan_group_power_mirrors_zone(power):
    plan, protected = power.plan_group_power("bench-power", True)
    assert [step["device"] for step in plan] == ["demo-cam", "demo-key"]
    assert [step["plug"] for step in plan] == ["plug-2", "plug-1"]
    assert protected == []


@pytest.mark.unit
def test_plan_group_off_skips_manual_only(power):
    plan, protected = power.plan_group_power("desk-power", False)
    assert plan == []
    assert protected == [
        {"device": "demo-screen", "plug": "plug-3", "reason": "manual-only (tick the box to include)"}
    ]


@pytest.mark.unit
def test_plan_group_off_include_manual(power):
    plan, protected = power.plan_group_power("desk-power", False, include_manual=True)
    assert [step["device"] for step in plan] == ["demo-screen"]
    assert protected == []


@pytest.mark.unit
def test_plan_group_off_never_always_skipped(power):
    power.registry._devices["demo-cam"]["powerOffPolicy"] = "never"
    plan, protected = power.plan_group_power("bench-power", False, include_manual=True)
    assert "plug-2" not in [step["plug"] for step in plan]
    assert protected == [{"device": "demo-cam", "plug": "plug-2", "reason": "never off programmatically"}]


@pytest.mark.unit
def test_plan_group_unknown(power):
    with pytest.raises(UnknownPowerGroupError, match="nope-group"):
        power.plan_group_power("nope-group", True)


@pytest.mark.unit
def test_set_group_power_executes(power):
    results = power.set_group_power("desk-power", True, sleep=lambda s: None)
    assert [(item["device"], item["ok"]) for item in results] == [("demo-screen", True)]
    assert power.drivers["plug-3"].state["on"] is True


@pytest.mark.unit
def test_status_isolates_failing_driver(power):
    """Module.status reports one bad driver without hiding the rest."""

    class Boom:
        def get_state(self):
            raise RuntimeError("relay jammed")

    power.drivers["plug-9"] = Boom()
    status = power.status()
    assert status["module"] == "power"
    assert status["drivers"]["plug-1"]["ok"] is True
    assert status["drivers"]["plug-9"]["ok"] is False
    assert "jammed" in status["drivers"]["plug-9"]["error"]
