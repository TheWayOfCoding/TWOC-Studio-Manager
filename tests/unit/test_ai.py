# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the AI director scaffold (mock provider, no network)."""

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
    assert both["lights"] == {"demo-key": {"intensity": 30, "cct": 4200}}
    assert both["screens"] == {}
    assert MockProvider().direct("cozy", "screens", manifest)["lights"] == {}


@pytest.mark.unit
def test_apply_look_end_to_end(modes):
    outcome = apply_look(MockProvider(), modes, "cozy interview", "lights")
    assert outcome["provider"] == "mock"
    assert outcome["critique"] == ["clean: all directives within declared capabilities"]
    assert outcome["apply"]["subsystems"]["lights"]["status"] == "ready"
    assert modes.lights["demo-key"].state == {"intensity": 30, "cct": 4200}
    assert outcome["preset_candidate"]["label"] == "cozy interview"


@pytest.mark.unit
def test_bad_targets_rejected(modes):
    with pytest.raises(ModesError, match="targets"):
        apply_look(MockProvider(), modes, "x", "projector")


@pytest.mark.unit
def test_invalid_directives_rejected_before_moving(modes):
    class WildProvider(MockProvider):
        def direct(self, prompt, targets, manifest):
            return {"lights": {"demo-key": {"intensity": 999}}}

    with pytest.raises(ModesError, match="rejected"):
        apply_look(WildProvider(), modes, "blinding", "lights")
    assert modes.lights["demo-key"].state == {}


@pytest.mark.unit
def test_critique_flags_empty_and_zero():
    assert critique({"lights": {}, "screens": {}}, {}) == ["empty look: nothing to apply"]
    assert critique({"lights": {"a": {"intensity": 0}}}, {})[0].startswith("a at 0%")
