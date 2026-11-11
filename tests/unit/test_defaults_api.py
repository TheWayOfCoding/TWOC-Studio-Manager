# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for studio default sets (saved equipment-value collections)."""

import json
import shutil
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config"


@contextmanager
def _tmp_local_studio(tmp_path):
    """Real app pointed at a tmp config root with one local studio (id "x")."""
    from app.config.loader import load_all
    from app.registry import Registry

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


def _set(label="Evening", **overrides):
    body = {
        "set_id": "evening",
        "label": label,
        "devices": {"demo-key": {"intensity": 40, "cct": 3200}},
        "plugs": {"plug-1": {"on": True}},
    }
    body.update(overrides)
    return body


@pytest.mark.unit
def test_list_shows_assigned_default_and_sets(tmp_path):
    client = TestClient(app)
    with _tmp_local_studio(tmp_path):
        response = client.get("/api/studios/x/defaults")
        assert response.status_code == 200
        body = response.json()
        assert body["default"] == "default"
        assert body["sets"]["default"]["label"] == "Default"
        assert body["sets"]["default"]["devices"]["demo-key"] == {"intensity": 50, "cct": 4600}
        assert client.get("/api/studios/nope/defaults").status_code == 404


@pytest.mark.unit
def test_get_one_roundtrip_and_404s(tmp_path):
    client = TestClient(app)
    with _tmp_local_studio(tmp_path):
        response = client.get("/api/studios/x/defaults/default")
        assert response.status_code == 200
        assert response.json()["set"]["label"] == "Default"
        assert client.get("/api/studios/x/defaults/nope").status_code == 404
        assert client.get("/api/studios/nope/defaults/default").status_code == 404


@pytest.mark.unit
def test_create_persists_and_reloads(tmp_path):
    client = TestClient(app)
    with _tmp_local_studio(tmp_path) as studio_dir:
        created = client.post("/api/studios/x/defaults", json=_set())
        assert created.status_code == 200
        assert created.json() == {"set": "evening", "label": "Evening", "overwrote": False}
        on_disk = json.loads((studio_dir / "defaults.json").read_text(encoding="utf-8"))
        assert on_disk["evening"]["devices"] == {"demo-key": {"intensity": 40, "cct": 3200}}
        again = client.get("/api/studios/x/defaults/evening")
        assert again.json()["set"]["plugs"] == {"plug-1": {"on": True}}


@pytest.mark.unit
def test_create_conflicts_without_overwrite(tmp_path):
    client = TestClient(app)
    with _tmp_local_studio(tmp_path):
        assert client.post("/api/studios/x/defaults", json=_set()).status_code == 200
        dup = client.post("/api/studios/x/defaults", json=_set(label="Other"))
        assert dup.status_code == 409
        replaced = client.post("/api/studios/x/defaults", json=_set(label="Other", overwrite=True))
        assert replaced.status_code == 200
        assert replaced.json()["overwrote"] is True
        assert client.get("/api/studios/x/defaults/evening").json()["set"]["label"] == "Other"


@pytest.mark.unit
def test_create_rejects_bad_shapes(tmp_path):
    client = TestClient(app)
    with _tmp_local_studio(tmp_path):
        base = "/api/studios/x/defaults"
        assert client.post(base, json=_set(set_id="bad id")).status_code == 422
        assert client.post(base, json=_set(set_id="assign")).status_code == 422
        assert client.post(base, json=_set(label="")).status_code == 422
        assert client.post(base, json=_set(devices={"nope": {"intensity": 5}})).status_code == 422
        assert client.post(base, json=_set(devices={"demo-cam": {"iso": 400}})).status_code == 422
        assert client.post(base, json=_set(devices={"demo-key": {"bogus": 1}})).status_code == 422
        assert client.post(base, json=_set(devices={"demo-key": {"intensity": 500}})).status_code == 422
        assert client.post(base, json=_set(devices={"demo-key": {"power": "maybe"}})).status_code == 422
        assert client.post(base, json=_set(devices={"demo-key": {}})).status_code == 422
        assert client.post(base, json=_set(devices={"demo-screen": {"theme": 3}})).status_code == 422
        assert client.post(base, json=_set(plugs={"plug-1": {"on": "yes"}})).status_code == 422
        assert client.post(base, json=_set(plugs={"plug-1": {"on": True, "extra": 1}})).status_code == 422


@pytest.mark.unit
def test_create_refuses_tracked_example_studio():
    client = TestClient(app)
    response = client.post("/api/studios/example-studio/defaults", json=_set())
    assert response.status_code == 422


@pytest.mark.unit
def test_edit_replaces_and_validates(tmp_path):
    client = TestClient(app)
    with _tmp_local_studio(tmp_path):
        assert client.put("/api/studios/x/defaults/nope", json={"label": "X"}).status_code == 404
        edited = client.put(
            "/api/studios/x/defaults/default",
            json={"label": "House", "devices": {"demo-rgb": {"intensity": 10}}, "plugs": {}},
        )
        assert edited.status_code == 200
        assert edited.json() == {"set": "default", "label": "House"}
        assert client.get("/api/studios/x/defaults/default").json()["set"]["devices"] == {
            "demo-rgb": {"intensity": 10}
        }
        bad = client.put("/api/studios/x/defaults/default", json={"devices": {"nope": {}}})
        assert bad.status_code == 422


@pytest.mark.unit
def test_delete_removes_but_protects_assigned_default(tmp_path):
    client = TestClient(app)
    with _tmp_local_studio(tmp_path):
        assert client.delete("/api/studios/x/defaults/nope").status_code == 404
        assert client.post("/api/studios/x/defaults", json=_set()).status_code == 200
        removed = client.delete("/api/studios/x/defaults/evening")
        assert removed.status_code == 200
        assert removed.json() == {"set": "evening", "deleted": True}
        assert client.get("/api/studios/x/defaults/evening").status_code == 404
        held = client.delete("/api/studios/x/defaults/default")
        assert held.status_code == 422  # assigned default: reassign first


@pytest.mark.unit
def test_assign_and_clear_studio_default(tmp_path):
    client = TestClient(app)
    with _tmp_local_studio(tmp_path) as studio_dir:
        assert client.post("/api/studios/x/defaults", json=_set()).status_code == 200
        assigned = client.post("/api/studios/x/defaults/assign", json={"set_id": "evening"})
        assert assigned.status_code == 200
        assert assigned.json() == {"default": "evening"}
        meta = json.loads((studio_dir / "studio.json").read_text(encoding="utf-8"))
        assert meta["defaultSet"] == "evening"
        assert client.get("/api/studios/x/defaults").json()["default"] == "evening"
        assert client.post("/api/studios/x/defaults/assign", json={"set_id": "nope"}).status_code == 422
        cleared = client.post("/api/studios/x/defaults/assign", json={"set_id": None})
        assert cleared.status_code == 200
        assert cleared.json() == {"default": None}
        assert "defaultSet" not in json.loads((studio_dir / "studio.json").read_text(encoding="utf-8"))
        assert client.post("/api/studios/nope/defaults/assign", json={"set_id": None}).status_code == 404
