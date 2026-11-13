# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the orchestrator scaffold (mock provider, no network)."""

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.drivers.mock import MockDriver
from app.drivers.ninja_ble import NinjaBleDriver
from app.modules.modes import ModesError, ModesModule
from app.orchestrator.director import build_manifest, critique, orchestrate
from app.orchestrator.mock import MockProvider
from app.config.loader import load_all
from app.registry import Registry

CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config"


@pytest.fixture()
def modes():
    data = load_all(CONFIG_ROOT)
    registry = Registry.from_loaded(data["studios"]["example-studio"], data["profiles"])
    lights = {"demo-key": MockDriver("demo-key", initial_state={})}
    return ModesModule("modes", registry, lights=lights)


@pytest.mark.unit
def test_manifest_lists_controllable_lights(modes):
    manifest = build_manifest(modes.registry)
    assert sorted(manifest["lights"]) == ["demo-key", "demo-rgb"]
    assert manifest["lights"]["demo-key"]["capabilities"]["cct"] is True


@pytest.mark.unit
def test_mock_provider_targets(modes):
    manifest = build_manifest(modes.registry)
    both = MockProvider().direct("cozy", "both", manifest)
    assert both["lights"] == {"demo-key": {"intensity": 25, "cct": 4600}}  # half the usual 50/4600
    assert both["screens"] == {}
    assert MockProvider().direct("cozy", "screens", manifest)["lights"] == {}


@pytest.mark.unit
def test_orchestrate_end_to_end(modes):
    outcome = orchestrate(MockProvider(), modes, "cozy interview", "lights")
    assert outcome["provider"] == "mock"
    assert outcome["critique"] == ["clean: all directives within declared capabilities"]
    assert outcome["apply"]["subsystems"]["lights"]["status"] == "ready"
    assert modes.lights["demo-key"].state == {"intensity": 25, "cct": 4600}
    assert outcome["preset_candidate"]["label"] == "cozy interview"


@pytest.mark.unit
def test_bad_targets_rejected(modes):
    with pytest.raises(ModesError, match="targets"):
        orchestrate(MockProvider(), modes, "x", "projector")


@pytest.mark.unit
def test_invalid_directives_rejected_before_moving(modes):
    class WildProvider(MockProvider):
        def direct(self, prompt, targets, manifest):
            return {"lights": {"nope": {"intensity": 50}}}

    with pytest.raises(ModesError, match="rejected"):
        orchestrate(WildProvider(), modes, "blinding", "lights")
    assert modes.lights["demo-key"].state == {}


@pytest.mark.unit
def test_critique_flags_empty_and_zero():
    assert critique({"lights": {}, "screens": {}}, {}) == ["empty look: nothing to apply"]
    assert critique({"lights": {"a": {"intensity": 0}}}, {})[0].startswith("a at 0%")


@pytest.mark.unit
def test_mock_falls_back_without_usual():
    bare = {"lights": {"k": {"capabilities": {"cct": True}, "usual": {}}}, "screens": []}
    assert MockProvider().direct("cozy", "lights", bare)["lights"] == {"k": {"intensity": 15, "cct": 4200}}
    no_cct = {"lights": {"k": {"capabilities": {"cct": False}, "usual": {"intensity": 40}}}, "screens": []}
    assert MockProvider().direct("cozy", "lights", no_cct)["lights"] == {"k": {"intensity": 20}}


@pytest.mark.unit
def test_orchestrator_prompt_teaches_look_contract():
    from app.orchestrator.director import PROMPT_TEMPLATE

    assert "orchestrator" in PROMPT_TEMPLATE
    assert "usual" in PROMPT_TEMPLATE
    assert "baseline" in PROMPT_TEMPLATE
    assert "intensity 0 means dark" in PROMPT_TEMPLATE
    assert '"power": "off"' in PROMPT_TEMPLATE
    assert "keys matching the requested look" in PROMPT_TEMPLATE
    assert "controls" in PROMPT_TEMPLATE
    assert "animation" in PROMPT_TEMPLATE
    assert "screen_options" in PROMPT_TEMPLATE
    assert "orchCap" in PROMPT_TEMPLATE
    assert "zones" in PROMPT_TEMPLATE
    assert "plugs are not directable" in PROMPT_TEMPLATE
    assert "default" in PROMPT_TEMPLATE


@pytest.mark.unit
def test_manifest_includes_usual_and_orch_cap(modes):
    modes.registry._devices["demo-key"]["orchCap"] = {"intensity": 20}
    manifest = build_manifest(modes.registry)
    entry = manifest["lights"]["demo-key"]
    assert entry["usual"] == {"intensity": 50, "cct": 4600}
    assert entry["orchCap"] == {"intensity": 20}


@pytest.mark.unit
def test_manifest_honors_legacy_ai_cap(modes):
    modes.registry._devices["demo-key"]["aiCap"] = {"intensity": 20}
    assert build_manifest(modes.registry)["lights"]["demo-key"]["orchCap"] == {"intensity": 20}
    modes.registry._devices["demo-key"]["orchCap"] = {"intensity": 10}
    assert build_manifest(modes.registry)["lights"]["demo-key"]["orchCap"] == {"intensity": 10}


@pytest.mark.unit
def test_orchestrate_clamps_ranges_and_steps(modes):
    """Orchestrator range misses clamp to the profile (recorded), never 422 or misfire."""

    class WarmProvider(MockProvider):
        def direct(self, prompt, targets, manifest):
            return {"lights": {"demo-key": {"intensity": 999, "cct": 2700}}}

    outcome = orchestrate(WarmProvider(), modes, "warm", "lights")
    assert modes.lights["demo-key"].state == {"intensity": 100, "cct": 3200}
    capped = outcome["apply"]["subsystems"]["lights"]["capped"]
    assert {"target": "demo-key", "key": "intensity", "asked": 999, "capped": 100} in capped
    assert {"target": "demo-key", "key": "cct", "asked": 2700, "capped": 3200} in capped


@pytest.mark.unit
def test_orchestrate_quantizes_hue_to_panel_steps(modes):
    """Hue 28 lands on the 5-degree panel grid (clamp records the rounding)."""

    class HueProvider(MockProvider):
        def direct(self, prompt, targets, manifest):
            return {"lights": {"demo-rgb": {"hue": 28, "saturation": 75}}}

    outcome = orchestrate(HueProvider(), modes, "amber", "lights")
    capped = outcome["apply"]["subsystems"]["lights"]["capped"]
    assert {"target": "demo-rgb", "key": "hue", "asked": 28, "capped": 30} in capped


@pytest.mark.unit
def test_manifest_carries_spatial_context(modes):
    """The orchestrator sees zone/position/role/distance/modifiers/power, not just ranges."""
    entry = build_manifest(modes.registry)["lights"]["demo-key"]
    assert entry["zone"] == "bench"
    assert entry["position"] == "Bench left, aimed at subject"
    assert entry["role"] == "key"
    assert entry["distanceToSubjectM"] == 1.0
    assert entry["modifiers"] == []
    assert entry["estWatts"] == 60
    assert entry["details"] == {}


@pytest.mark.unit
def test_manifest_names_every_directable(modes):
    manifest = build_manifest(modes.registry)
    assert manifest["lights"]["demo-key"]["name"] == "Demo key light"
    assert manifest["lights"]["demo-rgb"]["name"] == "Demo background panel"


@pytest.mark.unit
def test_manifest_controls_match_ranges(modes):
    """Controls name exactly the settable keys (combos welcome, guesses gone)."""
    key_controls = build_manifest(modes.registry)["lights"]["demo-key"]["controls"]
    assert key_controls["intensity"] == {"min": 0, "max": 100, "step": 1}
    assert key_controls["cct"] == {"min": 3200, "max": 5600, "step": 100}
    assert key_controls["page"] == ["cct", "hsi"]
    assert "hue" not in key_controls
    assert "saturation" not in key_controls
    rgb_controls = build_manifest(modes.registry)["lights"]["demo-rgb"]["controls"]
    assert rgb_controls["hue"] == {"min": 0, "max": 360, "step": 5}
    assert rgb_controls["saturation"] == {"min": 0, "max": 100, "step": 1}


@pytest.mark.unit
def test_manifest_screen_options_list_themes_and_animations(modes):
    options = build_manifest(modes.registry)["screen_options"]
    assert "live" in options["themes"]
    assert "fireplace" in options["animations"]
    assert "fishtank" in options["animations"]


@pytest.mark.unit
def test_manifest_zones_list_directable_members(modes):
    manifest = build_manifest(modes.registry)
    assert sorted(zone["id"] for zone in manifest["zones"]) == ["bench", "desk"]
    bench = next(zone for zone in manifest["zones"] if zone["id"] == "bench")
    assert bench["label"] == "Workbench"
    assert bench["devices"] == ["demo-key", "demo-rgb"]


@pytest.mark.unit
def test_manifest_default_carries_example_assignment(modes):
    assert build_manifest(modes.registry)["default"] == {
        "set": "default",
        "devices": {
            "demo-key": {"intensity": 50, "cct": 4600},
            "demo-rgb": {"intensity": 25, "hue": 220, "saturation": 60},
        },
    }


@pytest.mark.unit
def test_manifest_default_carries_assigned_values():
    """The active set rides along for restore asks; plugs and ghosts never do."""
    loaded = {
        "studio": {"id": "s", "label": "s", "defaultSet": "day"},
        "devices": [
            {
                "id": "lamp",
                "name": "Lamp",
                "type": "light",
                "zone": "desk",
                "controllable": True,
                "driver": "mock",
            }
        ],
        "zones": [{"id": "desk", "label": "Desk"}],
        "modes": [],
        "presets": {},
        "defaults": {
            "day": {
                "label": "Day",
                "devices": {"lamp": {"intensity": 50}, "ghost": {"intensity": 10}},
                "plugs": {"plug-1": {"on": True}},
            }
        },
    }
    manifest = build_manifest(Registry.from_loaded(loaded, {}))
    assert manifest["default"] == {"set": "day", "devices": {"lamp": {"intensity": 50}}}


@pytest.mark.unit
def test_orchestrate_scopes_default_values():
    """Scope filters the restore values too, so out-of-scope stays untouched."""

    class SpyProvider(MockProvider):
        def __init__(self):
            self.seen = None

        def direct(self, prompt, targets, manifest):
            self.seen = manifest
            return {"lights": {}, "screens": {}}

    loaded = {
        "studio": {"id": "s", "label": "s", "defaultSet": "day"},
        "devices": [
            {
                "id": "lamp",
                "name": "Lamp",
                "type": "light",
                "zone": "desk",
                "controllable": True,
                "driver": "mock",
            },
            {
                "id": "panel",
                "name": "Panel",
                "type": "light",
                "zone": "desk",
                "controllable": True,
                "driver": "mock",
            },
        ],
        "zones": [{"id": "desk", "label": "Desk"}],
        "modes": [],
        "presets": {},
        "defaults": {
            "day": {
                "label": "Day",
                "devices": {"lamp": {"intensity": 50}, "panel": {"intensity": 60}},
                "plugs": {},
            }
        },
    }
    module = ModesModule("modes", Registry.from_loaded(loaded, {}))
    spy = SpyProvider()
    orchestrate(spy, module, "restore", "lights", allowed=["lamp"])
    assert spy.seen["default"] == {"set": "day", "devices": {"lamp": {"intensity": 50}}}


@pytest.mark.unit
def test_orchestrate_applies_screen_animation():
    """Animation directives ride the modes screen path onto the kiosk state."""
    from app.drivers.screen import ScreenDriver
    from app.modules.screens import ScreensModule

    loaded = {
        "studio": {"id": "s", "label": "s"},
        "devices": [
            {
                "id": "orch-wall",
                "name": "Orch Wall",
                "type": "screen",
                "zone": "desk",
                "controllable": True,
                "driver": "screen",
            }
        ],
        "zones": [{"id": "desk", "label": "Desk"}],
        "modes": [],
        "presets": {},
    }
    module = ModesModule(
        "modes",
        Registry.from_loaded(loaded, {}),
        screens=ScreensModule("screens", {"orch-wall": ScreenDriver("orch-wall")}).apply_mapping,
    )

    class AnimateProvider(MockProvider):
        def direct(self, prompt, targets, manifest):
            return {"lights": {}, "screens": {"orch-wall": {"theme": "warm", "animation": "fireplace"}}}

    outcome = orchestrate(AnimateProvider(), module, "cozy fire", "screens")
    assert outcome["apply"]["subsystems"]["screens"]["status"] == "ready"
    state = ScreenDriver("orch-wall").get_state().state
    assert state["animation"] == "fireplace"
    assert state["theme"] == "warm"


@pytest.mark.unit
def test_gvm_profile_ranges_match_driver():
    """gvm-1500d declares the driver's real CCT/intensity bounds (2700 must clamp)."""
    spec = json.loads((CONFIG_ROOT / "profiles" / "gvm-1500d.json").read_text(encoding="utf-8"))[
        "spec"
    ]
    assert spec["cctRange"] == [3200, 5600]
    assert spec["intensityRange"] == [0, 100]


@pytest.mark.unit
def test_orchestrate_scope_drops_out_of_scope(modes):
    """Allowed-list filters directives; dropped ids are named, not silent."""

    class GreedyProvider(MockProvider):
        def direct(self, prompt, targets, manifest):
            return {"lights": {"demo-key": {"intensity": 10}, "demo-rgb": {"intensity": 10}}}

    outcome = orchestrate(GreedyProvider(), modes, "all", "lights", allowed=["demo-key"])
    assert sorted(outcome["directives"]["lights"]) == ["demo-key"]
    assert outcome["scope"]["skipped"] == ["demo-rgb"]


@pytest.mark.unit
def test_orchestrate_clamps_to_orch_cap(modes):
    modes.registry._devices["demo-key"]["orchCap"] = {"intensity": 20}

    class EagerProvider(MockProvider):
        def direct(self, prompt, targets, manifest):
            return {"lights": {"demo-key": {"intensity": 50}}}

    outcome = orchestrate(EagerProvider(), modes, "bright", "lights")
    assert modes.lights["demo-key"].state == {"intensity": 20}
    assert outcome["apply"]["subsystems"]["lights"]["capped"] == [
        {"target": "demo-key", "key": "intensity", "asked": 50, "capped": 20}
    ]


@pytest.mark.unit
def test_orchestrate_honors_legacy_ai_cap(modes):
    modes.registry._devices["demo-key"]["aiCap"] = {"intensity": 20}

    class EagerProvider(MockProvider):
        def direct(self, prompt, targets, manifest):
            return {"lights": {"demo-key": {"intensity": 50}}}

    outcome = orchestrate(EagerProvider(), modes, "bright", "lights")
    assert modes.lights["demo-key"].state == {"intensity": 20}
    assert outcome["apply"]["subsystems"]["lights"]["capped"] == [
        {"target": "demo-key", "key": "intensity", "asked": 50, "capped": 20}
    ]


@contextmanager
def _tmp_local_studio(tmp_path):
    """Real app pointed at a tmp config root with one local studio (id "x").

    Copies the example studio + profiles, so the loader, gate, file write,
    and registry reload all run for real without touching repo config.
    """
    import json
    import shutil

    from app.main import app

    shutil.copytree(CONFIG_ROOT / "profiles", tmp_path / "profiles")
    studio_dir = tmp_path / "studios.local" / "x"
    shutil.copytree(CONFIG_ROOT / "studios" / "example-studio", studio_dir)
    meta_path = studio_dir / "studio.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["id"] = "x"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    from app.config.loader import load_all
    from app.registry import Registry

    data = load_all(tmp_path)
    saved = (app.state.config_root, dict(app.state.registries), list(app.state.studio_list))
    app.state.config_root = tmp_path
    app.state.registries = {"x": Registry.from_loaded(data["studios"]["x"], data["profiles"])}
    app.state.studio_list = [{"id": "x", "label": "X", "source": "studios.local"}]
    try:
        yield studio_dir
    finally:
        app.state.config_root, registries, studio_list = saved
        app.state.registries.clear()
        app.state.registries.update(registries)
        app.state.studio_list = studio_list


@pytest.mark.unit
def test_save_preset_roundtrip_validates_persists_reloads(tmp_path):
    import json

    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    with _tmp_local_studio(tmp_path) as studio_dir:
        body = {"preset_id": "orch-cozy", "label": "Orch Cozy", "lights": {"demo-key": {"intensity": 30}}}
        saved = client.post("/api/studios/x/orchestrator/save-preset", json=body)
        assert saved.status_code == 200
        assert saved.json() == {"preset": "orch-cozy", "label": "Orch Cozy", "overwrote": False}
        on_disk = json.loads((studio_dir / "presets.json").read_text(encoding="utf-8"))
        assert on_disk["orch-cozy"]["lights"] == {"demo-key": {"intensity": 30}}
        listed = client.get("/api/studios/x/presets")
        assert {"id": "orch-cozy", "label": "Orch Cozy"} in listed.json()["presets"]

        dupe = client.post("/api/studios/x/orchestrator/save-preset", json=body)
        assert dupe.status_code == 409
        assert client.post("/api/studios/x/orchestrator/save-preset", json={**body, "overwrite": True}).status_code == 200

        bad_device = client.post(
            "/api/studios/x/orchestrator/save-preset",
            json={"preset_id": "bad", "lights": {"ghost": {"intensity": 10}}},
        )
        assert bad_device.status_code == 422
        bad_range = client.post(
            "/api/studios/x/orchestrator/save-preset",
            json={"preset_id": "bad", "lights": {"demo-key": {"intensity": 999}}},
        )
        assert bad_range.status_code == 422
        bad_id = client.post(
            "/api/studios/x/orchestrator/save-preset", json={"preset_id": "../evil", "lights": {}}
        )
        assert bad_id.status_code == 422


@pytest.mark.unit
def test_save_preset_refuses_tracked_and_unknown_studios():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    tracked = client.post(
        "/api/studios/example-studio/orchestrator/save-preset",
        json={"preset_id": "nope", "lights": {}},
    )
    assert tracked.status_code == 422
    assert client.post("/api/studios/ghost/orchestrator/save-preset", json={"preset_id": "x"}).status_code == 404


@pytest.mark.unit
def test_manifest_lists_screen_devices():
    """Screen-driver devices land in the manifest so the orchestrator can target screens."""

    class StubRegistry:
        device_ids = ["lamp", "wall"]
        studio = {}
        zone_ids = []

        def get_device(self, device_id):
            return {"lamp": {"type": "light", "controllable": True}, "wall": {"driver": "screen"}}[
                device_id
            ]

        def effective_device(self, device_id):
            return {}

    manifest = build_manifest(StubRegistry())
    assert manifest["screens"] == {"wall": {"name": "wall", "zone": None}}
    assert sorted(manifest["lights"]) == ["lamp"]
    assert manifest["default"] == {"set": None, "devices": {}}


@pytest.mark.unit
def test_manifest_includes_ninja_light(modes):
    """A controllable Ninja light joins the manifest with its baseline + cap."""
    modes.registry._devices["ninja-x"] = {
        "id": "ninja-x",
        "name": "Ninja X",
        "type": "light",
        "zone": "pc",
        "profile": "viltrox-ninja-10",
        "controllable": True,
        "driver": "ninja",
        "driverAddress": {"channel": 3, "group": "A"},
        "usual": {"intensity": 7},
        "orchCap": {"intensity": 10},
    }
    manifest = build_manifest(modes.registry)
    assert manifest["lights"]["ninja-x"]["usual"] == {"intensity": 7}
    assert manifest["lights"]["ninja-x"]["orchCap"] == {"intensity": 10}
    modes.registry._devices["ninja-x"]["controllable"] = False
    assert "ninja-x" not in build_manifest(modes.registry)["lights"]


@pytest.mark.unit
def test_orchestrator_reaches_ninja_light():
    """End to end: mock directs half the usual, the translator broadcasts it."""
    profiles = {
        "ninja-fixed": {
            "spec": {"intensityRange": [0, 100], "fixedTemp": True, "colorTempK": 5600},
            "capabilities": {"intensity": True, "cct": False, "rgb": False, "ble": True},
        }
    }
    loaded = {
        "studio": {"id": "s", "label": "s"},
        "devices": [
            {
                "id": "nx",
                "name": "NX",
                "type": "light",
                "zone": "pc",
                "profile": "ninja-fixed",
                "controllable": True,
                "driver": "ninja",
                "driverAddress": {"channel": 3, "group": "A"},
                "usual": {"intensity": 8},
                "orchCap": {"intensity": 10},
            }
        ],
        "zones": [{"id": "pc"}],
        "modes": [],
        "presets": {},
    }
    module = ModesModule(
        "modes",
        Registry.from_loaded(loaded, profiles),
        lights={"nx": NinjaBleDriver("nx", 3, "A", backend="mock")},
    )
    outcome = orchestrate(MockProvider(), module, "cozy", "lights")
    assert outcome["directives"]["lights"] == {"nx": {"intensity": 4}}
    assert outcome["apply"]["subsystems"]["lights"]["status"] == "ready"
    assert module.lights["nx"].look["brightness"] == 4


@pytest.mark.unit
def test_manifest_and_scope_include_lightstrip(modes):
    """The IR strip joins the manifest and zone scopes like any light."""
    from types import SimpleNamespace

    from app.api.orchestrator import _resolve_scope

    modes.registry._devices["strip"] = {
        "id": "strip",
        "name": "Strip",
        "type": "lightstrip",
        "zone": "pc",
        "profile": "bytech-strip",
        "controllable": True,
        "driver": "ir",
    }
    manifest = build_manifest(modes.registry)
    assert manifest["lights"]["strip"]["capabilities"]["rgb"] is True
    zone = modes.registry.zone_ids[0]
    modes.registry._devices["strip"]["zone"] = zone
    body = SimpleNamespace(scope_mode="zones", scope_zones=[zone], scope_lights=[])
    allowed, _ = _resolve_scope(modes.registry, body)
    assert "strip" in allowed
