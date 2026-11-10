# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for named OBS targets (store, admin API, studio selection)."""

import json
import shutil
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import obs_store
from app.main import app
from app.obs_store import (
    ObsCryptoError,
    ObsCryptoUnavailable,
    ObsStoreError,
    ObsTargetError,
    TargetConflictError,
    TargetReferencedError,
    UnknownTargetError,
)

CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config"
STATIC_DIR = Path(__file__).resolve().parents[2] / "backend" / "app" / "static"
SECRET = "s3cr3t-obs-pw"


@pytest.fixture()
def needs_crypto():
    """Gate: password encryption needs the cryptography package installed."""
    pytest.importorskip("cryptography")


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
        yield tmp_path
    finally:
        app.state.config_root, registries, studio_list = saved
        app.state.registries.clear()
        app.state.registries.update(registries)
        app.state.studio_list = studio_list


def _raw_store(tmp_path):
    return (tmp_path / "studios.local" / "obs.json").read_text(encoding="utf-8")


@pytest.mark.unit
def test_create_and_list_are_redacted(tmp_path, needs_crypto):
    """List views carry hosts and has_password only — never the secret."""
    obs_store.create_target(tmp_path, "b-main", "192.0.2.5", 4455, SECRET)
    obs_store.create_target(tmp_path, "a-bench", "192.0.2.6")
    listed = obs_store.list_targets(tmp_path)
    assert [t["name"] for t in listed] == ["a-bench", "b-main"]
    assert [t["has_password"] for t in listed] == [False, True]
    assert SECRET not in json.dumps(listed)
    assert all("password" not in t or t["password"] is None for t in listed)


@pytest.mark.unit
def test_password_encrypted_at_rest(tmp_path, needs_crypto):
    """The file holds enc: ciphertext; the key file sits beside it."""
    obs_store.create_target(tmp_path, "main", "192.0.2.5", 4455, SECRET)
    raw = _raw_store(tmp_path)
    assert SECRET not in raw
    assert obs_store.ENC_PREFIX in raw
    assert (tmp_path / "studios.local" / obs_store.KEY_FILENAME).exists()
    assert obs_store.resolve_target(tmp_path, "main") == {
        "host": "192.0.2.5",
        "port": 4455,
        "password": SECRET,
    }
    assert obs_store.resolve_target(tmp_path, "nope") is None


@pytest.mark.unit
def test_update_keep_set_clear_password(tmp_path, needs_crypto):
    """Omitted password keeps; explicit null clears to none."""
    obs_store.create_target(tmp_path, "main", "192.0.2.5", 4455, SECRET)
    kept = obs_store.update_target(tmp_path, "main", host="192.0.2.7")
    assert (kept["host"], kept["has_password"]) == ("192.0.2.7", True)
    assert obs_store.resolve_target(tmp_path, "main")["password"] == SECRET
    obs_store.update_target(tmp_path, "main", password="new-pw")
    assert obs_store.resolve_target(tmp_path, "main")["password"] == "new-pw"
    cleared = obs_store.update_target(tmp_path, "main", password=None)
    assert cleared["has_password"] is False
    assert obs_store.resolve_target(tmp_path, "main")["password"] is None
    with pytest.raises(UnknownTargetError):
        obs_store.update_target(tmp_path, "nope", host="x")


@pytest.mark.unit
def test_delete_refuses_referenced_target(tmp_path):
    """A studio's default cannot be deleted out from under it (409)."""
    obs_store.create_target(tmp_path, "main", "192.0.2.5")
    obs_store.create_target(tmp_path, "spare", "192.0.2.6")
    obs_store.set_studio_target(tmp_path, "x", "main")
    with pytest.raises(TargetReferencedError):
        obs_store.delete_target(tmp_path, "main")
    assert obs_store.delete_target(tmp_path, "spare") == {"name": "spare", "deleted": True}
    with pytest.raises(UnknownTargetError):
        obs_store.delete_target(tmp_path, "spare")


@pytest.mark.unit
def test_studio_selection_roundtrip_and_unknown(tmp_path):
    """Studios point at targets; null clears; unknown names refuse."""
    assert obs_store.studio_target(tmp_path, "x") is None
    obs_store.create_target(tmp_path, "main", "192.0.2.5")
    assert obs_store.set_studio_target(tmp_path, "x", "main") == {"studio": "x", "target": "main"}
    assert obs_store.studio_target(tmp_path, "x") == "main"
    with pytest.raises(UnknownTargetError):
        obs_store.set_studio_target(tmp_path, "x", "nope")
    assert obs_store.set_studio_target(tmp_path, "x", None) == {"studio": "x", "target": None}
    assert obs_store.studio_target(tmp_path, "x") is None
    assert obs_store.resolve_studio(tmp_path, "x") is None


@pytest.mark.unit
def test_legacy_inline_still_resolves(tmp_path):
    """Old {"host", ...} studio entries work with no crypto touched."""
    local = tmp_path / "studios.local"
    local.mkdir()
    (local / "obs.json").write_text(
        json.dumps({"studios": {"x": {"host": "192.0.2.9", "password": "pw"}}}),
        encoding="utf-8",
    )
    assert obs_store.resolve_studio(tmp_path, "x") == {
        "host": "192.0.2.9",
        "port": 4455,
        "password": "pw",
    }
    assert obs_store.studio_target(tmp_path, "x") is None  # no named target yet


@pytest.mark.unit
def test_legacy_migrates_on_first_write(tmp_path, needs_crypto):
    """First admin write folds inline entries into encrypted named targets."""
    local = tmp_path / "studios.local"
    local.mkdir()
    (local / "obs.json").write_text(
        json.dumps({"studios": {"x": {"host": "192.0.2.9", "password": "pw"}}}),
        encoding="utf-8",
    )
    obs_store.create_target(tmp_path, "fresh", "192.0.2.10")
    assert obs_store.studio_target(tmp_path, "x") == "x-obs"
    assert obs_store.resolve_studio(tmp_path, "x") == {
        "host": "192.0.2.9",
        "port": 4455,
        "password": "pw",
    }
    raw = _raw_store(tmp_path)
    assert '"pw"' not in raw and obs_store.ENC_PREFIX in raw


@pytest.mark.unit
def test_validation_and_conflicts(tmp_path):
    """Names, hosts, and ports are gated; duplicates conflict."""
    obs_store.create_target(tmp_path, "main", "192.0.2.5")
    with pytest.raises(TargetConflictError):
        obs_store.create_target(tmp_path, "main", "192.0.2.6")
    for bad_name in ("", "  ", "a/b", "x" * 65):
        with pytest.raises(ObsTargetError):
            obs_store.create_target(tmp_path, bad_name, "192.0.2.6")
    for bad_host in ("", "   "):
        with pytest.raises(ObsTargetError):
            obs_store.create_target(tmp_path, "h", bad_host)
    for bad_port in (0, 70000, True, "4455"):
        with pytest.raises(ObsTargetError):
            obs_store.create_target(tmp_path, "p", "192.0.2.6", bad_port)
    with pytest.raises(ObsTargetError):
        obs_store.create_target(tmp_path, "e", "192.0.2.6", 4455, "")


@pytest.mark.unit
def test_malformed_store_raises(tmp_path):
    """A corrupt file errors loudly instead of reading as empty."""
    local = tmp_path / "studios.local"
    local.mkdir()
    (local / "obs.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(ObsStoreError):
        obs_store.list_targets(tmp_path)


@pytest.mark.unit
def test_lost_key_is_honest(tmp_path, needs_crypto):
    """Ciphertext without its key file raises (re-enter, never silent)."""
    obs_store.create_target(tmp_path, "main", "192.0.2.5", 4455, SECRET)
    (tmp_path / "studios.local" / obs_store.KEY_FILENAME).unlink()
    with pytest.raises(ObsCryptoError):
        obs_store.resolve_target(tmp_path, "main")


@pytest.mark.unit
def test_missing_crypto_backend_is_honest(tmp_path, monkeypatch):
    """No cryptography package: writes needing it fail as unavailable."""

    def _boom():
        raise ImportError("nope")

    monkeypatch.setattr(obs_store, "_fernet_cls", _boom)
    with pytest.raises(ObsCryptoUnavailable):
        obs_store.encrypt_secret(tmp_path, SECRET)
    # Passwordless targets never touch the backend.
    assert obs_store.create_target(tmp_path, "plain", "192.0.2.5")["has_password"] is False


@pytest.mark.unit
def test_modes_applier_resolves_named_target(tmp_path, monkeypatch):
    """Modes follow the studio's selected target through the store."""
    from app.api import modes as modes_api
    from app.drivers.base import DriverResult
    from app.registry import Registry

    local = tmp_path / "studios.local"
    local.mkdir()
    (local / "obs.json").write_text(
        json.dumps({
            "targets": {"t": {"host": "192.0.2.9", "port": 4456, "password": None}},
            "studios": {"x": {"target": "t"}},
        }),
        encoding="utf-8",
    )
    made = {}

    class FakeObs:
        def __init__(self, driver_id, host, port, password, **kwargs):
            made["ctor"] = (driver_id, host, port, password)

        def apply(self, command):
            return DriverResult(ok=True, state={"scene": command["scene"]})

        def safe_stop(self):
            return DriverResult(ok=True, state={"connected": False})

    monkeypatch.setattr(modes_api, "ObsWsDriver", FakeObs)
    loaded = {
        "studio": {"id": "x", "label": "X"},
        "devices": [],
        "zones": [],
        "modes": [],
        "presets": {},
    }
    applier = modes_api._obs_applier(Registry.from_loaded(loaded, {}), tmp_path)
    assert callable(applier)
    assert applier("Main").ok is True
    assert made["ctor"] == ("obs-modes", "192.0.2.9", 4456, None)


@pytest.mark.unit
def test_api_crud_roundtrip_never_echoes_secret(tmp_path, needs_crypto):
    """Admin CRUD over HTTP; the password appears in no response."""
    client = TestClient(app)
    with _tmp_local_studio(tmp_path):
        created = client.post("/api/obs/targets", json={
            "name": "main", "host": "192.0.2.5", "port": 4455, "password": SECRET,
        })
        assert created.status_code == 201
        assert created.json()["target"]["has_password"] is True
        listed = client.get("/api/obs/targets")
        assert [t["name"] for t in listed.json()["targets"]] == ["main"]
        patched = client.put("/api/obs/targets/main", json={"host": "192.0.2.7"})
        assert patched.json()["target"]["host"] == "192.0.2.7"
        for response in (created, listed, patched):
            assert SECRET not in response.text
        assert SECRET not in _raw_store(tmp_path)
        assert client.delete("/api/obs/targets/main").status_code == 200
        assert client.get("/api/obs/targets").json() == {"targets": []}


@pytest.mark.unit
def test_api_conflicts_and_bad_shapes(tmp_path, needs_crypto):
    """Duplicate 409, unknown 404, bad data 422."""
    client = TestClient(app)
    with _tmp_local_studio(tmp_path):
        base = {"name": "main", "host": "192.0.2.5"}
        assert client.post("/api/obs/targets", json=base).status_code == 201
        assert client.post("/api/obs/targets", json=base).status_code == 409
        assert client.put("/api/obs/targets/nope", json={"host": "x"}).status_code == 404
        assert client.delete("/api/obs/targets/nope").status_code == 404
        assert client.post("/api/obs/targets/nope/test", json={}).status_code == 404
        assert client.post("/api/obs/targets", json={"name": "", "host": "x"}).status_code == 422
        assert client.post("/api/obs/targets", json={"name": "p", "host": "x", "port": 0}).status_code == 422
        assert client.put("/api/obs/targets/main", json={"port": 99999}).status_code == 422
        assert client.put("/api/obs/targets/main", json={"password": ""}).status_code == 422
        client.put("/api/studios/x/obs/target", json={"target": "main"})
        assert client.delete("/api/obs/targets/main").status_code == 409


@pytest.mark.unit
def test_api_studio_selection(tmp_path):
    """Per-studio default get/set/clear; unknown studio 404s."""
    client = TestClient(app)
    with _tmp_local_studio(tmp_path):
        assert client.get("/api/studios/nope/obs/target").status_code == 404
        assert client.put("/api/studios/nope/obs/target", json={"target": None}).status_code == 404
        assert client.get("/api/studios/x/obs/target").json() == {
            "studio": "x", "target": None, "targets": [],
        }
        client.post("/api/obs/targets", json={"name": "main", "host": "192.0.2.5"})
        assert client.put("/api/studios/x/obs/target", json={"target": "nope"}).status_code == 422
        assert client.put("/api/studios/x/obs/target", json={"target": "main"}).json() == {
            "studio": "x", "target": "main",
        }
        assert client.get("/api/studios/x/obs/target").json()["target"] == "main"
        assert client.put("/api/studios/x/obs/target", json={"target": None}).json()["target"] is None


@pytest.mark.unit
def test_api_test_uses_stored_credentials(tmp_path, needs_crypto, monkeypatch):
    """Test probes with the decrypted password — and never echoes it."""
    from app.api import obs_targets as targets_api
    from app.drivers.base import DriverResult

    made = {}

    class FakeObs:
        def __init__(self, driver_id, host, port, password):
            made["ctor"] = (driver_id, host, port, password)

        def probe(self):
            return DriverResult(ok=True, state={"obsVersion": "9.9.9"})

        def safe_stop(self):
            made["closed"] = True
            return DriverResult(ok=True, state={"connected": False})

    monkeypatch.setattr(targets_api, "ObsWsDriver", FakeObs)
    client = TestClient(app)
    with _tmp_local_studio(tmp_path):
        client.post("/api/obs/targets", json={
            "name": "main", "host": "192.0.2.5", "password": SECRET,
        })
        response = client.post("/api/obs/targets/main/test", json={})
        assert response.status_code == 200
        assert response.json()["ok"] is True
        assert made["ctor"] == ("obs-test-main", "192.0.2.5", 4455, SECRET)
        assert made.get("closed") is True
        assert SECRET not in response.text


@pytest.mark.unit
def test_api_password_write_without_backend_is_503(tmp_path, monkeypatch):
    """Hub without cryptography: password writes fail honestly, not silently."""

    def _boom():
        raise ImportError("nope")

    monkeypatch.setattr(obs_store, "_fernet_cls", _boom)
    client = TestClient(app)
    with _tmp_local_studio(tmp_path):
        response = client.post("/api/obs/targets", json={
            "name": "main", "host": "192.0.2.5", "password": SECRET,
        })
        assert response.status_code == 503
        assert client.post("/api/obs/targets", json={
            "name": "plain", "host": "192.0.2.5",
        }).status_code == 201


@pytest.mark.unit
def test_studio_status_disconnects_once(tmp_path, monkeypatch):
    """Status reuses one connection for its calls, then disconnects."""
    from app.drivers.base import DriverResult

    closed = []

    class FakeObs:
        def __init__(self, driver_id, host="127.0.0.1", port=4455, password=None):
            pass

        def apply(self, command):
            action = command.get("action")
            if action == "scenes":
                return DriverResult(ok=True, state={"scenes": [], "currentProgramSceneName": None})
            return DriverResult(ok=True, state={})

        def safe_stop(self):
            closed.append(1)
            return DriverResult(ok=True, state={"connected": False})

    monkeypatch.setattr("app.api.obs.ObsWsDriver", FakeObs)
    client = TestClient(app)
    with _tmp_local_studio(tmp_path):
        client.post("/api/obs/targets", json={"name": "main", "host": "192.0.2.5"})
        client.put("/api/studios/x/obs/target", json={"target": "main"})
        response = client.get("/api/studios/x/obs/status")
        assert response.status_code == 200
        assert response.json()["connected"] is True
        assert len(closed) == 1


@pytest.mark.unit
def test_obs_page_markers():
    """Admin page: table, form, studio defaults, all wired to the API."""
    html = (STATIC_DIR / "obs.html").read_text(encoding="utf-8")
    for marker in (
        'id="tg-rows"',
        'id="tg-name"',
        'id="tg-host"',
        'id="tg-port"',
        'id="tg-pass"',
        'id="tg-nopass"',
        'id="st-rows"',
        "tgLoad(",
        "tgSave()",
        "tgEdit(",
        "tgDelete(",
        "tgTest(",
        "stLoad()",
        "stSave(",
        "/api/obs/targets",
        "/obs/target",
    ):
        assert marker in html


@pytest.mark.unit
def test_obs_page_served():
    """GET /obs returns the admin page."""
    client = TestClient(app)
    response = client.get("/obs")
    assert response.status_code == 200
    assert "OBS targets" in response.text


@pytest.mark.unit
def test_dashboard_target_dropdown_markers():
    """Dashboard OBS card can switch the studio's target in place."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    for marker in (
        'id="obs-target"',
        "loadObsTargets(",
        "obsUseTarget()",
        "/obs/target",
    ):
        assert marker in html


@pytest.mark.unit
def test_dashboard_scene_dropdown_markers():
    """Scenes pick from a dropdown (current preselected), Cut applies it."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    for marker in (
        'id="obs-scene"',
        "obsCut()",
        "obsSwitch(",
        "Cut to scene",
    ):
        assert marker in html
    assert "obsSwitch('" not in html  # no more per-scene buttons


@pytest.mark.unit
def test_identify_close_reports_password_hint(monkeypatch):
    """Server closing after Identify reads as a wrong-password hint."""
    import sys

    from app.drivers import obs_ws
    from app.drivers.obs_ws import ObsError

    frames = [
        json.dumps({"op": 0, "d": {"authentication": {"salt": "s", "challenge": "c"}}}),
        Exception("closed"),
    ]

    class FakeSock:
        def send(self, data):
            pass

        def recv(self):
            nxt = frames.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt

        def close(self):
            pass

    class FakeWsMod:
        @staticmethod
        def create_connection(url, timeout=None):
            return FakeSock()

    monkeypatch.setitem(sys.modules, "websocket", FakeWsMod)
    with pytest.raises(ObsError, match="wrong WebSocket password"):
        obs_ws.ObsWsClient.connect("obs-pc", 4455, "nope")


@pytest.mark.unit
def test_nav_links_obs_targets_everywhere():
    """Every nav bar reaches the admin page."""
    for page in ("index.html", "control.html", "equipment.html", "modality.html",
                 "ordering.html", "bench.html", "obs.html"):
        html = (STATIC_DIR / page).read_text(encoding="utf-8")
        assert 'href="/obs"' in html, page
