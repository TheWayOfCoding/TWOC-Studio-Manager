# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for modalities: engine validation plus CRUD/run/status/stop API."""

import json
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.modality import ModalityError, apply_modality, beat_to_content, plan_modality
from app.registry import Registry

CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config"


@pytest.fixture(autouse=True)
def clean_modality_runs():
    app.state.modality_runs.clear()
    yield
    app.state.modality_runs.clear()


def _registry():
    loaded = {
        "studio": {"id": "x", "label": "X"},
        "devices": [
            {"id": "key", "name": "Key", "zone": "tzone", "type": "light",
             "driver": "mock", "controllable": True},
            {"id": "strip", "name": "Strip", "zone": "tzone", "type": "lightstrip",
             "driver": "mock", "controllable": True},
            {"id": "wall", "name": "Wall", "zone": "tzone", "type": "screen", "driver": "screen"},
            {"id": "spare", "name": "Spare", "zone": "tzone", "type": "light",
             "driver": "mock", "controllable": False},
        ],
        "zones": [{"id": "tzone", "label": "T"}],
        "modes": [],
        "presets": {},
    }
    return Registry.from_loaded(loaded, {})


class _FakeModes:
    """Recording stand-in for the modes gate (no drivers)."""

    def __init__(self, registry):
        self.registry = registry
        self.calls = []

    def apply_custom(self, name, content, sleep=None, orch_caps=False,
                     allow_scenes=False, power_delay_s=None):
        self.calls.append({"name": name, "content": dict(content), "delay": power_delay_s})
        return {"ok": True, "name": name}


@contextmanager
def _tmp_local_studio(tmp_path):
    """Real app pointed at a tmp config root with one local studio (id "x")."""
    from app.config.loader import load_all

    shutil.copytree(CONFIG_ROOT / "profiles", tmp_path / "profiles")
    studio_dir = tmp_path / "studios.local" / "x"
    shutil.copytree(CONFIG_ROOT / "studios" / "example-studio", studio_dir)
    meta_path = studio_dir / "studio.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["id"] = "x"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

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


def _wait_status(client, studio, mid, tries=100):
    for _ in range(tries):
        body = client.get(f"/api/studios/{studio}/modalities/{mid}/status").json()
        if body["state"] in ("done", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError("modality run never finished")


# --- engine: plan-time validation ---


@pytest.mark.unit
def test_plan_rejects_bad_beats():
    registry = _registry()
    with pytest.raises(ModalityError, match="non-empty beats"):
        plan_modality(registry, "m", {"label": "M", "beats": []})
    with pytest.raises(ModalityError, match="must be an object"):
        plan_modality(registry, "m", {"label": "M", "beats": ["nope"]})
    with pytest.raises(ModalityError, match="must be an object"):
        beat_to_content(registry, "nope")
    for bad_hold in (-1, 3601, True, "soon"):
        with pytest.raises(ModalityError, match="hold_s"):
            beat_to_content(registry, {"hold_s": bad_hold, "actions": {}}, "beat")
    with pytest.raises(ModalityError, match="actions must be an object"):
        beat_to_content(registry, {"actions": ["nope"]}, "beat")
    with pytest.raises(ModalityError, match="lights must be an object"):
        beat_to_content(registry, {"actions": {"lights": ["nope"]}}, "beat")
    with pytest.raises(ModalityError, match="zoneLights must be an object"):
        beat_to_content(registry, {"actions": {"zoneLights": ["nope"]}}, "beat")
    with pytest.raises(ModalityError, match="unknown zone"):
        beat_to_content(registry, {"actions": {"zoneLights": {"nope": {}}}}, "beat")
    with pytest.raises(ModalityError, match="must be an object"):
        beat_to_content(registry, {"actions": {"zoneLights": {"tzone": 5}}}, "beat")
    with pytest.raises(ModalityError, match="scenes must be an object"):
        beat_to_content(registry, {"actions": {"scenes": ["nope"]}}, "beat")
    with pytest.raises(ModalityError, match="stay is only for the last beat"):
        plan_modality(registry, "m", {"label": "M", "beats": [
            {"stay": True, "actions": {}}, {"actions": {}}]})


@pytest.mark.unit
def test_beat_expansion_folds_zones_scenes_and_delay():
    registry = _registry()
    content = beat_to_content(registry, {
        "hold_s": 0,
        "power_delay_s": 1.5,
        "actions": {
            "powerOn": ["tzone-power"],
            "lights": {"key": {"intensity": 50}},
            "zoneLights": {"tzone": {"intensity": 10}},
            "scenes": {"key": 3},
            "screen": {"wall": {"theme": "work"}},
        },
    })
    # Explicit per-light look wins; the zone look fills the strip only
    # (screens and uncontrollable spares are never zone targets).
    assert content["lights"]["key"] == {"intensity": 50, "scene": 3}
    assert content["lights"]["strip"] == {"intensity": 10}
    assert "wall" not in content["lights"]
    assert "spare" not in content["lights"]
    assert content["power_delay_s"] == 1.5
    assert content["fade_s"] == 0
    assert content["screen"] == {"wall": {"theme": "work"}}


@pytest.mark.unit
def test_apply_stop_to_final_skips_middle_beats():
    modes = _FakeModes(_registry())
    modality = {"label": "M", "beats": [
        {"label": "first", "actions": {"lights": {"key": {"intensity": 10}}}},
        {"label": "middle", "actions": {"lights": {"key": {"intensity": 50}}}},
        {"label": "final", "actions": {"lights": {"key": {"intensity": 90}}}},
    ]}
    result = apply_modality(modes, "m", modality, sleep=lambda s: None,
                            should_stop=lambda: True)
    assert result["stopped_to_final"] is True
    assert result["ok"] is True
    assert [call["name"] for call in modes.calls] == ["m#0", "m#2"]
    assert modes.calls[-1]["content"]["lights"] == {"key": {"intensity": 90}}


# --- API: list / read ---


@pytest.mark.unit
def test_modalities_list_and_get():
    client = TestClient(app)
    listed = client.get("/api/studios/example-studio/modalities")
    assert listed.status_code == 200
    by_id = {item["id"]: item for item in listed.json()["modalities"]}
    assert by_id["demo_work"]["kind"] == "mode"
    assert by_id["demo_work"]["beats"] == 1
    assert by_id["default"]["kind"] == "preset"
    one = client.get("/api/studios/example-studio/modalities/default")
    assert one.status_code == 200
    assert one.json()["modality"]["label"] == "Default"
    assert client.get("/api/studios/example-studio/modalities/nope").status_code == 404
    assert client.get("/api/studios/nope/modalities").status_code == 404
    assert client.get("/api/studios/nope/modalities/default").status_code == 404


# --- API: save / delete ---


@pytest.mark.unit
def test_modality_save_validates_then_refuses_tracked():
    client = TestClient(app)
    base = "/api/studios/example-studio/modalities/mine"
    assert client.put(base, json={"label": "M", "beats": []}).status_code == 422
    bad_hold = {"label": "M", "beats": [{"hold_s": -1, "actions": {}}]}
    assert client.put(base, json=bad_hold).status_code == 422
    good = {"label": "M", "kind": "mode",
            "beats": [{"label": "x", "hold_s": 0, "actions": {"lights": {}}}]}
    refused = client.put(base, json=good)
    assert refused.status_code == 422
    assert "local studios" in refused.json()["detail"]
    assert client.put("/api/studios/nope/modalities/mine", json=good).status_code == 404


@pytest.mark.unit
def test_modality_save_roundtrip_and_delete(tmp_path):
    client = TestClient(app)
    with _tmp_local_studio(tmp_path) as studio_dir:
        base = "/api/studios/x/modalities/mine"
        body = {"label": "Mine", "kind": "custom", "notes": "demo",
                "beats": [{"label": "x", "hold_s": 0,
                           "actions": {"lights": {"demo-key": {"intensity": 50}}}}]}
        saved = client.put(base, json=body)
        assert saved.status_code == 200
        assert saved.json() == {"modality": "mine", "saved": True}
        on_disk = json.loads((studio_dir / "modalities.json").read_text(encoding="utf-8"))
        assert on_disk["mine"]["notes"] == "demo"
        assert client.get(base).json()["modality"]["label"] == "Mine"
        assert client.put(base, json={"label": "M", "beats": []}).status_code == 422
        deleted = client.delete(base)
        assert deleted.status_code == 200
        assert deleted.json() == {"modality": "mine", "deleted": True}
        assert client.delete(base).status_code == 404


@pytest.mark.unit
def test_modality_store_errors_are_honest(tmp_path):
    client = TestClient(app)
    with _tmp_local_studio(tmp_path) as studio_dir:
        path = studio_dir / "modalities.json"
        path.write_text("{oops", encoding="utf-8")
        good = {"label": "M", "beats": [{"actions": {}}]}
        broken = client.put("/api/studios/x/modalities/mine", json=good)
        assert broken.status_code == 422
        assert "unreadable" in broken.json()["detail"]
        path.write_text("[1, 2]", encoding="utf-8")
        assert client.delete("/api/studios/x/modalities/mine").status_code == 404
        path.unlink()
        assert client.delete("/api/studios/x/modalities/mine").status_code == 404


@pytest.mark.unit
def test_modality_delete_unknown():
    client = TestClient(app)
    assert client.delete("/api/studios/example-studio/modalities/nope").status_code == 404
    assert client.delete("/api/studios/nope/modalities/nope").status_code == 404


# --- API: run / status / stop ---


@pytest.mark.unit
def test_modality_run_status_stop():
    client = TestClient(app)
    run = client.post("/api/studios/example-studio/modalities/default/run")
    assert run.status_code == 200
    assert run.json()["state"] == "running"
    final = _wait_status(client, "example-studio", "default")
    assert final["state"] == "done"
    assert final["ok"] is True
    assert client.post("/api/studios/example-studio/modalities/default/stop").json()["state"] == "idle"
    assert client.get("/api/studios/example-studio/modalities/other/status").json()["state"] == "idle"


@pytest.mark.unit
def test_modality_run_rejects_unknown():
    client = TestClient(app)
    assert client.post("/api/studios/example-studio/modalities/nope/run").status_code == 404
    assert client.post("/api/studios/nope/modalities/default/run").status_code == 404
    assert client.get("/api/studios/nope/modalities/default/status").status_code == 404
    assert client.post("/api/studios/nope/modalities/default/stop").status_code == 404


@pytest.mark.unit
def test_modality_run_conflict_and_stop_running():
    client = TestClient(app)
    app.state.modality_runs["example-studio"] = {
        "id": "default", "state": "running", "beat": 0, "beats": 3,
        "ok": True, "stop": threading.Event(), "result": None,
    }
    conflict = client.post("/api/studios/example-studio/modalities/default/run")
    assert conflict.status_code == 409
    stopping = client.post("/api/studios/example-studio/modalities/default/stop")
    assert stopping.json()["state"] == "stopping to final beat"
    assert app.state.modality_runs["example-studio"]["stop"].is_set()


@pytest.mark.unit
def test_modality_run_failure_is_honest(monkeypatch):
    from app.api import modality as modality_api
    from app.modules.modality import ModalityError as EngineError

    def boom(*args, **kwargs):
        raise EngineError("gate refused")

    monkeypatch.setattr(modality_api, "apply_modality", boom)
    client = TestClient(app)
    run = client.post("/api/studios/example-studio/modalities/default/run")
    assert run.status_code == 200
    final = _wait_status(client, "example-studio", "default")
    assert final["state"] == "failed"
    assert final["ok"] is False
    assert "gate refused" in final["result"]["error"]
