# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the REST API surface (mock drivers, example studio)."""

import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.drivers.base import DriverResult
from app.main import app
from app.registry import Registry

client = TestClient(app)
CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config"


class StubUniverse:
    """Recording stand-in for the shared DMX universe driver (no hardware)."""

    def __init__(self):
        self.channels = {}
        self.calls = []

    def apply(self, command):
        self.calls.append(("apply", dict(command["channels"])))
        self.channels.update(command["channels"])
        return DriverResult(ok=True, state={"channels": dict(self.channels)})

    def fade(self, target, seconds, units="dmx", dwell_s=None):
        self.calls.append(("fade", dict(target), units, dwell_s))
        if units == "dmx":
            self.channels.update(target)
        return 1

    def get_state(self):
        return DriverResult(ok=True, state={"channels": dict(self.channels)})


class FakeGvm:
    """Recording stand-in for a GVM BLE driver (no radio)."""

    instances = {}

    def __init__(self, driver_id, address, timeout=15):
        self.driver_id = driver_id
        self.calls = []
        FakeGvm.instances[driver_id] = self

    def apply(self, command):
        self.calls.append(dict(command))
        return DriverResult(ok=True, state=dict(command))


@pytest.mark.unit
def test_hub_restart_needs_confirm(monkeypatch):
    """Restart is destructive (site drops): no confirm, no launch."""
    from app.api import routes

    launches = []
    monkeypatch.setattr(routes, "_launch_hub_restart", lambda: launches.append(1))
    assert client.post("/api/hub/restart", json={}).status_code == 422
    assert client.post("/api/hub/restart", json={"confirm": False}).status_code == 422
    assert launches == []


@pytest.mark.unit
def test_hub_restart_launches_helper(monkeypatch):
    from app.api import routes

    launches = []
    monkeypatch.setattr(routes, "_launch_hub_restart", lambda: launches.append(1) or True)
    response = client.post("/api/hub/restart", json={"confirm": True})
    assert response.status_code == 200
    assert response.json()["restarting"] is True
    assert launches == [1]


@pytest.mark.unit
def test_hub_restart_launch_failure_is_honest(monkeypatch):
    from app.api import routes

    def boom():
        raise RuntimeError("schtasks said no")

    monkeypatch.setattr(routes, "_launch_hub_restart", boom)
    response = client.post("/api/hub/restart", json={"confirm": True})
    assert response.status_code == 503
    assert "restart not started" in response.json()["detail"]


@pytest.mark.unit
def test_hub_restart_helper_task_shape():
    """The helper restarts the main task from outside, then self-deletes."""
    from types import SimpleNamespace

    from app.api import routes

    calls = []

    def fake_runner(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0)

    assert routes._launch_hub_restart(runner=fake_runner) is True
    create, run = calls
    assert create[:3] == ["schtasks", "/Create", "/TN"]
    assert routes.HELPER_TASK in create
    helper_cmd = create[create.index("/TR") + 1]
    assert "/End /TN " + routes.MAIN_TASK in helper_cmd
    assert "/Run /TN " + routes.MAIN_TASK in helper_cmd
    assert "/Delete /TN " + routes.HELPER_TASK in helper_cmd
    assert run == ["schtasks", "/Run", "/TN", routes.HELPER_TASK]


@pytest.mark.unit
def test_health_ok():
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "example-studio" in body["studios"]
    assert "version" in body


@pytest.mark.unit
def test_studios_list():
    response = client.get("/api/studios")
    assert response.status_code == 200
    assert {"id": "example-studio", "label": "Example Studio", "source": "studios"} in response.json()


@pytest.mark.unit
def test_studio_devices_merged():
    response = client.get("/api/studios/example-studio/devices")
    assert response.status_code == 200
    devices = {item["id"]: item for item in response.json()}
    assert sorted(devices) == ["demo-cam", "demo-key", "demo-rgb", "demo-screen"]
    assert devices["demo-key"]["spec"]["maxWatts"] == 60


@pytest.mark.unit
def test_unknown_studio_404():
    assert client.get("/api/studios/nope/devices").status_code == 404


@pytest.mark.unit
def test_bench_drivers_list():
    response = client.get("/api/testbench/drivers")
    assert response.status_code == 200
    ids = sorted(item["id"] for item in response.json())
    assert ids == ["mock-dmx", "mock-gvm", "mock-ir", "mock-kasa", "mock-obs", "mock-screen"]


@pytest.mark.unit
def test_bench_probe_and_apply():
    probe = client.post("/api/testbench/mock-kasa/probe")
    assert probe.status_code == 200
    assert probe.json()["ok"] is True
    apply = client.post("/api/testbench/mock-kasa/apply", json={"command": {"power": "on"}})
    assert apply.status_code == 200
    assert apply.json()["state"]["power"] == "on"


@pytest.mark.unit
def test_bench_unknown_driver_404():
    assert client.post("/api/testbench/nope/probe").status_code == 404


@pytest.mark.unit
def test_gvm_probe_route_reaches_real_driver(monkeypatch):
    """Real-driver gvm/probe must not be shadowed by the generic mock bench route."""
    monkeypatch.setitem(sys.modules, "bleak", None)
    response = client.post("/api/testbench/gvm/probe", json={"address": "AA:BB:CC:DD:EE:FF"})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "bleak" in (body["error"] or "")


@pytest.mark.unit
def test_ir_state_port_selection(monkeypatch):
    """Empty port defers to the server default; explicit port and IR_PORT win."""
    seen = {}

    class FakeIrDriver:
        def __init__(self, driver_id, port=None):
            seen["port"] = port

        def get_state(self):
            return DriverResult(ok=True, state={"port": seen["port"]}, error=None)

    monkeypatch.setattr("app.api.ir.IrUsbSerialDriver", FakeIrDriver)
    monkeypatch.setattr("app.api.ir.default_port", lambda: None)
    monkeypatch.delenv("IR_PORT", raising=False)
    assert client.post("/api/testbench/ir/state", json={}).json()["state"]["port"] is None
    assert client.post("/api/testbench/ir/state", json={"port": "COM9"}).json()["state"]["port"] == "COM9"
    monkeypatch.setenv("IR_PORT", "COM7")
    assert client.post("/api/testbench/ir/state", json={}).json()["state"]["port"] == "COM7"


PANEL_MAC = "AA:BB:CC:DD:EE:FF"


@contextmanager
def _panel_registries():
    """Swap in one addressed + one unaddressed GVM panel (fictional addresses)."""
    studio = {"id": "s1", "label": "S1"}
    devices = [
        {
            "id": "key",
            "name": "Key panel",
            "type": "light",
            "zone": "z",
            "driver": "gvm",
            "driverAddress": {"ble": PANEL_MAC},
        },
        {"id": "back", "name": "Back panel", "type": "light", "zone": "z", "driver": "gvm"},
        {"id": "cam", "name": "Cam", "type": "camera", "zone": "z"},
    ]
    zones = [{"id": "z", "name": "Z"}]
    registries = app.state.registries
    saved = dict(registries)
    registries.clear()
    registries["s1"] = Registry(studio, devices, zones, [], {}, {})
    try:
        yield
    finally:
        registries.clear()
        registries.update(saved)


@pytest.mark.unit
def test_gvm_panels_lists_names_only():
    """Panels endpoint lists configured panels without ever exposing addresses."""
    with _panel_registries():
        response = client.get("/api/testbench/gvm/panels")
        assert response.status_code == 200
        assert response.json() == {"panels": [{"id": "s1/key", "name": "Key panel", "studio": "s1"}]}
        assert PANEL_MAC not in response.text


@pytest.mark.unit
def test_gvm_set_by_panel_resolves_and_scrubs(monkeypatch):
    """Panel ids resolve server-side; responses echo the name, not the address."""
    seen = {}

    class FakeGvmDriver:
        def __init__(self, driver_id, address, timeout=15):
            seen["address"] = address

        def apply(self, command):
            return DriverResult(
                ok=False,
                state={"address": seen["address"], **command},
                error=f"could not connect to {seen['address']}",
            )

    monkeypatch.setattr("app.api.gvm.GvmBleDriver", FakeGvmDriver)
    with _panel_registries():
        response = client.post("/api/testbench/gvm/set", json={"panel": "s1/key", "brightness": 5})
        assert response.status_code == 200
        assert seen["address"] == PANEL_MAC
        assert PANEL_MAC not in response.text
        assert "Key panel" in response.text


@pytest.mark.unit
def test_gvm_panel_errors():
    """Unknown/unaddressed panels 404; panel+address or neither is 422."""
    with _panel_registries():
        assert client.post("/api/testbench/gvm/state", json={"panel": "s1/nope"}).status_code == 404
        assert client.post("/api/testbench/gvm/state", json={"panel": "nope/key"}).status_code == 404
        assert client.post("/api/testbench/gvm/state", json={"panel": "s1/back"}).status_code == 404
        body = {"panel": "s1/key", "address": PANEL_MAC}
        assert client.post("/api/testbench/gvm/state", json=body).status_code == 422
        assert client.post("/api/testbench/gvm/state", json={}).status_code == 422


@pytest.mark.unit
def test_modes_list():
    response = client.get("/api/studios/example-studio/modes")
    assert response.status_code == 200
    body = response.json()
    assert body["default"] == "demo_work"
    assert [(item["id"], item["label"]) for item in body["modes"]] == [
        ("demo_stream", "Demo Stream"),
        ("demo_work", "Demo Work"),
    ]
    assert [item["powersOff"] for item in body["modes"]] == [False, False]


@contextmanager
def _powers_off_registries():
    """One studio with a power-off mode and a plain mode."""
    studio = {"id": "s1", "label": "S1"}
    modes = [
        {"id": "off", "label": "Off", "powerOff": ["g"]},
        {"id": "on", "label": "On"},
    ]
    registries = app.state.registries
    saved = dict(registries)
    registries.clear()
    registries["s1"] = Registry(studio, [], [], modes, {}, {})
    try:
        yield
    finally:
        registries.clear()
        registries.update(saved)


@pytest.mark.unit
def test_modes_list_flags_power_off_modes():
    """Modes list marks power-off modes so clients can see which cut power."""
    with _powers_off_registries():
        response = client.get("/api/studios/s1/modes")
        assert response.status_code == 200
        flags = {item["id"]: item["powersOff"] for item in response.json()["modes"]}
        assert flags == {"off": True, "on": False}


@contextmanager
def _mode_power_registries():
    """One studio with an auto and a manual-only plug behind a powerOff mode."""
    studio = {"id": "s1", "label": "S1"}
    devices = [
        {
            "id": "lamp",
            "name": "Lamp",
            "type": "light",
            "zone": "z",
            "powerGroup": "g-auto",
            "powerPlug": "plug-1",
            "powerOffPolicy": "auto",
        },
        {
            "id": "rig",
            "name": "Rig",
            "type": "camera",
            "zone": "z",
            "powerGroup": "g-safe",
            "powerPlug": "plug-2",
            "powerOffPolicy": "manual-only",
        },
    ]
    zones = [{"id": "z", "name": "Z"}]
    modes = [{"id": "off", "label": "Off", "powerOff": ["g-auto", "g-safe"]}]
    registries = app.state.registries
    saved = dict(registries)
    registries.clear()
    registries["s1"] = Registry(studio, devices, zones, modes, {}, {})
    try:
        yield
    finally:
        registries.clear()
        registries.update(saved)


@pytest.mark.unit
def test_mode_apply_include_manual_cuts_protected(monkeypatch):
    """Mode apply holds protected zones by default; the flag cuts them."""
    from app.drivers.mock import MockDriver

    drivers = {plug: MockDriver(plug) for plug in ("plug-1", "plug-2")}
    monkeypatch.setattr("app.api.modes._plug_drivers", lambda registry, root: drivers)
    with _mode_power_registries():
        default = client.post("/api/studios/s1/modes/off/apply", json={})
        assert default.status_code == 200
        power = default.json()["subsystems"]["power"]
        assert power["status"] == "ready"
        assert power["protected"] == [
            {"device": "rig", "plug": "plug-2", "reason": "manual-only (tick the box to include)"}
        ]
        assert drivers["plug-1"].state["on"] is False
        assert "on" not in drivers["plug-2"].state
        forced = client.post("/api/studios/s1/modes/off/apply", json={"include_manual": True})
        assert forced.status_code == 200
        assert forced.json()["subsystems"]["power"]["protected"] == []
        assert drivers["plug-2"].state["on"] is False
        bad = client.post("/api/studios/s1/modes/off/apply", json={"include_manual": "yes"})
        assert bad.status_code == 422


@pytest.mark.unit
def test_mode_apply_kicks_obs_boot(monkeypatch):
    """Mode applies wire the boot starter through to flagged modes."""
    from app.api import modes as modes_api
    from app.drivers.mock import MockDriver

    drivers = {plug: MockDriver(plug) for plug in ("plug-1", "plug-2", "plug-3")}
    monkeypatch.setattr(modes_api, "_plug_drivers", lambda registry, root: drivers)
    seen = {}
    monkeypatch.setattr(
        modes_api,
        "boot_try_start",
        lambda request, studio, scene=None, collection=None: seen.update(studio=studio, scene=scene)
        or ({"id": "job2", "state": "pinging", "scene": scene or "Face Full View"}, True),
    )
    registry = app.state.registries["example-studio"]
    mode = registry.get_mode("demo_stream")
    mode["autoStartObs"] = True
    try:
        body = client.post("/api/studios/example-studio/modes/demo_stream/apply", json={}).json()
        obs = body["subsystems"]["obs"]
        assert obs["status"] == "skipped"
        assert obs["boot_job"] == "job2"
        assert seen == {"studio": "example-studio", "scene": "Demo - Main"}
    finally:
        mode.pop("autoStartObs", None)


@pytest.mark.unit
def test_modes_apply_example_skips_without_hardware():
    """Example studio has no plug-map zones and no GVM panels: honest skips."""
    response = client.post("/api/studios/example-studio/modes/demo_stream/apply")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "demo_stream"
    subs = body["subsystems"]
    assert subs["power"]["status"] == "skipped"
    assert subs["lights"]["status"] == "skipped"
    assert any("demo-key" in item["reason"] for item in subs["lights"]["skipped"])
    assert subs["obs"]["status"] == "skipped"
    assert subs["obs"]["skipped"][0]["target"] == "Demo - Main"
    assert subs["screens"]["status"] == "skipped"
    assert body["ok"] is True


@pytest.mark.unit
def test_modes_apply_unknown_mode_404():
    assert client.post("/api/studios/example-studio/modes/nope/apply").status_code == 404


@pytest.mark.unit
def test_plug_drivers_wire_optional_credentials(tmp_path):
    """plug-map entries may carry Kasa creds; modes wiring passes them through."""
    import json

    from app.api import modes as modes_api

    local = tmp_path / "studios.local"
    local.mkdir()
    (local / "plug-map.json").write_text(
        json.dumps(
            {
                "plugs": [
                    {
                        "plug": "plug-a",
                        "host": "192.0.2.21",
                        "zone": "tzone",
                        "username": "user",
                        "password": "secret",
                    },
                    {"plug": "plug-b", "host": "192.0.2.22", "zone": "tzone"},
                    {
                        "plug": "plug-x",
                        "host": "192.0.2.23",
                        "zone": "elsewhere",
                        "username": "u",
                        "password": "p",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    loaded = {
        "studio": {"id": "x", "label": "X"},
        "devices": [],
        "zones": [{"id": "tzone", "label": "T"}],
        "modes": [],
        "presets": {},
    }
    drivers = modes_api._plug_drivers(Registry.from_loaded(loaded, {}), tmp_path)
    assert set(drivers) == {"plug-a", "plug-b"}
    assert (drivers["plug-a"].username, drivers["plug-a"].password) == ("user", "secret")
    assert (drivers["plug-b"].username, drivers["plug-b"].password) == (None, None)


@pytest.mark.unit
def test_obs_applier_scoped_per_studio_and_disconnects(tmp_path, monkeypatch):
    """obs.json maps studio ids to OBS hosts; applies switch then disconnects."""
    import json

    from app.api import modes as modes_api
    from app.drivers.base import DriverResult

    local = tmp_path / "studios.local"
    local.mkdir()
    (local / "obs.json").write_text(
        json.dumps({"studios": {"x": {"host": "192.0.2.9", "password": "pw"}}}),
        encoding="utf-8",
    )
    made = {}

    class FakeObs:
        def __init__(self, driver_id, host, port, password, **kwargs):
            made["ctor"] = (driver_id, host, port, password)
            made["window"] = (kwargs.get("connect_attempts"), kwargs.get("connect_delay_s"))
            self.closed = False

        def apply(self, command):
            made["apply"] = dict(command)
            return DriverResult(ok=True, state={"scene": command["scene"]})

        def safe_stop(self):
            self.closed = True
            made["closed"] = True
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
    result = applier("Main")
    assert result.ok is True and result.state == {"scene": "Main"}
    assert made["ctor"] == ("obs-modes", "192.0.2.9", 4455, "pw")
    assert made["apply"] == {"action": "switch", "scene": "Main"}
    assert made["closed"] is True
    assert made["window"] == (8, 2.0)  # modes wait out an OBS launch

    other = dict(loaded, studio={"id": "y", "label": "Y"})
    assert modes_api._obs_applier(Registry.from_loaded(other, {}), tmp_path) is None
    assert modes_api._obs_applier(Registry.from_loaded(loaded, {}), tmp_path / "empty") is None


@pytest.mark.unit
def test_screens_applier_wires_screen_devices():
    """Studios with screen-driver devices get a modes screens applier."""
    from app.api import modes as modes_api
    from app.drivers.screen import SCREEN_STATE

    SCREEN_STATE.clear()
    try:
        loaded = {
            "studio": {"id": "x", "label": "X"},
            "devices": [
                {"id": "kqc", "zone": "tzone", "type": "screen", "driver": "screen"},
                {"id": "cam", "zone": "tzone", "type": "camera", "driver": None},
            ],
            "zones": [{"id": "tzone", "label": "T"}],
            "modes": [],
            "presets": {},
        }
        applier = modes_api._screens_applier(Registry.from_loaded(loaded, {}))
        assert callable(applier)
        results = applier({"kqc": {"theme": "live", "text": "Hi"}})
        assert results["kqc"].ok is True
        assert results["kqc"].state["text"] == "Hi"
        assert applier({"ghost": {"theme": "x"}})["ghost"].ok is False

        bare = dict(loaded, devices=[loaded["devices"][1]])
        assert modes_api._screens_applier(Registry.from_loaded(bare, {})) is None
    finally:
        SCREEN_STATE.clear()


@pytest.mark.unit
def test_modes_unknown_studio_404():
    assert client.get("/api/studios/nope/modes").status_code == 404
    assert client.post("/api/studios/nope/modes/demo_work/apply").status_code == 404


@pytest.mark.unit
def test_presets_list_and_apply():
    response = client.get("/api/studios/example-studio/presets")
    assert response.status_code == 200
    assert response.json() == {"presets": [{"id": "default", "label": "Default"}]}
    apply = client.post("/api/studios/example-studio/presets/default/apply")
    assert apply.status_code == 200
    assert apply.json()["subsystems"]["lights"]["status"] == "skipped"
    assert client.post("/api/studios/example-studio/presets/nope/apply").status_code == 404


@contextmanager
def _bad_mode_registry():
    """One studio whose only mode violates a light range (synthetic, offline)."""
    registries = app.state.registries
    saved = dict(registries)
    loaded = {
        "studio": {"id": "x", "label": "X"},
        "devices": [
            {
                "id": "tkey",
                "zone": "tzone",
                "type": "light",
                "driver": "mock",
                "controllable": True,
                "profile": "tp",
            }
        ],
        "zones": [{"id": "tzone", "label": "T"}],
        "modes": [{"id": "bad", "label": "Bad", "lights": {"tkey": {"intensity": 999}}}],
        "presets": {},
    }
    profiles = {"tp": {"spec": {"intensityRange": [0, 100]}, "capabilities": {"intensity": True}}}
    registries.clear()
    registries["x"] = Registry.from_loaded(loaded, profiles)
    try:
        yield
    finally:
        registries.clear()
        registries.update(saved)


@pytest.mark.unit
def test_modes_apply_validation_422():
    with _bad_mode_registry():
        response = client.post("/api/studios/x/modes/bad/apply")
        assert response.status_code == 422
        assert "intensity" in response.json()["detail"]


@pytest.mark.unit
def test_modes_apply_fade_override_accepted_and_validated():
    ok = client.post("/api/studios/example-studio/modes/demo_work/apply", json={"fade_s": 0.5})
    assert ok.status_code == 200
    bad = client.post("/api/studios/example-studio/modes/demo_work/apply", json={"fade_s": "soon"})
    assert bad.status_code == 422
    assert "fade_s" in bad.json()["detail"]
    preset = client.post(
        "/api/studios/example-studio/presets/default/apply", json={"fade_s": "soon"}
    )
    assert preset.status_code == 422


@pytest.mark.unit
def test_modes_apply_power_delay_accepted_and_validated():
    ok = client.post(
        "/api/studios/example-studio/modes/demo_work/apply", json={"power_delay_s": 1.5}
    )
    assert ok.status_code == 200
    bad = client.post(
        "/api/studios/example-studio/modes/demo_work/apply", json={"power_delay_s": 9}
    )
    assert bad.status_code == 422
    assert "power_delay_s" in bad.json()["detail"]


@pytest.mark.unit
def test_modes_apply_home_hands_on_drives_ninja_ble(monkeypatch):
    """hands_on: Ninja 20 over BLE, Ninja 400 over DMX (mock, no radio)."""
    from app.api import modes as modes_api
    from app.drivers.ninja_ble import NinjaBleDriver

    FakeGvm.instances = {}
    mock_drivers = {}

    def fake_shared(channel, group, default_cct=None, mock=False, repeats=2):
        key = (channel, str(group).upper())
        if key not in mock_drivers:
            driver = NinjaBleDriver(
                f"ninja-ble-{channel}-{group}", channel, group, backend="mock"
            )
            if default_cct is not None:
                driver.seed_default_cct(default_cct)
            mock_drivers[key] = driver
        return mock_drivers[key]

    monkeypatch.setattr(modes_api, "_plug_drivers", lambda registry, root: {})
    monkeypatch.setattr(modes_api, "_obs_applier", lambda registry, root: None)
    monkeypatch.setattr(modes_api, "_screens_applier", lambda registry: None)
    stub = StubUniverse()
    monkeypatch.setattr(modes_api, "get_shared_driver", lambda port=None: stub)
    monkeypatch.setattr(modes_api, "GvmBleDriver", FakeGvm)
    monkeypatch.setattr(modes_api, "shared_driver", fake_shared)
    response = client.post("/api/studios/home-studio/modes/hands_on/apply")
    assert response.status_code == 200
    lights = response.json()["subsystems"]["lights"]
    assert lights["status"] == "ready"
    assert (3, "B") not in mock_drivers  # Ninja 400 is DMX-only: no BLE driver
    assert stub.channels == {1: 6, 2: 132}  # 400 over DMX: 2% + 5600K
    assert mock_drivers[(3, "C")].look["brightness"] == 2
    assert FakeGvm.instances["gvm-ho"].calls == [{"brightness": 5}, {"cct": 5000}]
    faded = client.post("/api/studios/home-studio/modes/hands_on/apply", json={"fade_s": 0.5})
    assert faded.status_code == 200
    faded_lights = faded.json()["subsystems"]["lights"]
    assert faded_lights["status"] == "ready"
    assert any("ninja-20" in item["reason"] for item in faded_lights["notes"])


@pytest.mark.unit
def test_build_modes_module_wires_home_ninja_without_http(monkeypatch):
    """Same hands_on path through the module directly (runs socket-free)."""
    from app.api import modes as modes_api
    from app.config.loader import load_all
    from app.drivers.ninja_ble import NinjaBleDriver
    from app.registry import Registry as RegistryCls

    data = load_all(CONFIG_ROOT)
    registry = RegistryCls.from_loaded(data["studios"]["home-studio"], data["profiles"])
    stub = StubUniverse()
    FakeGvm.instances = {}
    mock_drivers = {}

    def fake_shared(channel, group, default_cct=None, mock=False, repeats=2):
        key = (channel, str(group).upper())
        if key not in mock_drivers:
            mock_drivers[key] = NinjaBleDriver(
                f"ninja-ble-{channel}-{group}", channel, group, backend="mock"
            )
        return mock_drivers[key]

    monkeypatch.setattr(modes_api, "_plug_drivers", lambda reg, root: {})
    monkeypatch.setattr(modes_api, "_obs_applier", lambda reg, root: None)
    monkeypatch.setattr(modes_api, "_screens_applier", lambda reg: None)
    monkeypatch.setattr(modes_api, "get_shared_driver", lambda port=None: stub)
    monkeypatch.setattr(modes_api, "GvmBleDriver", FakeGvm)
    monkeypatch.setattr(modes_api, "shared_driver", fake_shared)
    module = modes_api.build_modes_module(registry, CONFIG_ROOT)
    result = module.apply_mode("hands_on", sleep=lambda s: None)
    assert result["subsystems"]["lights"]["status"] == "ready"
    assert stub.channels == {1: 6, 2: 132}  # Ninja 400 over DMX (DMX-only)
    assert (3, "B") not in mock_drivers
    assert mock_drivers[(3, "C")].look["brightness"] == 2
    assert FakeGvm.instances["gvm-ho"].calls == [{"brightness": 5}, {"cct": 5000}]
    faded = module.apply_mode("hands_on", sleep=lambda s: None, fade_s=0.5)
    assert faded["subsystems"]["lights"]["status"] == "ready"
    assert any("ninja-20" in item["reason"] for item in faded["subsystems"]["lights"]["notes"])


@pytest.mark.unit
def test_obs_bench_passthrough(monkeypatch):
    """Bench OBS routes drive one per-request driver (fake here, no sockets)."""

    seen = {}

    class FakeObsDriver:
        def __init__(self, driver_id, host="127.0.0.1", port=4455, password=None, client=None):
            seen["host"] = host

        def apply(self, command):
            return DriverResult(ok=True, state={"echo": command}, error=None)

    monkeypatch.setattr("app.api.obs.ObsWsDriver", FakeObsDriver)
    state = client.post("/api/testbench/obs/state", json={"host": "obs-pc"})
    assert state.json()["ok"] is True
    assert seen["host"] == "obs-pc"
    switch = client.post("/api/testbench/obs/switch", json={"host": "obs-pc", "scene": "Main"})
    assert switch.json()["state"] == {"echo": {"action": "switch", "scene": "Main"}}


@pytest.mark.unit
def test_obs_stream_refused_without_confirm():
    response = client.post("/api/testbench/obs/stream", json={"action": "start"})
    assert response.status_code == 422
    confirmed = client.post("/api/testbench/obs/stream", json={"action": "start", "confirmed": True})
    assert confirmed.status_code == 200
    assert confirmed.json()["ok"] is False


@pytest.mark.unit
def test_ai_look_mock_pipeline():
    response = client.post(
        "/api/studios/example-studio/ai/look", json={"prompt": "cozy interview", "targets": "lights"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "mock"
    assert body["directives"]["lights"] == {"demo-key": {"intensity": 25, "cct": 4600}}
    assert body["critique"] == ["clean: all directives within declared capabilities"]
    assert body["apply"]["subsystems"]["lights"]["status"] == "skipped"
    assert body["preset_candidate"]["label"] == "cozy interview"


@pytest.mark.unit
def test_ai_look_bad_targets_422():
    response = client.post("/api/studios/example-studio/ai/look", json={"prompt": "x", "targets": "smell"})
    assert response.status_code == 422


@pytest.mark.unit
def test_ai_look_unknown_provider_422():
    response = client.post(
        "/api/studios/example-studio/ai/look", json={"prompt": "x", "provider": "baseten"}
    )
    assert response.status_code == 422


@pytest.mark.unit
def test_ai_look_meta_unconfigured_503(monkeypatch):
    from app.api import ai as ai_api

    monkeypatch.setattr(ai_api, "_meta_provider", lambda root: None)
    response = client.post("/api/studios/example-studio/ai/look", json={"prompt": "x", "provider": "meta"})
    assert response.status_code == 503


@pytest.mark.unit
def test_ai_look_meta_uses_configured_provider(monkeypatch):
    from app.ai.mock import MockProvider
    from app.api import ai as ai_api

    class MetaStub(MockProvider):
        def name(self):
            return "meta"

    monkeypatch.setattr(ai_api, "_meta_provider", lambda root: MetaStub())
    response = client.post(
        "/api/studios/example-studio/ai/look",
        json={"prompt": "cozy", "targets": "lights", "provider": "meta"},
    )
    assert response.status_code == 200
    assert response.json()["provider"] == "meta"


@pytest.mark.unit
def test_dmx_gentle_range_refusals():
    over = client.post("/api/testbench/dmx/set", json={"channel": 1, "value": 27})
    assert over.status_code == 422
    wide = client.post("/api/testbench/dmx/sweep", json={"start": 1, "end": 64, "value": 13})
    assert wide.status_code == 422
    hot = client.post("/api/testbench/dmx/sweep", json={"start": 1, "end": 4, "value": 200})
    assert hot.status_code == 422
    state = client.post("/api/testbench/dmx/state", json={})
    assert state.status_code == 200
    assert state.json()["state"]["channels"] == {}
    hot_fade = client.post("/api/testbench/dmx/fade", json={"channels": {"1": 27}, "seconds": 1})
    assert hot_fade.status_code == 422
    hot_pct = client.post("/api/testbench/dmx/fade", json={"channels": {"1": 11}, "seconds": 1, "units": "percent"})
    assert hot_pct.status_code == 422
    bad_units = client.post("/api/testbench/dmx/fade", json={"channels": {"1": 5}, "units": "kelvin"})
    assert bad_units.status_code == 422
    hot_light = client.post("/api/testbench/dmx/light", json={"fixture": "ninja-400", "power": 50})
    assert hot_light.status_code == 422
    frac_light = client.post("/api/testbench/dmx/light", json={"fixture": "ninja-400", "power": 5.5})
    assert frac_light.status_code == 422
    frac_cct = client.post("/api/testbench/dmx/light", json={"fixture": "ninja-400", "cct": 5600.5})
    assert frac_cct.status_code == 422
    frac_pct = client.post(
        "/api/testbench/dmx/fade",
        json={"channels": {"1": 5.5}, "seconds": 1, "units": "percent"},
    )
    assert frac_pct.status_code == 422


@pytest.mark.unit
def test_dmx_allow_bright_unlocks_full_range(monkeypatch):
    """The human operator gets the whole fixture; the flag is the acknowledgment."""

    seen = {}

    class FakeDmx:
        def __init__(self, driver_id, port=None, **kwargs):
            pass

        def apply(self, command):
            seen["apply"] = command
            return DriverResult(ok=True, state={"channels": {}}, error=None)

        def fade(self, channels, seconds, units="dmx"):
            seen["fade"] = (channels, seconds, units)
            return 3

        def get_state(self):
            return DriverResult(ok=True, state={"channels": {}}, error=None)

    monkeypatch.setattr("app.api.dmx.DmxUsbDriver", FakeDmx)
    bright = client.post(
        "/api/testbench/dmx/set", json={"channel": 1, "value": 200, "allow_bright": True}
    )
    assert bright.status_code == 200
    assert seen["apply"] == {"channel": 1, "value": 200}
    fade = client.post(
        "/api/testbench/dmx/fade",
        json={"channels": {"1": 200}, "seconds": 1, "allow_bright": True},
    )
    assert fade.status_code == 200
    assert seen["fade"] == ({1: 200}, 1.0, "dmx")
    light = client.post(
        "/api/testbench/dmx/light",
        json={"fixture": "ninja-400", "power": 50, "allow_bright": True},
    )
    assert light.status_code == 200
    assert light.json()["applied"]["channels"] == {"1": 128}


@pytest.mark.unit
def test_dmx_light_dwell_steps_power_then_color(monkeypatch):
    """Dwell fades: power walks whole percents first, then color walks DMX."""

    calls = []

    class FakeDmx:
        def __init__(self, driver_id, port=None, **kwargs):
            pass

        def apply(self, command):
            calls.append(("apply", command))
            return DriverResult(ok=True, state={"channels": {}}, error=None)

        def fade(self, channels, seconds, units="dmx", dwell_s=None):
            calls.append(("fade", channels, seconds, units, dwell_s))
            return 4

        def get_state(self):
            return DriverResult(ok=True, state={"channels": {}}, error=None)

    monkeypatch.setattr("app.api.dmx.DmxUsbDriver", FakeDmx)
    look = client.post(
        "/api/testbench/dmx/light",
        json={"fixture": "ninja-400", "power": 5, "cct": 5600, "dwell_s": 0.5},
    )
    assert look.status_code == 200
    assert calls == [
        ("fade", {1: 5}, 0, "percent", 0.5),
        ("fade", {2: 132}, 0, "dmx", 0.5),
    ]
    body = look.json()
    assert body["applied"]["sequence"] == ["power", "cct"]
    assert body["applied"]["channels"] == {"1": 13, "2": 132}
    assert body["state"]["frames"] == 8
    solo = client.post("/api/testbench/dmx/light", json={"fixture": "ninja-400", "power": 3})
    assert solo.status_code == 200
    assert calls[-1] == ("apply", {"channels": {1: 8}})
    bad = client.post(
        "/api/testbench/dmx/light", json={"fixture": "ninja-400", "power": 5, "dwell_s": 999}
    )
    assert bad.status_code == 422


@pytest.mark.unit
def test_dmx_fade_cct_exempt(monkeypatch):
    """Declared CCT channels are color, not brightness; bright dimmers need the flag."""

    seen = {}

    class FakeDmx:
        def __init__(self, driver_id, port=None, **kwargs):
            pass

        def fade(self, channels, seconds, units="dmx"):
            seen["fade"] = (channels, seconds, units)
            return 3

        def get_state(self):
            return DriverResult(ok=True, state={"channels": {}}, error=None)

    monkeypatch.setattr("app.api.dmx.DmxUsbDriver", FakeDmx)
    ok = client.post(
        "/api/testbench/dmx/fade", json={"channels": {"1": 26, "2": 200}, "cct": [2], "seconds": 2}
    )
    assert ok.status_code == 200
    assert seen["fade"] == ({1: 26, 2: 200}, 2.0, "dmx")
    bad = client.post("/api/testbench/dmx/fade", json={"channels": {"1": 200}, "seconds": 1})
    assert bad.status_code == 422
    bright = client.post(
        "/api/testbench/dmx/fade", json={"channels": {"1": 200}, "seconds": 1, "allow_bright": True}
    )
    assert bright.status_code == 200
    assert seen["fade"] == ({1: 200}, 1.0, "dmx")


@pytest.mark.unit
def test_screen_push_and_kiosk_state():
    push = client.post(
        "/api/testbench/screen/push", json={"screen": "bench-test", "theme": "live", "text": "hi"}
    )
    assert push.status_code == 200
    assert push.json()["state"]["theme"] == "live"
    kiosk = client.get("/api/screens/bench-test/state")
    assert kiosk.status_code == 200
    assert kiosk.json()["text"] == "hi"
    audio = client.post("/api/testbench/screen/audio", json={"screen": "bench-test", "action": "play"})
    assert audio.json()["state"]["audio"] == "play"


@pytest.mark.unit
def test_kasa_bench_passthrough(monkeypatch):
    """Bench Kasa routes pass hosts + creds to the driver (fake here, no LAN)."""
    seen = {}

    def fake_discover(username=None, password=None, discovery_timeout=8):
        seen["discover"] = (username, password, discovery_timeout)
        return [{"host": "192.0.2.61", "alias": "bench", "model": "HS103", "on": True}]

    class FakeKasa:
        def __init__(self, driver_id, host, username=None, password=None, timeout=10):
            seen["driver"] = (host, username, password)

        def get_state(self):
            return DriverResult(ok=True, state={"power": "on"}, error=None)

        def apply(self, command):
            seen["apply"] = command
            return DriverResult(ok=True, state={"power": "on" if command["on"] else "off"}, error=None)

    monkeypatch.setattr("app.api.kasa.discover_plugs", fake_discover)
    monkeypatch.setattr("app.api.kasa.KasaDriver", FakeKasa)
    found = client.post("/api/testbench/kasa/discover", json={"username": "u", "password": "p"})
    assert found.status_code == 200
    assert found.json()["plugs"][0]["host"] == "192.0.2.61"
    assert seen["discover"] == ("u", "p", 8)
    state = client.post("/api/testbench/kasa/state", json={"host": "192.0.2.61"})
    assert state.json() == {"ok": True, "state": {"power": "on"}, "error": None}
    assert seen["driver"] == ("192.0.2.61", None, None)
    switched = client.post("/api/testbench/kasa/set", json={"host": "192.0.2.61", "on": False})
    assert switched.json()["state"] == {"power": "off"}
    assert seen["apply"] == {"on": False}


@pytest.mark.unit
def test_kasa_bench_unavailable_503(monkeypatch):
    """Without python-kasa the bench says so instead of 500ing."""
    monkeypatch.setattr("app.api.kasa.KASA_AVAILABLE", False)
    response = client.post("/api/testbench/kasa/discover", json={})
    assert response.status_code == 503


@pytest.mark.unit
def test_ir_bench_endpoints(monkeypatch):
    """Bench IR routes pass through to the module (fake here, no serial)."""
    from app.modules.ir import IrModuleError

    class FakeIrDriver:
        def __init__(self, driver_id, port=None):
            pass

        def listen(self, seconds):
            return {"codes": [[1, 2, 3]], "seconds": seconds}

    class FakeIrModule:
        def __init__(self, name, drivers, store):
            pass

        def capture(self, label, seconds):
            if label == "bad":
                raise IrModuleError("heard nothing")
            return {"label": label, "code": [1, 2, 3]}

        def codes(self):
            return {"power": [1, 2, 3]}

        def send_label(self, label, times=1, gap_s=0.3):
            if label == "ghost":
                raise IrModuleError("unknown label: ghost")
            return DriverResult(ok=True, state={"sent": label, "times": times}, error=None)

        def send_code(self, code, times=1, gap_s=0.3):
            return DriverResult(ok=True, state={"sent": code}, error=None)

        def mark(self, label, works=None, notes=None):
            if label == "ghost":
                raise IrModuleError("unknown label: ghost")
            return {"works": works, "notes": notes}

    monkeypatch.setattr("app.api.ir.IrUsbSerialDriver", FakeIrDriver)
    monkeypatch.setattr("app.api.ir.IrModule", FakeIrModule)
    heard = client.post("/api/testbench/ir/listen", json={"seconds": 2})
    assert heard.json() == {"codes": [[1, 2, 3]], "seconds": 2}
    saved = client.post("/api/testbench/ir/capture", json={"label": "power"})
    assert saved.json()["code"] == [1, 2, 3]
    assert client.post("/api/testbench/ir/capture", json={"label": "bad"}).status_code == 422
    assert client.get("/api/testbench/ir/codes").json() == {"power": [1, 2, 3]}
    sent = client.post("/api/testbench/ir/send", json={"label": "power", "times": 2})
    assert sent.json()["state"] == {"sent": "power", "times": 2}
    raw = client.post("/api/testbench/ir/send", json={"code": [1, 2, 3]})
    assert raw.json()["ok"] is True
    assert client.post("/api/testbench/ir/send", json={}).status_code == 422
    assert client.post("/api/testbench/ir/send", json={"label": "ghost"}).status_code == 404
    marked = client.post("/api/testbench/ir/mark", json={"label": "power", "works": True})
    assert marked.json() == {"label": "power", "works": True, "notes": None}
    assert client.post("/api/testbench/ir/mark", json={"label": "ghost"}).status_code == 404


@pytest.mark.unit
def test_gvm_scan_passthrough(monkeypatch):
    """Bench BLE scan echoes discovered devices; radio failures report 502."""
    monkeypatch.setattr(
        "app.api.gvm.scan_devices",
        lambda timeout: [{"address": "AA:BB:CC:DD:EE:01", "name": "BT_LED 01", "rssi": -60}],
    )
    found = client.post("/api/testbench/gvm/scan", json={"timeout": 3})
    assert found.status_code == 200
    assert found.json()["devices"][0]["name"] == "BT_LED 01"

    def boom(timeout):
        raise RuntimeError("radio off")

    monkeypatch.setattr("app.api.gvm.scan_devices", boom)
    assert client.post("/api/testbench/gvm/scan", json={}).status_code == 502


@pytest.mark.unit
def test_dmx_blackout_and_fixtures(monkeypatch):
    """Blackout toggles through the shared driver; fixtures list home DMX lights."""
    seen = {}

    class FakeDmx:
        def __init__(self, driver_id, port=None, **kwargs):
            pass

        def apply(self, command):
            seen["apply"] = command
            return DriverResult(ok=True, state={"blackout": command["blackout"]}, error=None)

        def get_state(self):
            return DriverResult(ok=True, state={"channels": {}}, error=None)

    monkeypatch.setattr("app.api.dmx.DmxUsbDriver", FakeDmx)
    off = client.post("/api/testbench/dmx/blackout", json={"on": True})
    assert off.json()["state"] == {"blackout": True}
    assert seen["apply"] == {"blackout": True}
    on = client.post("/api/testbench/dmx/blackout", json={"on": False})
    assert on.json()["state"] == {"blackout": False}

    home = client.get("/api/testbench/dmx/fixtures?studio=home-studio")
    assert home.status_code == 200
    fixtures = {item["id"]: item for item in home.json()["fixtures"]}
    assert fixtures["ninja-400"]["dimmer"] == 1
    assert fixtures["ninja-400"]["cct"] == 2
    assert fixtures["ninja-400"]["cct_range"] == [2500, 8500]
    assert "ninja-20" not in fixtures  # BLE-only now: its DMX wire is gone
    assert set(fixtures) == {"ninja-400"}
    assert home.json()["skipped"] == []
    empty = client.get("/api/testbench/dmx/fixtures?studio=example-studio")
    assert empty.json() == {"fixtures": [], "skipped": []}
    assert client.get("/api/testbench/dmx/fixtures?studio=nope").status_code == 404
    fixed = client.post("/api/testbench/dmx/light", json={"fixture": "ninja-20", "cct": 5600})
    assert fixed.status_code == 422
    ghost = client.post("/api/testbench/dmx/light", json={"fixture": "nope", "power": 5})
    assert ghost.status_code == 422


@pytest.mark.unit
def test_obs_scenes_passthrough(monkeypatch):
    """Bench scene list drives the same per-request driver as state/switch."""

    class FakeObsDriver:
        def __init__(self, driver_id, host="127.0.0.1", port=4455, password=None, client=None):
            pass

        def apply(self, command):
            return DriverResult(ok=True, state={"scenes": ["Main", "Bench"]}, error=None)

    monkeypatch.setattr("app.api.obs.ObsWsDriver", FakeObsDriver)
    scenes = client.post("/api/testbench/obs/scenes", json={"host": "obs-pc"})
    assert scenes.status_code == 200
    assert scenes.json()["state"] == {"scenes": ["Main", "Bench"]}


@pytest.mark.unit
def test_bench_safe_stop():
    stopped = client.post("/api/testbench/mock-kasa/safe-stop")
    assert stopped.status_code == 200
    assert stopped.json()["ok"] is True
    assert client.post("/api/testbench/nope/safe-stop").status_code == 404


@pytest.mark.unit
def test_screen_bench_state():
    client.post("/api/testbench/screen/push", json={"screen": "bench-state", "theme": "rehearse"})
    state = client.post("/api/testbench/screen/state", json={"screen": "bench-state"})
    assert state.status_code == 200
    assert state.json()["state"]["theme"] == "rehearse"


@pytest.mark.unit
def test_screen_audio_volume_defaults_gentle():
    audio = client.post("/api/testbench/screen/audio", json={"screen": "bench-vol", "action": "play"})
    assert audio.status_code == 200
    assert audio.json()["state"]["volume"] == 10


@pytest.mark.unit
def test_screen_audio_loud_passes_uncapped():
    """No loudness cap: the bed synth runs quiet (user-verified at 100%)."""
    loud = client.post(
        "/api/testbench/screen/audio", json={"screen": "bench-vol", "action": "play", "volume": 100}
    )
    assert loud.status_code == 200
    assert loud.json()["state"]["volume"] == 100


@pytest.mark.unit
def test_screen_audio_rejects_bad_volume():
    bad = client.post(
        "/api/testbench/screen/audio", json={"screen": "bench-vol", "action": "play", "volume": 101}
    )
    assert bad.status_code == 422


@pytest.mark.unit
def test_bench_and_screen_pages_serve():
    bench = client.get("/bench")
    assert bench.status_code == 200
    assert "Kasa plugs" in bench.text
    screen = client.get("/screen/kqc-screen")
    assert screen.status_code == 200


@pytest.mark.unit
def test_kiosk_state_carries_hub_boot_id():
    first = client.get("/api/screens/boot-check/state").json()
    assert first["hub"]
    second = client.get("/api/screens/boot-check/state").json()
    assert second["hub"] == first["hub"]


@pytest.mark.unit
def test_kiosk_page_reloads_on_hub_reboot():
    page = client.get("/screen/boot-check")
    assert page.status_code == 200
    assert "seenBoot" in page.text
    assert "location.reload()" in page.text


@pytest.mark.unit
def test_create_app_degraded_on_broken_config(monkeypatch, tmp_path):
    """Broken config yields degraded health, never a crash."""
    from app.main import create_app

    broken = tmp_path / "studios" / "broken"
    broken.mkdir(parents=True)
    (broken / "studio.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("STUDIO_CONFIG", str(tmp_path))
    degraded = create_app()
    assert degraded.state.health["status"] == "degraded"
    assert degraded.state.registries == {}
    assert degraded.state.health["error"]


@pytest.mark.unit
def test_actions_log_records_mode_and_preset():
    """Mode and preset applies land in the per-studio action log, oldest first."""
    app.state.action_log.clear()
    assert client.post("/api/studios/example-studio/modes/demo_work/apply").status_code == 200
    assert client.post("/api/studios/example-studio/presets/default/apply").status_code == 200
    response = client.get("/api/studios/example-studio/actions")
    assert response.status_code == 200
    body = response.json()
    assert body["can_undo"] is True
    assert [(entry["kind"], entry["id"]) for entry in body["actions"]] == [
        ("mode", "demo_work"),
        ("preset", "default"),
    ]
    assert all("ok" in entry and "statuses" in entry for entry in body["actions"])


@pytest.mark.unit
def test_actions_log_records_ai_look():
    """AI looks record their directives so undo can replay them."""
    app.state.action_log.clear()
    look = client.post(
        "/api/studios/example-studio/ai/look", json={"prompt": "dim", "targets": "screens"}
    )
    assert look.status_code == 200
    client.post("/api/studios/example-studio/modes/demo_work/apply")
    entries = client.get("/api/studios/example-studio/actions").json()["actions"]
    assert entries[0]["kind"] == "custom"
    assert entries[0]["content"] is not None


@pytest.mark.unit
def test_actions_undo_restores_previous():
    """Undo re-applies the previous entry; a lone entry leaves nothing to undo."""
    app.state.action_log.clear()
    client.post("/api/studios/example-studio/modes/demo_work/apply")
    client.post("/api/studios/example-studio/modes/demo_stream/apply")
    undo = client.post("/api/studios/example-studio/actions/undo")
    assert undo.status_code == 200
    body = undo.json()
    assert body["restored"] == {"kind": "mode", "id": "demo_work"}
    assert "subsystems" in body
    follow_up = client.get("/api/studios/example-studio/actions").json()
    assert follow_up["can_undo"] is False
    assert client.post("/api/studios/example-studio/actions/undo").status_code == 409


@pytest.mark.unit
def test_actions_undo_needs_two_entries():
    """Empty or single-entry history refuses undo honestly."""
    app.state.action_log.clear()
    assert client.post("/api/studios/example-studio/actions/undo").status_code == 409
    client.post("/api/studios/example-studio/modes/demo_work/apply")
    assert client.post("/api/studios/example-studio/actions/undo").status_code == 409


@pytest.mark.unit
def test_actions_undo_replays_modality():
    """Undo restores the pre-run state by re-applying the modality's final beat."""
    from types import SimpleNamespace

    from app.api.modes import record_action

    app.state.action_log.clear()
    stub = SimpleNamespace(app=app)
    blank = {"ok": True, "subsystems": {}}
    record_action(stub, "example-studio", "modality", "demo_work", None, blank)
    record_action(stub, "example-studio", "modality", "demo_stream", None, blank)
    undo = client.post("/api/studios/example-studio/actions/undo")
    assert undo.status_code == 200
    assert undo.json()["restored"] == {"kind": "modality", "id": "demo_work"}


@pytest.mark.unit
def test_actions_unknown_studio_404():
    assert client.get("/api/studios/nope/actions").status_code == 404
    assert client.post("/api/studios/nope/actions/undo").status_code == 404


@pytest.mark.unit
def test_ai_look_scope_lights_limits_directives():
    """scope_mode lights: only listed fixtures are directed or applied."""
    response = client.post(
        "/api/studios/example-studio/ai/look",
        json={
            "prompt": "dim",
            "targets": "lights",
            "scope_mode": "lights",
            "scope_lights": ["demo-rgb"],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["directives"]["lights"] == {"demo-rgb": {"intensity": 12, "cct": 4200}}
    assert body["scope"]["skipped"] == []


@pytest.mark.unit
def test_ai_look_scope_zones_limits_directives():
    """scope_mode zones: only devices in those zones are directed; rest named."""
    response = client.post(
        "/api/studios/example-studio/ai/look",
        json={"prompt": "dim", "targets": "both", "scope_mode": "zones", "scope_zones": ["bench"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert sorted(body["directives"]["lights"]) == ["demo-key"]
    assert body["directives"]["screens"] == {}
    assert body["scope"]["skipped"] == []
    assert body["scope"]["mode"] == "zones"
    assert body["scope"]["allowed"] == ["demo-key", "demo-rgb"]


@pytest.mark.unit
def test_ai_look_scope_rejects_unknown():
    """Unknown scope mode, zone, or light is a 422, never a silent empty look."""
    base = "/api/studios/example-studio/ai/look"
    assert client.post(base, json={"prompt": "x", "scope_mode": "rooms"}).status_code == 422
    assert (
        client.post(base, json={"prompt": "x", "scope_mode": "zones", "scope_zones": ["nope"]}).status_code
        == 422
    )
    assert (
        client.post(base, json={"prompt": "x", "scope_mode": "lights", "scope_lights": ["nope"]}).status_code
        == 422
    )


@pytest.mark.unit
def test_zones_list():
    response = client.get("/api/studios/example-studio/zones")
    assert response.status_code == 200
    zones = {zone["id"]: zone for zone in response.json()["zones"]}
    assert sorted(zones) == ["bench", "desk"]
    assert zones["bench"]["groups"] == ["bench-power"]
    assert sorted(zones["bench"]["plugs"]) == ["plug-1", "plug-2"]
    assert zones["bench"]["autoStartObs"] is False
    assert client.get("/api/studios/nope/zones").status_code == 404


@pytest.mark.unit
def test_zone_power_switches(monkeypatch):
    from app.api import power as power_api
    from app.drivers.mock import MockDriver

    drivers = {plug: MockDriver(plug) for plug in ("plug-1", "plug-2", "plug-3")}
    monkeypatch.setattr(power_api, "_plug_drivers", lambda registry, root: drivers)
    off = client.post("/api/studios/example-studio/zones/bench/power", json={"on": False})
    assert off.status_code == 200
    assert off.json()["ok"] is True
    assert off.json()["obs_boot"] is None
    assert drivers["plug-1"].state["on"] is False
    assert client.post("/api/studios/example-studio/zones/nope/power", json={"on": True}).status_code == 404


@contextmanager
def _tmp_zone_studio(tmp_path):
    """Real app pointed at a tmp config root with one local studio (id "x")."""
    import json as json_lib
    import shutil

    from app.config.loader import load_all
    from app.registry import Registry

    config_root = Path(__file__).resolve().parents[2] / "config"
    shutil.copytree(config_root / "profiles", tmp_path / "profiles")
    studio_dir = tmp_path / "studios.local" / "x"
    shutil.copytree(config_root / "studios" / "example-studio", studio_dir)
    meta_path = studio_dir / "studio.json"
    meta = json_lib.loads(meta_path.read_text(encoding="utf-8"))
    meta["id"] = "x"
    meta_path.write_text(json_lib.dumps(meta), encoding="utf-8")

    data = load_all(tmp_path)
    saved = (app.state.config_root, dict(app.state.registries))
    app.state.config_root = tmp_path
    app.state.registries = {"x": Registry.from_loaded(data["studios"]["x"], data["profiles"])}
    try:
        yield studio_dir
    finally:
        app.state.config_root, registries = saved
        app.state.registries.clear()
        app.state.registries.update(registries)


@pytest.mark.unit
def test_zone_config_save_roundtrip(tmp_path):
    import json as json_lib

    with _tmp_zone_studio(tmp_path) as studio_dir:
        assert client.get("/api/studios/x/zones").json()["zones"][0]["autoStartObs"] is False
        saved = client.put("/api/studios/x/zones/bench/config", json={"autoStartObs": True})
        assert saved.status_code == 200
        assert saved.json() == {"zone": "bench", "autoStartObs": True}
        zones = {zone["id"]: zone for zone in client.get("/api/studios/x/zones").json()["zones"]}
        assert zones["bench"]["autoStartObs"] is True
        stored = json_lib.loads((studio_dir / "zones.json").read_text(encoding="utf-8"))
        assert [item for item in stored if item["id"] == "bench"][0]["autoStartObs"] is True
        assert client.put("/api/studios/x/zones/nope/config", json={"autoStartObs": True}).status_code == 404
        assert client.put("/api/studios/nope/zones/bench/config", json={"autoStartObs": True}).status_code == 404


@pytest.mark.unit
def test_zone_config_refuses_tracked_studios():
    assert client.put("/api/studios/example-studio/zones/bench/config", json={"autoStartObs": True}).status_code == 422


@pytest.mark.unit
def test_zone_power_kicks_obs_boot(monkeypatch):
    from app.api import power as power_api
    from app.drivers.mock import MockDriver

    drivers = {plug: MockDriver(plug) for plug in ("plug-1", "plug-2", "plug-3")}
    monkeypatch.setattr(power_api, "_plug_drivers", lambda registry, root: drivers)
    seen = {}
    monkeypatch.setattr(
        power_api,
        "boot_try_start",
        lambda request, studio, scene=None: seen.update(studio=studio, scene=scene)
        or ({"id": "job1", "state": "pinging", "scene": "Face Full View"}, True),
    )
    registry = app.state.registries["example-studio"]
    zone = registry.get_zone("bench")
    zone["autoStartObs"] = True
    try:
        on = client.post("/api/studios/example-studio/zones/bench/power", json={"on": True})
        assert on.status_code == 200
        assert on.json()["obs_boot"] == {"job": "job1", "started": True, "state": "pinging", "scene": "Face Full View"}
        assert seen == {"studio": "example-studio", "scene": None}
        off = client.post("/api/studios/example-studio/zones/bench/power", json={"on": False})
        assert off.json()["obs_boot"] is None
    finally:
        zone.pop("autoStartObs", None)


@pytest.mark.unit
def test_zone_power_off_refuses_protected():
    """Zone-off on a manual-only zone is an honest 422, never a 500."""
    response = client.post("/api/studios/example-studio/zones/desk/power", json={"on": False})
    assert response.status_code == 422
    assert "refusing auto power-off of demo-screen" in response.json()["detail"]


@pytest.mark.unit
def test_all_off_respects_policies(monkeypatch):
    from app.api import power as power_api
    from app.drivers.mock import MockDriver

    drivers = {plug: MockDriver(plug) for plug in ("plug-1", "plug-2", "plug-3")}
    monkeypatch.setattr(power_api, "_plug_drivers", lambda registry, root: drivers)
    default = client.post("/api/studios/example-studio/power/all-off", json={})
    assert default.status_code == 200
    assert drivers["plug-1"].state["on"] is False
    assert "on" not in drivers["plug-3"].state
    assert default.json()["skipped"] == [
        {"device": "demo-screen", "plug": "plug-3", "reason": "manual-only (tick the box to include)"}
    ]
    full = client.post("/api/studios/example-studio/power/all-off", json={"include_manual": True})
    assert full.status_code == 200
    assert drivers["plug-3"].state["on"] is False
    assert full.json()["skipped"] == []
    assert client.post("/api/studios/nope/power/all-off", json={}).status_code == 404


@pytest.mark.unit
def test_obs_status_reports(monkeypatch):
    from app.api import obs as obs_api
    from app.drivers.base import DriverResult

    class StubObsModule:
        def list_scenes(self):
            return DriverResult(
                ok=True,
                state={"scenes": [{"sceneName": "Main"}, {"sceneName": "BRB"}], "currentProgramSceneName": "Main"},
            )

        def stream_status(self):
            return DriverResult(ok=True, state={"outputActive": False})

        def list_collections(self):
            return DriverResult(
                ok=True,
                state={"sceneCollections": ["Untitled", "ScottyPhoton"], "currentSceneCollectionName": "Untitled"},
            )

    monkeypatch.setattr(obs_api, "_studio_obs", lambda root, studio: (StubObsModule(), "BRB"))
    body = client.get("/api/studios/example-studio/obs/status").json()
    assert body["configured"] is True
    assert body["connected"] is True
    assert body["current"] == "Main"
    assert body["streaming"] is False
    assert body["collection"] == "Untitled"
    assert body["collections"] == ["Untitled", "ScottyPhoton"]


@pytest.mark.unit
def test_obs_status_skips_followups_when_down(monkeypatch):
    """One refused probe answers status; stream/collections are not retried."""
    from app.api import obs as obs_api
    from app.drivers.base import DriverResult

    class StubObsModule:
        def list_scenes(self):
            return DriverResult(ok=False, state={}, error="refused")

        def stream_status(self):
            raise AssertionError("must not be called when scenes fail")

        def list_collections(self):
            raise AssertionError("must not be called when scenes fail")

    monkeypatch.setattr(obs_api, "_studio_obs", lambda root, studio: (StubObsModule(), None))
    body = client.get("/api/studios/example-studio/obs/status").json()
    assert body["connected"] is False
    assert body["current"] is None
    assert body["streaming"] is None
    assert body["scenes"] == []
    assert body["collection"] is None
    assert body["collections"] == []
    monkeypatch.setattr(obs_api, "_studio_obs", lambda root, studio: None)
    assert client.get("/api/studios/example-studio/obs/status").json()["configured"] is False
    assert client.get("/api/studios/nope/obs/status").status_code == 404


@pytest.mark.unit
def test_obs_switch_uses_safe_fallback(monkeypatch):
    from app.api import obs as obs_api
    from app.drivers.base import DriverResult

    class StubObsModule:
        def __init__(self):
            self.seen = None

        def switch_scene(self, scene, safe_scene=None):
            self.seen = (scene, safe_scene)
            if scene == "Main":
                return DriverResult(ok=True, state={"scene": "Main"})
            return DriverResult(ok=True, state={"scene": "BRB", "fallback": True, "requested": scene})

    stub = StubObsModule()
    monkeypatch.setattr(obs_api, "_studio_obs", lambda root, studio: (stub, "BRB"))
    fell_back = client.post("/api/studios/example-studio/obs/switch", json={"scene": "Nope"})
    assert fell_back.status_code == 200
    assert fell_back.json()["state"] == {"scene": "BRB", "fallback": True, "requested": "Nope"}
    assert stub.seen == ("Nope", "BRB")
    monkeypatch.setattr(obs_api, "_studio_obs", lambda root, studio: None)
    assert client.post("/api/studios/example-studio/obs/switch", json={"scene": "Main"}).status_code == 422


@pytest.mark.unit
def test_obs_collection_switches(monkeypatch):
    from app.api import obs as obs_api
    from app.drivers.base import DriverResult

    class StubObsModule:
        def __init__(self):
            self.seen = None

        def set_collection(self, collection):
            self.seen = collection
            return DriverResult(ok=True, state={"collection": collection})

    stub = StubObsModule()
    monkeypatch.setattr(obs_api, "_studio_obs", lambda root, studio: (stub, None))
    switched = client.post("/api/studios/example-studio/obs/collection", json={"collection": "ScottyPhoton"})
    assert switched.status_code == 200
    assert switched.json()["state"] == {"collection": "ScottyPhoton"}
    assert stub.seen == "ScottyPhoton"
    monkeypatch.setattr(obs_api, "_studio_obs", lambda root, studio: None)
    assert (
        client.post("/api/studios/example-studio/obs/collection", json={"collection": "X"}).status_code == 422
    )


@pytest.mark.unit
def test_obs_stream_guarded(monkeypatch):
    from app.api import obs as obs_api
    from app.core.safety import SafetyViolation
    from app.drivers.base import DriverResult

    class StubObsModule:
        def __init__(self):
            self.streaming = False

        def set_streaming(self, action, confirmed=False):
            if not confirmed:
                raise SafetyViolation("refusing unconfirmed stream start")
            self.streaming = action == "start"
            return DriverResult(ok=True, state={"streaming": self.streaming})

    monkeypatch.setattr(obs_api, "_studio_obs", lambda root, studio: (StubObsModule(), None))
    base = "/api/studios/example-studio/obs/stream"
    assert client.post(base, json={"action": "start", "confirmed": False}).status_code == 422
    started = client.post(base, json={"action": "start", "confirmed": True})
    assert started.status_code == 200
    assert started.json()["state"] == {"streaming": True}


@contextmanager
def _clean_boot_state():
    """Isolate boot-job tests from the app-global job store."""
    jobs = app.state.__dict__.setdefault("boot_jobs", {})
    stops = app.state.__dict__.setdefault("boot_stops", {})
    saved_jobs, saved_stops = dict(jobs), dict(stops)
    jobs.clear()
    stops.clear()
    try:
        yield
    finally:
        jobs.clear()
        stops.clear()
        jobs.update(saved_jobs)
        stops.update(saved_stops)


@pytest.mark.unit
def test_obs_boot_lifecycle(monkeypatch):
    import threading
    import time

    from app.api import obs as obs_api

    proceed = threading.Event()

    def fake_run(job, link, obs_set, sleep=None, stop=None, cfg=None, collect_set=None):
        assert stop is not None and proceed.wait(timeout=10)
        job["state"] = "ready"
        return job

    monkeypatch.setattr(obs_api, "run_boot", fake_run)
    base = "/api/studios/example-studio/obs/boot"
    with _clean_boot_state():
        assert client.get(base + "/status").status_code == 404
        assert client.post(base + "/stop").status_code == 404
        started = client.post(base, json={})
        assert started.status_code == 200
        job_id = started.json()["job"]["id"]
        assert client.post(base, json={}).status_code == 409
        mid = client.get(base + "/status").json()["job"]
        assert mid["id"] == job_id and mid["running"] is True
        assert mid["scene"] == "Face Full View"
        named = client.post(base, json={"scene": "Custom"})
        assert named.status_code == 409  # running job wins over a new scene
        proceed.set()
        deadline = time.monotonic() + 10
        while client.get(base + "/status").json()["job"]["running"] and time.monotonic() < deadline:
            time.sleep(0.05)
        done = client.get(base + "/status").json()["job"]
        assert done["running"] is False and done["state"] == "ready"
        again = client.post(base, json={"scene": "Custom", "collection": "ScottyPhoton"})
        assert again.status_code == 200
        assert again.json()["job"]["scene"] == "Custom"
        assert again.json()["job"]["collection"] == "ScottyPhoton"
        assert client.post("/api/studios/nope/obs/boot", json={}).status_code == 404


@pytest.mark.unit
def test_obs_boot_stop_and_attach(monkeypatch):
    import time

    from types import SimpleNamespace

    from app.api import obs as obs_api

    def fake_run(job, link, obs_set, sleep=None, stop=None, cfg=None, collect_set=None):
        assert stop is not None and stop.wait(timeout=10)
        job["state"] = "stopped"
        return job

    monkeypatch.setattr(obs_api, "run_boot", fake_run)
    base = "/api/studios/example-studio/obs/boot"
    with _clean_boot_state():
        started = client.post(base, json={})
        assert started.status_code == 200
        job_id = started.json()["job"]["id"]
        attached, is_new = obs_api.boot_try_start(SimpleNamespace(app=app), "example-studio")
        assert is_new is False and attached["id"] == job_id
        stopped = client.post(base + "/stop")
        assert stopped.status_code == 200
        deadline = time.monotonic() + 10
        while client.get(base + "/status").json()["job"]["running"] and time.monotonic() < deadline:
            time.sleep(0.05)
        assert client.get(base + "/status").json()["job"]["state"] == "stopped"


@pytest.mark.unit
def test_plug_drivers_resolve_alias_to_live_host(monkeypatch):
    """Modes power dials the live host for a plug alias, not the stored one."""
    from app.api import modes as modes_api

    seen = {}

    def fake_plug_map(root):
        return [
            {
                "plug": "plug-9",
                "alias": "Studio Plug 9",
                "host": "192.0.2.99",
                "zone": "bench",
                "powerGroup": "bench-power",
            }
        ]

    def fake_resolve(alias, username=None, password=None, fallback=None):
        seen["alias"] = alias
        seen["fallback"] = fallback
        return "192.0.2.21"

    monkeypatch.setattr(modes_api, "_plug_map", fake_plug_map)
    monkeypatch.setattr(modes_api, "resolve_host", fake_resolve)
    registry = app.state.registries["example-studio"]
    drivers = modes_api._plug_drivers(registry, CONFIG_ROOT)
    assert drivers["plug-9"].host == "192.0.2.21"
    assert seen == {"alias": "Studio Plug 9", "fallback": "192.0.2.99"}
