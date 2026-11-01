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
        def __init__(self, driver_id, host, port, password):
            made["ctor"] = (driver_id, host, port, password)
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
def test_modes_apply_home_hands_on_drives_dmx(monkeypatch):
    """hands_on sets both DMX fixtures (stub universe, plugs unwired: no hardware)."""
    from app.api import modes as modes_api
    from app.drivers.dmx_usb import dmx_for_cct, dmx_for_percent

    stub = StubUniverse()
    FakeGvm.instances = {}
    monkeypatch.setattr(modes_api, "_plug_drivers", lambda registry, root: {})
    monkeypatch.setattr(modes_api, "_obs_applier", lambda registry, root: None)
    monkeypatch.setattr(modes_api, "_screens_applier", lambda registry: None)
    monkeypatch.setattr(modes_api, "get_shared_driver", lambda port=None: stub)
    monkeypatch.setattr(modes_api, "GvmBleDriver", FakeGvm)
    response = client.post("/api/studios/home-studio/modes/hands_on/apply")
    assert response.status_code == 200
    lights = response.json()["subsystems"]["lights"]
    assert lights["status"] == "ready"
    assert stub.channels == {
        1: dmx_for_percent(2),
        2: dmx_for_cct(5600, 2500, 8500),
        20: dmx_for_percent(2),
    }
    assert FakeGvm.instances["gvm-ho"].calls == [{"brightness": 5}, {"cct": 5000}]
    faded = client.post("/api/studios/home-studio/modes/hands_on/apply", json={"fade_s": 0.5})
    assert faded.status_code == 200
    assert faded.json()["subsystems"]["lights"]["status"] == "ready"
    assert ("fade", {1: 2}, "percent", 0.5) in stub.calls
    assert ("fade", {20: 2}, "percent", 0.5) in stub.calls


@pytest.mark.unit
def test_build_modes_module_wires_home_dmx_without_http(monkeypatch):
    """Same hands_on path through the module directly (runs socket-free)."""
    from app.api import modes as modes_api
    from app.config.loader import load_all
    from app.drivers.dmx_usb import dmx_for_cct, dmx_for_percent
    from app.registry import Registry as RegistryCls

    data = load_all(CONFIG_ROOT)
    registry = RegistryCls.from_loaded(data["studios"]["home-studio"], data["profiles"])
    stub = StubUniverse()
    FakeGvm.instances = {}
    monkeypatch.setattr(modes_api, "_plug_drivers", lambda reg, root: {})
    monkeypatch.setattr(modes_api, "_obs_applier", lambda reg, root: None)
    monkeypatch.setattr(modes_api, "_screens_applier", lambda reg: None)
    monkeypatch.setattr(modes_api, "get_shared_driver", lambda port=None: stub)
    monkeypatch.setattr(modes_api, "GvmBleDriver", FakeGvm)
    module = modes_api.build_modes_module(registry, CONFIG_ROOT)
    result = module.apply_mode("hands_on", sleep=lambda s: None)
    assert result["subsystems"]["lights"]["status"] == "ready"
    assert stub.channels == {
        1: dmx_for_percent(2),
        2: dmx_for_cct(5600, 2500, 8500),
        20: dmx_for_percent(2),
    }
    assert FakeGvm.instances["gvm-ho"].calls == [{"brightness": 5}, {"cct": 5000}]
    faded = module.apply_mode("hands_on", sleep=lambda s: None, fade_s=0.5)
    assert faded["subsystems"]["lights"]["status"] == "ready"
    assert ("fade", {1: 2}, "percent", 0.5) in stub.calls
    assert ("fade", {20: 2}, "percent", 0.5) in stub.calls


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
    assert body["directives"]["lights"] == {"demo-key": {"intensity": 30, "cct": 4200}}
    assert body["critique"] == ["clean: all directives within declared capabilities"]
    assert body["apply"]["subsystems"]["lights"]["status"] == "skipped"
    assert body["preset_candidate"]["label"] == "cozy interview"


@pytest.mark.unit
def test_ai_look_bad_targets_422():
    response = client.post("/api/studios/example-studio/ai/look", json={"prompt": "x", "targets": "smell"})
    assert response.status_code == 422


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
    solo = client.post("/api/testbench/dmx/light", json={"fixture": "ninja-20", "power": 3})
    assert solo.status_code == 200
    assert calls[-1] == ("apply", {"channels": {20: 8}})
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
