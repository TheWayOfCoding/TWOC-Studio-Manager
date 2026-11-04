# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ordering store, zone collections, hub shutdown gate, Kasa priming."""

import json
from pathlib import Path

import pytest
from app.config.ordering import OrderingError, load_ordering, ordered_ids, save_ordering
from app.main import create_app
from fastapi.testclient import TestClient


@pytest.mark.unit
def test_ordered_ids_custom_then_alpha():
    assert ordered_ids(["c", "a"], ["b", "a", "c", "d"]) == ["c", "a", "b", "d"]
    assert ordered_ids(None, ["b", "a"]) == ["a", "b"]
    assert ordered_ids(["gone"], ["b", "a"]) == ["a", "b"]


@pytest.mark.unit
def test_ordering_save_local_only_and_validates(tmp_path):
    (tmp_path / "studios.local" / "x").mkdir(parents=True)
    (tmp_path / "studios" / "y").mkdir(parents=True)
    valid = {"zones": {"a", "b"}, "devices": set(), "modes": set(), "presets": set(), "plugs": set()}
    clean = save_ordering(tmp_path, "x", {"zones": ["b", "a"]}, valid)
    assert clean == {"zones": ["b", "a"]}
    assert load_ordering(tmp_path, "x") == {"zones": ["b", "a"]}
    with pytest.raises(OrderingError):
        save_ordering(tmp_path, "y", {"zones": ["a"]}, valid)
    with pytest.raises(OrderingError):
        save_ordering(tmp_path, "x", {"zones": ["nope"]}, valid)
    with pytest.raises(OrderingError):
        save_ordering(tmp_path, "x", {"bogus": []}, valid)


@pytest.mark.unit
def test_ordering_api_and_zone_devices():
    client = TestClient(create_app())
    zones = client.get("/api/studios/example-studio/zones").json()["zones"]
    bench = next(z for z in zones if z["id"] == "bench")
    assert {d["id"] for d in bench["devices"]} == {"demo-cam", "demo-key", "demo-rgb"}
    assert bench["devices"][0]["name"]
    ordering = client.get("/api/studios/example-studio/ordering").json()
    assert set(ordering["collections"]) == {"zones", "devices", "modes", "presets", "plugs", "panels", "modalities"}
    assert [p["id"] for p in ordering["collections"]["panels"]] == [
        "modalities", "obs", "zones", "power", "hub", "ai"]
    # Tracked example studio refuses saves (local-only rule).
    save = client.put("/api/studios/example-studio/ordering", json={"ordering": {"zones": ["desk", "bench"]}})
    assert save.status_code == 422
    assert client.get("/api/studios/nope/ordering").status_code == 404


@pytest.mark.unit
def test_hub_shutdown_requires_confirm_and_windows(monkeypatch):
    from app.api import routes

    client = TestClient(create_app())
    assert client.post("/api/hub/shutdown", json={}).status_code == 422
    assert client.post("/api/hub/shutdown", json={"confirm": False}).status_code == 422
    calls = []
    monkeypatch.setattr(routes, "_launch_hub_shutdown", lambda: calls.append(True) or True)
    monkeypatch.setattr(routes.os, "name", "nt")
    r = client.post("/api/hub/shutdown", json={"confirm": True})
    assert r.status_code == 200
    assert r.json()["shutting_down"] is True
    assert calls == [True]


@pytest.mark.unit
def test_kasa_official_plugs_from_map(tmp_path, monkeypatch):
    # Point the app config root at a fixture tree with a plug-map.
    import app.api.kasa as kasa_api

    root = tmp_path
    (root / "studios.local").mkdir(parents=True)
    (root / "studios.local" / "plug-map.json").write_text(json.dumps({"plugs": [
        {"plug": "plug-2", "alias": "Studio Plug 2", "model": "HS103", "zone": "workstation-content", "powerGroup": "workstation-content"},
        {"plug": "plug-1", "alias": "Studio Plug 1", "model": "HS103", "zone": "workstation-computer", "powerGroup": "workstation-computer"},
    ]}), encoding="utf-8")
    entries = kasa_api._plug_map(root)
    assert [e["plug"] for e in entries] == ["plug-2", "plug-1"]


@pytest.mark.unit
def test_kasa_prime_debounces(monkeypatch):
    from app.drivers import kasa

    calls = {"n": 0}

    def fake_discover(username=None, password=None, discovery_timeout=4):
        calls["n"] += 1
        return [{"host": "192.0.2.1", "alias": "Studio Plug 1", "model": "HS103", "on": True}]

    monkeypatch.setattr(kasa, "discover_plugs", fake_discover)
    kasa._prime_state["at"] = 0.0
    kasa._prime_state["plugs"] = []
    first = kasa.prime_plugs()
    second = kasa.prime_plugs()
    forced = kasa.prime_plugs(force=True)
    assert first == second == forced
    assert calls["n"] == 2  # second call served from the debounce window
    assert kasa.cached_alias_hosts()["Studio Plug 1"] == "192.0.2.1"


@pytest.mark.unit
def test_kasa_official_plug_endpoints(monkeypatch):
    """Official plugs: static list, alias-resolved state/set, 404s."""
    from app.api import kasa as kasa_api
    from app.drivers.base import DriverResult

    class FakeDriver:
        instances = {}

        def __init__(self, driver_id, host, username=None, password=None):
            self.driver_id, self.host = driver_id, host
            FakeDriver.instances[driver_id] = self
            self.applied = []

        def get_state(self):
            return DriverResult(ok=True, state={"power": "on", "alias": "Studio Plug 1", "model": "HS103"})

        def apply(self, command):
            self.applied.append(command)
            return DriverResult(ok=True, state={"power": "off" if command.get("on") is False else "on"})

    monkeypatch.setattr(kasa_api, "KasaDriver", FakeDriver)
    monkeypatch.setattr(kasa_api, "resolve_host", lambda alias, u=None, p=None, fallback=None: "192.0.2.9")
    client = TestClient(create_app())
    plugs = client.get("/api/kasa/plugs")
    assert plugs.status_code == 200
    ids = [p["plug"] for p in plugs.json()["plugs"]]
    assert ids == ["plug-1", "plug-2", "plug-3", "plug-4", "plug-5", "plug-6"]
    state = client.get("/api/kasa/plugs/plug-1/state")
    assert state.status_code == 200 and state.json()["state"]["power"] == "on"
    assert FakeDriver.instances["plug-1"].host == "192.0.2.9"  # alias-resolved, not the stored fallback
    off = client.post("/api/kasa/plugs/plug-1/set", json={"on": False})
    assert off.json()["state"]["power"] == "off"
    assert client.get("/api/kasa/plugs/plug-99/state").status_code == 404
    assert client.post("/api/kasa/plugs/plug-99/set", json={"on": True}).status_code == 404


@pytest.mark.unit
def test_kasa_prime_endpoint(monkeypatch):
    from app.api import kasa as kasa_api

    monkeypatch.setattr(kasa_api, "prime_plugs", lambda u=None, p=None, force=False: [{"host": "192.0.2.1", "alias": "Studio Plug 1"}])
    client = TestClient(create_app())
    r = client.post("/api/testbench/kasa/prime", json={})
    assert r.status_code == 200 and r.json()["ok"] is True and len(r.json()["plugs"]) == 1


@pytest.mark.unit
def test_hub_shutdown_helper_failure_paths():
    from app.api.routes import _launch_hub_shutdown

    class Proc:
        def __init__(self, rc):
            self.returncode = rc

    with pytest.raises(RuntimeError):
        _launch_hub_shutdown(runner=lambda *a, **k: Proc(1))

    calls = []

    def ok_runner(args, **kwargs):
        calls.append(args[1])
        return Proc(0)

    assert _launch_hub_shutdown(runner=ok_runner) is True
    assert calls == ["/Create", "/Run"]


@pytest.mark.unit
def test_equipment_properties_endpoint(tmp_path):
    """Editable built-ins persist locally; identity fields cannot move."""
    from app.api.equipment import DetailsSaveError, save_properties

    root = tmp_path
    studio_dir = root / "studios.local" / "x"
    studio_dir.mkdir(parents=True)
    (root / "profiles").mkdir()
    (studio_dir / "studio.json").write_text(json.dumps({"id": "x", "label": "X"}), encoding="utf-8")
    (studio_dir / "zones.json").write_text(json.dumps([{"id": "z", "label": "Z"}]), encoding="utf-8")
    (studio_dir / "devices.json").write_text(json.dumps([
        {"id": "d1", "name": "Old", "type": "light", "zone": "z"}]), encoding="utf-8")
    (studio_dir / "modes.json").write_text("[]", encoding="utf-8")
    (studio_dir / "presets.json").write_text("{}", encoding="utf-8")
    registry = save_properties(root, "x", "d1", {"name": "Key Light", "estWatts": 190})
    assert registry.get_device("d1")["name"] == "Key Light"
    assert registry.get_device("d1")["zone"] == "z"
    with pytest.raises(DetailsSaveError):
        save_properties(root, "x", "d1", {"name": ""})


@pytest.mark.unit
def test_modality_engine_beats_and_zone_expansion():
    """Beats expand zone lights, fold scenes, and hold between beats."""
    from app.config.loader import load_all
    from app.modules.modality import ModalityError, apply_modality, beat_to_content, plan_modality
    from app.registry import Registry

    data = load_all(Path(__file__).resolve().parents[2] / "config")
    registry = Registry.from_loaded(data["studios"]["example-studio"], data["profiles"])
    content = beat_to_content(registry, {"actions": {"zoneLights": {"bench": {"intensity": 10}}}})
    assert content["lights"]["demo-key"] == {"intensity": 10}
    assert content["lights"]["demo-rgb"] == {"intensity": 10}

    calls = []

    class FakeModes:
        def apply_custom(self, plan_id, content, **kwargs):
            calls.append((plan_id, content, kwargs))
            return {"ok": True, "subsystems": {}}

    FakeModes.registry = registry
    modality = {"label": "T", "beats": [
        {"label": "a", "hold_s": 3, "actions": {"lights": {"demo-key": {"intensity": 5}}}},
        {"label": "b", "stay": True, "actions": {"obsScene": "Main"}},
    ]}
    sleeps = []
    result = apply_modality(FakeModes(), "t", modality, sleep=sleeps.append)
    assert result["ok"] is True and len(result["beats"]) == 2
    assert sleeps == [3]  # hold between beats only; last beat stays
    assert calls[0][2]["allow_scenes"] is True
    with pytest.raises(ModalityError):
        plan_modality(registry, "bad", {"beats": [{"stay": True, "actions": {}}, {"actions": {}}]})


@pytest.mark.unit
def test_dual_protocol_transport_selection():
    """Ninja 400/20 follow transport; single-protocol devices refuse."""
    from app.modules.modes import effective_family

    dual = {"driver": "ninja", "transport": "dmx",
            "driverAddress": {"channel": 3, "group": "B", "dmx": {"dimmer": 1, "cct": 2}}}
    assert effective_family(dual) == "dmx"
    dual["transport"] = "ble"
    assert effective_family(dual) == "ninja"
    assert effective_family({"driver": "gvm", "transport": "dmx"}) == "gvm"

    client = TestClient(create_app())
    bad = client.put("/api/studios/home-studio/devices/gvm-pc/transport", json={"transport": "dmx"})
    assert bad.status_code == 422
    bad2 = client.put("/api/studios/home-studio/devices/ninja-400/transport", json={"transport": "wifi"})
    assert bad2.status_code == 422


@pytest.mark.unit
def test_zone_power_dependency_brings_parent_first():
    """A zone whose strip hangs off another zone's plug powers parent first."""
    from pathlib import Path as _P

    from app.config.loader import load_all
    from app.drivers.base import DriverResult
    from app.modules.power import PowerModule
    from app.registry import Registry

    data = load_all(_P(__file__).resolve().parents[2] / "config")
    registry = Registry.from_loaded(data["studios"]["home-studio"], data["profiles"])
    order = []

    class Rec:
        def __init__(self, pid):
            self.driver_id = pid

        def apply(self, command):
            order.append((self.driver_id, command["on"]))
            return DriverResult(ok=True, state={"power": "on" if command["on"] else "off"})

    module = PowerModule("power", registry,
                         {"plug-4": Rec("plug-4"), "plug-5": Rec("plug-5")})
    results = module.set_zone_power("hands-on-screens", True, sleep=lambda s: None)
    assert order[0] == ("plug-4", True)  # parent strip first
    assert ("plug-5", True) in order
    assert all(r["ok"] for r in results)
    order.clear()
    module.set_zone_power("hands-on-screens", False, sleep=lambda s: None)
    assert order == [("plug-5", False)]  # off never touches the parent


@pytest.mark.unit
def test_screen_fx_endpoints():
    """Audio FX over HTTP: list, fire (seq stamps), unknown rejected."""
    client = TestClient(create_app())
    listed = client.get("/api/testbench/screen/fx")
    assert listed.status_code == 200 and "coin" in listed.json()["fx"]
    fired = client.post("/api/testbench/screen/fx", json={"screen": "fx-probe", "fx": "coin", "volume": 25})
    assert fired.status_code == 200 and fired.json()["ok"] is True
    assert fired.json()["state"]["fx"] == {"name": "coin", "seq": 1, "volume": 25}
    bad = client.post("/api/testbench/screen/fx", json={"screen": "fx-probe", "fx": "airhorn"})
    assert bad.json()["ok"] is False


@pytest.mark.unit
def test_room_manifest_lists_unswitchable_room_lights():
    from app.ai.director import build_manifest
    from app.config.loader import load_all
    from app.registry import Registry

    data = load_all(Path(__file__).resolve().parents[2] / "config")
    registry = Registry.from_loaded(data["studios"]["home-studio"], data["profiles"])
    manifest = build_manifest(registry)
    room_ids = {entry["id"] for entry in manifest["room"]}
    assert room_ids == {"room-clamp", "crapy-pt15b", "room-ceiling"}
    assert all(entry["switchable"] is False for entry in manifest["room"])
    assert not (room_ids & set(manifest["lights"]))
    zone_ids = registry.zone_ids
    assert zone_ids[:2] == ["hub", "workstation-computer"]
    assert "ambient-mood" in zone_ids and "room" in zone_ids
