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
    modes.registry._devices["demo-rgb"]["driver"] = "gvm"
    modes.registry._modes["h"] = {
        "id": "h",
        "label": "H",
        "lights": {"demo-rgb": {"hue": 220, "saturation": 60}},
    }
    hued = modes.apply_mode("h", sleep=lambda s: None)
    assert hued["subsystems"]["lights"]["status"] == "ready"
    assert modes.lights["demo-rgb"].state == {"hue": 220, "saturation": 60}


@pytest.mark.unit
def test_unknown_driver_family_degraded(modes):
    modes.registry._devices["demo-screen"]["driver"] = "hyperlight"
    result = modes.apply_mode("demo_work", sleep=lambda s: None)
    lights = result["subsystems"]["lights"]
    assert lights["status"] == "degraded"
    assert any("hyperlight" in item["reason"] for item in lights["skipped"])


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


class FadeDriver:
    """Fade-capable fake: records apply/fade calls like MockDriver."""

    def __init__(self, driver_id):
        self.driver_id = driver_id
        self.state = {}
        self.calls = []

    def apply(self, command):
        self.calls.append(("apply", dict(command)))
        self.state.update(command)
        return DriverResult(ok=True, state=dict(self.state))

    def fade(self, command, dwell_s):
        self.calls.append(("fade", dict(command), dwell_s))
        self.state.update(command)
        return DriverResult(ok=True, state=dict(self.state))


class InstantDriver:
    """Fade-less fake: apply only, like the GVM driver."""

    def __init__(self, driver_id):
        self.driver_id = driver_id
        self.state = {}
        self.calls = []

    def apply(self, command):
        self.calls.append(("apply", dict(command)))
        self.state.update(command)
        return DriverResult(ok=True, state=dict(self.state))


DMX_PROFILES = {
    "dmx-bicolor": {
        "spec": {"intensityRange": [0, 100], "cctRange": [2500, 8500]},
        "capabilities": {"intensity": True, "cct": True, "rgb": False},
    },
    "dmx-dimmer": {
        "spec": {"intensityRange": [0, 100]},
        "capabilities": {"intensity": True, "cct": False, "rgb": False},
    },
}


def make_dmx_registry():
    devices = [
        {
            "id": "dmx-key",
            "name": "DMX key",
            "type": "light",
            "zone": "bench",
            "profile": "dmx-bicolor",
            "controllable": True,
            "driver": "dmx",
            "driverAddress": {"dmx": {"dimmer": 1, "cct": 2}},
        },
        {
            "id": "dmx-fill",
            "name": "DMX fill",
            "type": "light",
            "zone": "bench",
            "profile": "dmx-dimmer",
            "controllable": True,
            "driver": "dmx",
            "driverAddress": {"dmx": {"dimmer": 20}},
        },
        {
            "id": "dmx-broken",
            "name": "DMX broken",
            "type": "light",
            "zone": "bench",
            "profile": "dmx-dimmer",
            "controllable": True,
            "driver": "dmx",
        },
    ]
    loaded = {
        "studio": {"id": "s", "label": "s"},
        "devices": devices,
        "zones": [{"id": "bench"}],
        "modes": [],
        "presets": {},
    }
    return Registry.from_loaded(loaded, DMX_PROFILES)


@pytest.fixture()
def dmx_modes():
    registry = make_dmx_registry()
    lights = {device: FadeDriver(device) for device in ("dmx-key", "dmx-fill", "dmx-broken")}
    return ModesModule("modes", registry, lights=lights)


@pytest.mark.unit
def test_dmx_keys_translate_and_apply(dmx_modes):
    dmx_modes.registry._modes["look"] = {
        "id": "look",
        "label": "Look",
        "lights": {"dmx-key": {"intensity": 5, "cct": 5600}, "dmx-fill": {"intensity": 3}},
    }
    result = dmx_modes.apply_mode("look", sleep=lambda s: None)
    assert result["subsystems"]["lights"]["status"] == "ready"
    assert dmx_modes.lights["dmx-key"].state == {"intensity": 5, "cct": 5600}
    assert dmx_modes.lights["dmx-fill"].state == {"intensity": 3}
    calls = [call for call in dmx_modes.lights["dmx-key"].calls if call[0] == "apply"]
    assert [sorted(call[1]) for call in calls] == [["intensity"], ["cct"]]


@pytest.mark.unit
def test_dmx_missing_channel_map_degraded(dmx_modes):
    dmx_modes.registry._modes["look"] = {
        "id": "look",
        "label": "Look",
        "lights": {"dmx-broken": {"intensity": 5}},
    }
    result = dmx_modes.apply_mode("look", sleep=lambda s: None)
    lights = result["subsystems"]["lights"]
    assert lights["status"] == "degraded"
    assert any("dmx-broken" in item["reason"] for item in lights["skipped"])


@pytest.mark.unit
def test_dmx_power_key_skipped_with_hint(dmx_modes):
    dmx_modes.registry._modes["look"] = {
        "id": "look",
        "label": "Look",
        "lights": {"dmx-key": {"power": "off"}},
    }
    result = dmx_modes.apply_mode("look", sleep=lambda s: None)
    lights = result["subsystems"]["lights"]
    assert lights["status"] == "degraded"
    assert any("intensity 0" in item["reason"] for item in lights["skipped"])
    assert dmx_modes.lights["dmx-key"].state == {}


@pytest.mark.unit
def test_dmx_cct_without_channel_skipped(dmx_modes):
    dmx_modes.registry._devices["dmx-fill"]["profile"] = "dmx-bicolor"
    dmx_modes.registry._modes["look"] = {
        "id": "look",
        "label": "Look",
        "lights": {"dmx-fill": {"intensity": 3, "cct": 5600}},
    }
    result = dmx_modes.apply_mode("look", sleep=lambda s: None)
    lights = result["subsystems"]["lights"]
    assert lights["status"] == "degraded"
    assert any("CCT" in item["reason"] for item in lights["skipped"])
    assert dmx_modes.lights["dmx-fill"].state == {"intensity": 3}


@pytest.mark.unit
def test_mode_fade_calls_driver_fade(dmx_modes):
    dmx_modes.registry._modes["look"] = {
        "id": "look",
        "label": "Look",
        "fade_s": 0.5,
        "lights": {"dmx-key": {"intensity": 5, "cct": 5600}},
    }
    result = dmx_modes.apply_mode("look", sleep=lambda s: None)
    assert result["subsystems"]["lights"]["status"] == "ready"
    calls = dmx_modes.lights["dmx-key"].calls
    assert calls == [("fade", {"intensity": 5, "cct": 5600}, 0.5)]


@pytest.mark.unit
def test_per_light_fade_overrides_mode(dmx_modes):
    dmx_modes.registry._modes["look"] = {
        "id": "look",
        "label": "Look",
        "fade_s": 0.5,
        "lights": {"dmx-key": {"intensity": 5, "fade_s": 0}, "dmx-fill": {"intensity": 3}},
    }
    result = dmx_modes.apply_mode("look", sleep=lambda s: None)
    assert result["subsystems"]["lights"]["status"] == "ready"
    assert dmx_modes.lights["dmx-key"].calls == [("apply", {"intensity": 5})]
    assert dmx_modes.lights["dmx-fill"].calls == [("fade", {"intensity": 3}, 0.5)]


@pytest.mark.unit
def test_apply_time_fade_overrides_all(dmx_modes):
    dmx_modes.registry._modes["look"] = {
        "id": "look",
        "label": "Look",
        "fade_s": 0.5,
        "lights": {"dmx-key": {"intensity": 5, "fade_s": 0.1}},
    }
    result = dmx_modes.apply_mode("look", sleep=lambda s: None, fade_s=1.5)
    assert result["subsystems"]["lights"]["status"] == "ready"
    assert dmx_modes.lights["dmx-key"].calls == [("fade", {"intensity": 5}, 1.5)]


@pytest.mark.unit
def test_fade_unsupported_applies_instantly_with_note(dmx_modes):
    dmx_modes.lights["dmx-key"] = InstantDriver("dmx-key")
    dmx_modes.registry._modes["look"] = {
        "id": "look",
        "label": "Look",
        "fade_s": 0.5,
        "lights": {"dmx-key": {"intensity": 5}},
    }
    result = dmx_modes.apply_mode("look", sleep=lambda s: None)
    lights = result["subsystems"]["lights"]
    assert lights["status"] == "ready"
    assert dmx_modes.lights["dmx-key"].state == {"intensity": 5}
    assert any("dmx-key" in item["reason"] for item in lights["notes"])


@pytest.mark.unit
def test_bad_fade_rejected_before_moving(dmx_modes):
    dmx_modes.registry._modes["bad"] = {
        "id": "bad",
        "label": "Bad",
        "fade_s": "soon",
        "lights": {"dmx-key": {"intensity": 5}},
    }
    with pytest.raises(ModesError, match="fade_s"):
        dmx_modes.apply_mode("bad", sleep=lambda s: None)
    assert dmx_modes.lights["dmx-key"].state == {}
    assert dmx_modes.lights["dmx-key"].calls == []
    dmx_modes.registry._modes["bad"]["fade_s"] = 0
    dmx_modes.registry._modes["bad"]["lights"] = {"dmx-key": {"intensity": 5, "fade_s": -1}}
    with pytest.raises(ModesError, match="fade_s"):
        dmx_modes.apply_mode("bad", sleep=lambda s: None)
    assert dmx_modes.lights["dmx-key"].calls == []


@pytest.mark.unit
def test_ai_cap_clamps_and_reports(dmx_modes):
    dmx_modes.registry._devices["dmx-key"]["aiCap"] = {"intensity": 20}
    result = dmx_modes.apply_custom(
        "ai-x", {"lights": {"dmx-key": {"intensity": 50, "cct": 5600}}}, sleep=lambda s: None
    )
    lights = result["subsystems"]["lights"]
    assert lights["status"] == "ready"
    assert dmx_modes.lights["dmx-key"].state == {"intensity": 20, "cct": 5600}
    assert lights["capped"] == [{"target": "dmx-key", "key": "intensity", "asked": 50, "capped": 20}]


@pytest.mark.unit
def test_ai_cap_applies_all_families(modes):
    modes.registry._devices["demo-key"]["aiCap"] = {"intensity": 10}
    result = modes.apply_custom("ai-x", {"lights": {"demo-key": {"intensity": 30}}}, sleep=lambda s: None)
    assert result["subsystems"]["lights"]["status"] == "ready"
    assert modes.lights["demo-key"].state == {"intensity": 10}
    assert result["subsystems"]["lights"]["capped"][0]["capped"] == 10


@pytest.mark.unit
def test_authored_modes_ignore_ai_cap(modes):
    modes.registry._devices["demo-key"]["aiCap"] = {"intensity": 10}
    modes.registry._modes["bright"] = {
        "id": "bright",
        "label": "Bright",
        "lights": {"demo-key": {"intensity": 60}},
    }
    result = modes.apply_mode("bright", sleep=lambda s: None)
    assert result["subsystems"]["lights"]["status"] == "ready"
    assert modes.lights["demo-key"].state == {"intensity": 60}
    assert result["subsystems"]["lights"]["capped"] == []


@pytest.mark.unit
def test_apply_power_delay_staggers_plugs(modes):
    sleeps = []
    result = modes.apply_mode("demo_stream", sleep=sleeps.append, power_delay_s=0.5)
    assert result["subsystems"]["power"]["status"] == "ready"
    assert sleeps == [0.5]
    assert modes.power.drivers["plug-1"].state["on"] is True


@pytest.mark.unit
def test_apply_power_delay_defaults_instant(modes):
    sleeps = []
    result = modes.apply_mode("demo_stream", sleep=sleeps.append)
    assert result["subsystems"]["power"]["status"] == "ready"
    assert sleeps == []


@pytest.mark.unit
def test_bad_power_delay_rejected_before_moving(modes):
    with pytest.raises(ModesError, match="power_delay_s"):
        modes.apply_mode("demo_stream", sleep=lambda s: None, power_delay_s="soon")
    with pytest.raises(ModesError, match="power_delay_s"):
        modes.apply_mode("demo_stream", sleep=lambda s: None, power_delay_s=5.5)
    assert "on" not in modes.power.drivers["plug-1"].state


@pytest.mark.unit
def test_preset_fade_and_dmx(dmx_modes):
    dmx_modes.registry._presets["look"] = {
        "label": "Look",
        "fade_s": 0.5,
        "lights": {"dmx-key": {"intensity": 5}},
    }
    result = dmx_modes.apply_preset("look", sleep=lambda s: None)
    assert result["kind"] == "preset"
    assert result["subsystems"]["lights"]["status"] == "ready"
    assert dmx_modes.lights["dmx-key"].calls == [("fade", {"intensity": 5}, 0.5)]


@pytest.mark.unit
def test_custom_skips_ai_cap_when_asked(modes):
    """Authored shows bypass the AI intensity cap (they are not AI asks)."""
    modes.registry._devices["demo-key"]["aiCap"] = {"intensity": 10}
    result = modes.apply_custom(
        "show-x", {"lights": {"demo-key": {"intensity": 30}}}, sleep=lambda s: None, ai_caps=False
    )
    assert result["subsystems"]["lights"]["status"] == "ready"
    assert modes.lights["demo-key"].state == {"intensity": 30}
    assert result["subsystems"]["lights"]["capped"] == []


@pytest.mark.unit
def test_custom_scene_refused_unless_allowed(modes):
    """Scenes stay refused on the automation path; confirmed shows may pass."""
    with pytest.raises(SafetyViolation, match="scene"):
        modes.apply_custom("ai-x", {"lights": {"demo-key": {"scene": 3}}}, sleep=lambda s: None)
    result = modes.apply_custom(
        "show-x", {"lights": {"demo-key": {"scene": 3}}}, sleep=lambda s: None, allow_scenes=True
    )
    assert result["subsystems"]["lights"]["status"] == "ready"
    assert ("apply", {"scene": 3}) in modes.lights["demo-key"].calls


@pytest.mark.unit
def test_gvm_keys_plan_page_first_scene_last(modes):
    """GVM plans follow the proven bench order, not JSON key order."""
    modes.registry._devices["demo-rgb"]["driver"] = "gvm"
    plan = modes.plan_custom(
        "show-x",
        {"lights": {"demo-rgb": {"hue": 220, "intensity": 100, "page": "hsi",
                                 "saturation": 60, "scene": 3}}},
        allow_scenes=True,
    )
    keys = [key for key, _value in plan["lights"]["devices"][0]["commands"]]
    assert keys == ["page", "brightness", "hue", "saturation", "scene"]


class BatchDriver:
    """Fake batch-capable driver: records apply_many, explodes on apply."""

    def __init__(self, results=None):
        self.batches = []
        self.results = results

    def apply(self, command):
        raise AssertionError("batch driver must not take single applies")

    def apply_many(self, commands):
        self.batches.append(commands)
        if self.results is not None:
            return self.results
        return [DriverResult(ok=True, state=dict(cmd)) for cmd in commands]


@pytest.mark.unit
def test_exec_batches_multi_key_steps(modes):
    modes.lights["demo-key"] = BatchDriver()
    result = modes.apply_custom(
        "show-x", {"lights": {"demo-key": {"intensity": 40, "cct": 5000}}}, sleep=lambda s: None
    )
    applied = result["subsystems"]["lights"]["applied"]
    assert [(item["key"], item["value"]) for item in applied] == [("intensity", 40), ("cct", 5000)]
    assert all(item["ok"] for item in applied)
    assert modes.lights["demo-key"].batches == [[{"intensity": 40}, {"cct": 5000}]]


@pytest.mark.unit
def test_exec_batch_shortfall_fails_missing_keys(modes):
    modes.lights["demo-key"] = BatchDriver(results=[DriverResult(ok=True, state={})])
    result = modes.apply_custom(
        "show-x", {"lights": {"demo-key": {"intensity": 40, "cct": 5000}}}, sleep=lambda s: None
    )
    applied = result["subsystems"]["lights"]["applied"]
    assert [item["ok"] for item in applied] == [True, False]
    assert "too few" in applied[1]["error"]
