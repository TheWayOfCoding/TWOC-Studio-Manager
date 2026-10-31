# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the modes engine: plan-then-apply across power/lights/OBS/screens."""

from pathlib import Path

import pytest

from app.config.loader import load_all
from app.core.safety import SafetyViolation
from app.drivers.base import DriverResult
from app.drivers.mock import MockDriver
from app.modules.power import PowerModule, UnknownPowerGroupError
from app.modules.modes import ModesError, ModesModule
from app.registry import Registry, UnknownDeviceError, UnknownModeError, UnknownPresetError

CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config"


def make_registry(modes=(), devices=(), zones=(), presets=None):
    loaded = {
        "studio": {"id": "s", "label": "s"},
        "devices": list(devices),
        "zones": list(zones),
        "modes": list(modes),
        "presets": dict(presets or {}),
    }
    return Registry.from_loaded(loaded, {})


@pytest.fixture()
def example_registry():
    data = load_all(CONFIG_ROOT)
    return Registry.from_loaded(data["studios"]["example-studio"], data["profiles"])


@pytest.fixture()
def modes(example_registry):
    plugs = {plug: MockDriver(plug) for plug in ("plug-1", "plug-2", "plug-3")}
    power = PowerModule("power", example_registry, plugs)
    lights = {device: MockDriver(device, initial_state={}) for device in ("demo-key", "demo-rgb", "demo-screen")}
    obs_calls = []
    screens_calls = []

    def obs(scene):
        obs_calls.append(scene)
        return DriverResult(ok=True, state={"scene": scene})

    def screens(mapping):
        screens_calls.append(dict(mapping))
        return {screen: DriverResult(ok=True, state={"theme": spec.get("theme")}) for screen, spec in mapping.items()}

    module = ModesModule("modes", example_registry, power=power, lights=lights, obs=obs, screens=screens)
    module.test_obs_calls = obs_calls
    module.test_screens_calls = screens_calls
    return module


@pytest.mark.unit
def test_apply_demo_stream_ready(modes):
    result = modes.apply_mode("demo_stream", sleep=lambda s: None)
    assert result["mode"] == "demo_stream"
    assert result["kind"] == "mode"
    statuses = {name: sub["status"] for name, sub in result["subsystems"].items()}
    assert statuses == {"power": "ready", "lights": "ready", "obs": "ready", "screens": "ready"}
    assert result["ok"] is True
    assert modes.test_obs_calls == ["Demo - Main"]
    assert modes.test_screens_calls == [{"demo-screen": {"theme": "stream_starting"}}]
    assert modes.power.drivers["plug-1"].state["on"] is True
    assert modes.lights["demo-key"].state == {"intensity": 50, "cct": 4600}
    assert modes.lights["demo-rgb"].state == {"intensity": 25, "hue": 220}
    assert modes.log[-1]["id"] == "demo_stream"


@pytest.mark.unit
def test_apply_unwired_subsystems_skipped_and_named(example_registry):
    module = ModesModule("modes", example_registry)
    result = module.apply_mode("demo_stream", sleep=lambda s: None)
    subs = result["subsystems"]
    assert subs["power"]["status"] == "skipped"
    assert "bench-power" in subs["power"]["skipped"][0]["reason"]
    assert subs["lights"]["status"] == "skipped"
    assert "demo-key" in subs["lights"]["skipped"][0]["reason"]
    assert subs["obs"]["status"] == "skipped"
    assert subs["obs"]["skipped"][0]["target"] == "Demo - Main"
    assert subs["screens"]["status"] == "skipped"
    assert subs["screens"]["skipped"][0]["target"] == "demo-screen"
    assert result["ok"] is True


@pytest.mark.unit
def test_apply_unknown_mode_raises(modes):
    with pytest.raises(UnknownModeError, match="nope"):
        modes.apply_mode("nope", sleep=lambda s: None)


@pytest.mark.unit
def test_apply_unknown_device_raises_before_moving(modes):
    modes.registry._modes["bad"] = {
        "id": "bad",
        "label": "Bad",
        "lights": {"ghost-light": {"intensity": 10}},
    }
    with pytest.raises(UnknownDeviceError, match="ghost-light"):
        modes.apply_mode("bad", sleep=lambda s: None)
    assert "on" not in modes.power.drivers["plug-1"].state


@pytest.mark.unit
def test_failing_plug_degrades_but_mode_continues(modes):
    modes.power.drivers["plug-1"] = MockDriver("plug-1", fail_on=("on",))
    result = modes.apply_mode("demo_stream", sleep=lambda s: None)
    power = result["subsystems"]["power"]
    assert power["status"] == "degraded"
    assert any(not item["ok"] for item in power["applied"])
    assert modes.power.drivers["plug-2"].state["on"] is True
    assert result["subsystems"]["lights"]["status"] == "ready"
    assert result["ok"] is False


@pytest.mark.unit
def test_missing_drivers_degraded_and_named(modes):
    del modes.power.drivers["plug-3"]
    del modes.lights["demo-rgb"]
    result = modes.apply_mode("demo_stream", sleep=lambda s: None)
    power = result["subsystems"]["power"]
    assert power["status"] == "degraded"
    assert any("desk-power" in item["reason"] for item in power["skipped"])
    lights = result["subsystems"]["lights"]
    assert lights["status"] == "degraded"
    assert any("demo-rgb" in item["reason"] for item in lights["skipped"])
    assert modes.lights["demo-key"].state["intensity"] == 50


@pytest.mark.unit
def test_out_of_range_light_rejected_before_moving(modes):
    modes.registry._modes["bad"] = {
        "id": "bad",
        "label": "Bad",
        "lights": {"demo-key": {"intensity": 150}},
    }
    with pytest.raises(SafetyViolation, match="intensity"):
        modes.apply_mode("bad", sleep=lambda s: None)
    assert modes.lights["demo-key"].state == {}
    assert "on" not in modes.power.drivers["plug-1"].state


@pytest.mark.unit
def test_scene_key_refused(modes):
    modes.registry._modes["bad"] = {
        "id": "bad",
        "label": "Bad",
        "lights": {"demo-key": {"scene": 3}},
    }
    with pytest.raises(SafetyViolation, match="scene"):
        modes.apply_mode("bad", sleep=lambda s: None)


@pytest.mark.unit
def test_capability_mismatch_skips_key(modes):
    modes.registry._modes["mismatch"] = {
        "id": "mismatch",
        "label": "Mismatch",
        "lights": {"demo-key": {"intensity": 40, "hue": 180}},
    }
    result = modes.apply_mode("mismatch", sleep=lambda s: None)
    lights = result["subsystems"]["lights"]
    assert lights["status"] == "degraded"
    assert lights["skipped"] == [{"target": "demo-key.hue", "reason": "demo-key cannot do hue (rgb unsupported)"}]
    assert modes.lights["demo-key"].state == {"intensity": 40}


@pytest.mark.unit
def test_uncontrollable_device_skipped(modes):
    modes.registry._modes["cam"] = {
        "id": "cam",
        "label": "Cam",
        "lights": {"demo-cam": {"power": "on"}, "demo-key": {"intensity": 10}},
    }
    modes.lights["demo-cam"] = MockDriver("demo-cam")
    result = modes.apply_mode("cam", sleep=lambda s: None)
    lights = result["subsystems"]["lights"]
    assert lights["status"] == "degraded"
    assert any("demo-cam" in item["reason"] for item in lights["skipped"])
    assert modes.lights["demo-key"].state == {"intensity": 10}


@pytest.mark.unit
def test_poweroff_policy_aborts_before_moving(modes):
    modes.registry._modes["off"] = {
        "id": "off",
        "label": "Off",
        "powerOn": ["bench-power"],
        "powerOff": ["desk-power"],
    }
    with pytest.raises(SafetyViolation, match="demo-screen"):
        modes.apply_mode("off", sleep=lambda s: None)
    assert "on" not in modes.power.drivers["plug-1"].state


@pytest.mark.unit
def test_unknown_power_group_raises(modes):
    modes.registry._modes["bad"] = {"id": "bad", "label": "Bad", "powerOn": ["nope-group"]}
    with pytest.raises(UnknownPowerGroupError, match="nope-group"):
        modes.apply_mode("bad", sleep=lambda s: None)


@pytest.mark.unit
def test_gvm_keys_translated_per_key(modes):
    modes.registry._devices["demo-key"]["driver"] = "gvm"
    result = modes.apply_mode("demo_work", sleep=lambda s: None)
    assert result["subsystems"]["lights"]["status"] == "ready"
    assert modes.lights["demo-screen"].state == {"power": "on"}
    modes.registry._modes["g"] = {
        "id": "g",
        "label": "G",
        "lights": {"demo-key": {"intensity": 30, "cct": 5000}},
    }
    modes.apply_mode("g", sleep=lambda s: None)
    assert modes.lights["demo-key"].state == {"brightness": 30, "cct": 5000}
    calls = [call for call in modes.lights["demo-key"].calls if call[0] == "apply"]
    assert [sorted(call[1]) for call in calls] == [["brightness"], ["cct"]]


@pytest.mark.unit
def test_unknown_driver_family_degraded(modes):
    modes.registry._devices["demo-screen"]["driver"] = "dmx"
    result = modes.apply_mode("demo_work", sleep=lambda s: None)
    lights = result["subsystems"]["lights"]
    assert lights["status"] == "degraded"
    assert any("dmx" in item["reason"] for item in lights["skipped"])


@pytest.mark.unit
def test_apply_preset_lights_ready(modes):
    result = modes.apply_preset("default", sleep=lambda s: None)
    assert result["kind"] == "preset"
    assert result["subsystems"]["lights"]["status"] == "ready"
    assert modes.lights["demo-key"].state == {"intensity": 50, "cct": 4600}
    assert modes.lights["demo-rgb"].state == {"intensity": 25, "hue": 220, "saturation": 60}
    assert result["subsystems"]["power"]["status"] == "skipped"


@pytest.mark.unit
def test_apply_unknown_preset_raises(modes):
    with pytest.raises(UnknownPresetError, match="nope"):
        modes.apply_preset("nope", sleep=lambda s: None)


@pytest.mark.unit
def test_failing_obs_degraded(modes):
    modes.obs = lambda scene: DriverResult(ok=False, error="obs down")
    result = modes.apply_mode("demo_stream", sleep=lambda s: None)
    obs = result["subsystems"]["obs"]
    assert obs["status"] == "degraded"
    assert obs["applied"][0]["error"] == "obs down"
    assert result["ok"] is False


@pytest.mark.unit
def test_plan_custom_validates_like_modes(modes):
    plan = modes.plan_custom("ai-x", {"lights": {"demo-key": {"intensity": 30}}})
    assert plan["kind"] == "custom"
    assert plan["lights"]["devices"][0]["device"] == "demo-key"
    with pytest.raises(SafetyViolation, match="intensity"):
        modes.plan_custom("ai-x", {"lights": {"demo-key": {"intensity": 999}}})
    with pytest.raises(ModesError, match="object"):
        modes.plan_custom("ai-x", ["not", "a", "dict"])


@pytest.mark.unit
def test_describe_reports_readiness(modes):
    described = modes.describe()
    assert described["default"] == "demo_work"
    assert described["subsystems"]["power"] == "ready"
    assert described["subsystems"]["obs"] == "ready"
    bare = ModesModule("modes", modes.registry)
    assert bare.describe()["subsystems"] == {
        "power": "missing",
        "lights": "missing",
        "obs": "missing",
        "screens": "missing",
    }
