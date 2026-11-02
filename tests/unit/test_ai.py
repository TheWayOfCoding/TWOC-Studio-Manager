# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the AI director scaffold (mock provider, no network)."""

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.ai.director import apply_look, build_manifest, critique
from app.ai.mock import MockProvider
from app.config.loader import load_all
from app.drivers.mock import MockDriver
from app.modules.modes import ModesError, ModesModule
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
def test_apply_look_end_to_end(modes):
    outcome = apply_look(MockProvider(), modes, "cozy interview", "lights")
    assert outcome["provider"] == "mock"
    assert outcome["critique"] == ["clean: all directives within declared capabilities"]
    assert outcome["apply"]["subsystems"]["lights"]["status"] == "ready"
    assert modes.lights["demo-key"].state == {"intensity": 25, "cct": 4600}
    assert outcome["preset_candidate"]["label"] == "cozy interview"


@pytest.mark.unit
def test_bad_targets_rejected(modes):
    with pytest.raises(ModesError, match="targets"):
        apply_look(MockProvider(), modes, "x", "projector")


@pytest.mark.unit
def test_invalid_directives_rejected_before_moving(modes):
    class WildProvider(MockProvider):
        def direct(self, prompt, targets, manifest):
            return {"lights": {"nope": {"intensity": 50}}}

    with pytest.raises(ModesError, match="rejected"):
        apply_look(WildProvider(), modes, "blinding", "lights")
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
def test_meta_prompt_teaches_baseline_and_off():
    from app.ai.meta import PROMPT_TEMPLATE

    assert "usual" in PROMPT_TEMPLATE
    assert "baseline" in PROMPT_TEMPLATE
    assert "intensity 0 means dark" in PROMPT_TEMPLATE
    assert '"power": "off"' in PROMPT_TEMPLATE
    assert "keys matching the requested look" in PROMPT_TEMPLATE


@pytest.mark.unit
def test_manifest_includes_usual_and_ai_cap(modes):
    modes.registry._devices["demo-key"]["aiCap"] = {"intensity": 20}
    manifest = build_manifest(modes.registry)
    entry = manifest["lights"]["demo-key"]
    assert entry["usual"] == {"intensity": 50, "cct": 4600}
    assert entry["aiCap"] == {"intensity": 20}


@pytest.mark.unit
def test_apply_look_clamps_ranges_and_steps(modes):
    """AI range misses clamp to the profile (recorded), never 422 or misfire."""

    class WarmProvider(MockProvider):
        def direct(self, prompt, targets, manifest):
            return {"lights": {"demo-key": {"intensity": 999, "cct": 2700}}}

    outcome = apply_look(WarmProvider(), modes, "warm", "lights")
    assert modes.lights["demo-key"].state == {"intensity": 100, "cct": 3200}
    capped = outcome["apply"]["subsystems"]["lights"]["capped"]
    assert {"target": "demo-key", "key": "intensity", "asked": 999, "capped": 100} in capped
    assert {"target": "demo-key", "key": "cct", "asked": 2700, "capped": 3200} in capped


@pytest.mark.unit
def test_apply_look_quantizes_hue_to_panel_steps(modes):
    """Hue 28 lands on the 5-degree panel grid (clamp records the rounding)."""

    class HueProvider(MockProvider):
        def direct(self, prompt, targets, manifest):
            return {"lights": {"demo-rgb": {"hue": 28, "saturation": 75}}}

    outcome = apply_look(HueProvider(), modes, "amber", "lights")
    capped = outcome["apply"]["subsystems"]["lights"]["capped"]
    assert {"target": "demo-rgb", "key": "hue", "asked": 28, "capped": 30} in capped


@pytest.mark.unit
def test_manifest_carries_spatial_context(modes):
    """The AI sees zone/position/role/distance/modifiers/power, not just ranges."""
    entry = build_manifest(modes.registry)["lights"]["demo-key"]
    assert entry["zone"] == "bench"
    assert entry["position"] == "Bench left, aimed at subject"
    assert entry["role"] == "key"
    assert entry["distanceToSubjectM"] == 1.0
    assert entry["modifiers"] == []
    assert entry["estWatts"] == 60
    assert entry["details"] == {}


@pytest.mark.unit
def test_gvm_profile_ranges_match_driver():
    """gvm-1500d declares the driver's real CCT/intensity bounds (2700 must clamp)."""
    spec = json.loads((CONFIG_ROOT / "profiles" / "gvm-1500d.json").read_text(encoding="utf-8"))[
        "spec"
    ]
    assert spec["cctRange"] == [3200, 5600]
    assert spec["intensityRange"] == [0, 100]


@pytest.mark.unit
def test_apply_look_scope_drops_out_of_scope(modes):
    """Allowed-list filters directives; dropped ids are named, not silent."""

    class GreedyProvider(MockProvider):
        def direct(self, prompt, targets, manifest):
            return {"lights": {"demo-key": {"intensity": 10}, "demo-rgb": {"intensity": 10}}}

    outcome = apply_look(GreedyProvider(), modes, "all", "lights", allowed=["demo-key"])
    assert sorted(outcome["directives"]["lights"]) == ["demo-key"]
    assert outcome["scope"]["skipped"] == ["demo-rgb"]


@pytest.mark.unit
def test_apply_look_clamps_to_ai_cap(modes):
    modes.registry._devices["demo-key"]["aiCap"] = {"intensity": 20}

    class EagerProvider(MockProvider):
        def direct(self, prompt, targets, manifest):
            return {"lights": {"demo-key": {"intensity": 50}}}

    outcome = apply_look(EagerProvider(), modes, "bright", "lights")
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
        body = {"preset_id": "ai-cozy", "label": "AI Cozy", "lights": {"demo-key": {"intensity": 30}}}
        saved = client.post("/api/studios/x/ai/save-preset", json=body)
        assert saved.status_code == 200
        assert saved.json() == {"preset": "ai-cozy", "label": "AI Cozy", "overwrote": False}
        on_disk = json.loads((studio_dir / "presets.json").read_text(encoding="utf-8"))
        assert on_disk["ai-cozy"]["lights"] == {"demo-key": {"intensity": 30}}
        listed = client.get("/api/studios/x/presets")
        assert {"id": "ai-cozy", "label": "AI Cozy"} in listed.json()["presets"]

        dupe = client.post("/api/studios/x/ai/save-preset", json=body)
        assert dupe.status_code == 409
        assert client.post("/api/studios/x/ai/save-preset", json={**body, "overwrite": True}).status_code == 200

        bad_device = client.post(
            "/api/studios/x/ai/save-preset",
            json={"preset_id": "bad", "lights": {"ghost": {"intensity": 10}}},
        )
        assert bad_device.status_code == 422
        bad_range = client.post(
            "/api/studios/x/ai/save-preset",
            json={"preset_id": "bad", "lights": {"demo-key": {"intensity": 999}}},
        )
        assert bad_range.status_code == 422
        bad_id = client.post(
            "/api/studios/x/ai/save-preset", json={"preset_id": "../evil", "lights": {}}
        )
        assert bad_id.status_code == 422


@pytest.mark.unit
def test_save_preset_refuses_tracked_and_unknown_studios():
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    tracked = client.post(
        "/api/studios/example-studio/ai/save-preset",
        json={"preset_id": "nope", "lights": {}},
    )
    assert tracked.status_code == 422
    assert client.post("/api/studios/ghost/ai/save-preset", json={"preset_id": "x"}).status_code == 404


@pytest.mark.unit
def test_manifest_lists_screen_devices():
    """Screen-driver devices land in the manifest so AI can target screens."""

    class StubRegistry:
        device_ids = ["lamp", "wall"]

        def get_device(self, device_id):
            return {"lamp": {"type": "light", "controllable": True}, "wall": {"driver": "screen"}}[
                device_id
            ]

        def effective_device(self, device_id):
            return {}

    manifest = build_manifest(StubRegistry())
    assert manifest["screens"] == ["wall"]
    assert sorted(manifest["lights"]) == ["lamp"]
