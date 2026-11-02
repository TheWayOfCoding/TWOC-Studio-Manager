# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the equipment datapoint editor (details bag per device)."""

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


@pytest.mark.unit
def test_get_device_shows_facts_and_details(tmp_path):
    client = TestClient(app)
    with _tmp_local_studio(tmp_path):
        response = client.get("/api/studios/x/devices/demo-key")
        assert response.status_code == 200
        body = response.json()
        assert body["device"]["id"] == "demo-key"
        assert body["device"]["zone"] == "bench"
        assert body["details"] == {}
        assert client.get("/api/studios/x/devices/nope").status_code == 404
        assert client.get("/api/studios/nope/devices/demo-key").status_code == 404


@pytest.mark.unit
def test_put_details_roundtrip_persists_reloads(tmp_path):
    client = TestClient(app)
    with _tmp_local_studio(tmp_path) as studio_dir:
        payload = {"details": {"Diffusion layers": 2, "Softbox size": "32x32in"}}
        saved = client.put("/api/studios/x/devices/demo-key/details", json=payload)
        assert saved.status_code == 200
        assert saved.json() == {"device": "demo-key", "details": payload["details"]}
        on_disk = json.loads((studio_dir / "devices.json").read_text(encoding="utf-8"))
        kept = [item for item in on_disk if item["id"] == "demo-key"][0]
        assert kept["details"] == payload["details"]
        again = client.get("/api/studios/x/devices/demo-key")
        assert again.json()["details"] == payload["details"]


@pytest.mark.unit
def test_put_details_rejects_bad_shapes(tmp_path):
    client = TestClient(app)
    with _tmp_local_studio(tmp_path):
        base = "/api/studios/x/devices/demo-key/details"
        assert client.put(base, json={"details": ["nope"]}).status_code == 422
        assert client.put(base, json={"details": {"": "empty key"}}).status_code == 422
        assert client.put(base, json={"details": {"ok": True}}).status_code == 422
        assert client.put(base, json={"details": {"ok": {"nested": 1}}}).status_code == 422
        assert client.put("/api/studios/x/devices/nope/details", json={"details": {}}).status_code == 404


@pytest.mark.unit
def test_put_details_refuses_tracked_studios():
    client = TestClient(app)
    response = client.put(
        "/api/studios/example-studio/devices/demo-key/details", json={"details": {"a": "b"}}
    )
    assert response.status_code == 422
