# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for hardware snapshots (live rig look -> saved preset)."""

import json
import shutil
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.drivers.base import DriverResult
from app.main import app

client = TestClient(app)
CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config"

SNAP_DEVICES = [
    {"id": "snap-gvm", "name": "Snap GVM", "type": "light", "zone": "bench",
     "profile": "gvm-1500d", "driver": "gvm", "driverAddress": {"ble": "AA:BB:CC:DD:EE:99"}},
    {"id": "snap-ninja", "name": "Snap Ninja", "type": "light", "zone": "bench",
     "profile": "viltrox-ninja-400", "driver": "ninja",
     "driverAddress": {"channel": 3, "group": "F"}},
    {"id": "snap-dmx", "name": "Snap DMX", "type": "light", "zone": "bench",
     "profile": "viltrox-ninja-400", "driver": "dmx",
     "driverAddress": {"dmx": {"dimmer": 40, "cct": 41}}},
    {"id": "snap-screen", "name": "Snap Screen", "type": "screen", "zone": "bench", "driver": "screen"},
    {"id": "snap-strip", "name": "Snap Strip", "type": "lightstrip", "zone": "bench",
     "profile": "bytech-strip", "driver": "ir"},
]


@contextmanager
def _tmp_snapshot_studio(tmp_path):
    """Tmp config root: example studio + snapshot-family devices (id "x")."""
    from app.config.loader import load_all
    from app.registry import Registry

    shutil.copytree(CONFIG_ROOT / "profiles", tmp_path / "profiles")
    studio_dir = tmp_path / "studios.local" / "x"
    shutil.copytree(CONFIG_ROOT / "studios" / "example-studio", studio_dir)
    meta_path = studio_dir / "studio.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["id"] = "x"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    devices_path = studio_dir / "devices.json"
    devices = json.loads(devices_path.read_text(encoding="utf-8"))
    devices.extend(SNAP_DEVICES)
    devices_path.write_text(json.dumps(devices), encoding="utf-8")

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


@pytest.fixture
def live_state():
    """Seeded hub-known state with global cleanup (panel looks, radio cache, screens)."""
    from app.drivers import gvm_ble
    from app.api import ninja as ninja_api
    from app.drivers.ninja_ble import NinjaBleDriver
    from app.drivers.screen import SCREEN_STATE

    saved_panels = dict(gvm_ble._PANEL_LOOKS)
    saved_drivers = dict(ninja_api._DRIVERS)
    saved_screens = dict(SCREEN_STATE)
    gvm_ble._PANEL_LOOKS["AA:BB:CC:DD:EE:99"] = {
        "brightness": 12, "cct": 4500, "hue": 210, "saturation": 80, "page": "hsi",
    }
    radio = NinjaBleDriver("snap-radio", 3, "F", backend="mock")
    assert radio.apply({"brightness": 7, "cct": 5600}).ok
    ninja_api._DRIVERS[(3, "F", False)] = radio
    SCREEN_STATE["snap-screen"] = {"theme": "live", "text": "hi"}
    try:
        yield {"radio": radio}
    finally:
        gvm_ble._PANEL_LOOKS.clear()
        gvm_ble._PANEL_LOOKS.update(saved_panels)
        ninja_api._DRIVERS.clear()
        ninja_api._DRIVERS.update(saved_drivers)
        SCREEN_STATE.clear()
        SCREEN_STATE.update(saved_screens)


@pytest.mark.unit
def test_gvm_look_records_only_successes(monkeypatch):
    """Panel looks merge per successful send; failures leave memory alone."""
    from app.drivers import gvm_ble

    saved = dict(gvm_ble._PANEL_LOOKS)
    gvm_ble._PANEL_LOOKS.clear()
    try:
        monkeypatch.setattr(gvm_ble, "_bleak_error", lambda: None)

        def _fake_run(coro):
            """Drive an await-free coroutine with no event loop (sandbox-safe)."""
            try:
                coro.send(None)
            except StopIteration as done:
                return done.value
            raise AssertionError("unexpected suspension")

        async def _ok(address, frame, timeout):
            return {"bytes": len(frame)}

        async def _boom(address, frame, timeout):
            raise RuntimeError("radio down")

        monkeypatch.setattr(gvm_ble.asyncio, "run", _fake_run)
        monkeypatch.setattr(gvm_ble, "_awrite", _ok)
        driver = gvm_ble.GvmBleDriver("t", "AA:BB:CC:DD:EE:01")
        assert driver.apply({"brightness": 5}).ok
        assert driver.apply({"cct": 5000}).ok
        assert gvm_ble.panel_look("AA:BB:CC:DD:EE:01") == {"brightness": 5, "cct": 5000}
        assert gvm_ble.panel_look("AA:BB:CC:DD:EE:02") == {}
        monkeypatch.setattr(gvm_ble, "_awrite", _boom)
        assert not driver.apply({"brightness": 9}).ok
        assert gvm_ble.panel_look("AA:BB:CC:DD:EE:01")["brightness"] == 5
    finally:
        gvm_ble._PANEL_LOOKS.clear()
        gvm_ble._PANEL_LOOKS.update(saved)


@pytest.mark.unit
def test_dmx_emitted_tracks_first_frame(monkeypatch):
    """Universe reports pristine until a frame actually goes out."""
    from app.drivers import dmx_usb

    universe = dmx_usb.DmxUsbDriver("t", port="COM9")
    assert universe.emitted is False
    assert universe.get_state().state["emitted"] is False
    monkeypatch.setattr(dmx_usb, "open_port", lambda port: object())
    monkeypatch.setattr(dmx_usb, "_send_frames", lambda handle, frame, count: None)
    universe.apply({"channels": {"1": 26}})
    assert universe.emitted is True


@pytest.mark.unit
def test_ninja_cached_driver_never_fabricates(monkeypatch):
    """Snapshot lookup returns live cache entries only (bad/unknown -> None)."""
    from app.api import ninja as ninja_api
    from app.drivers.ninja_ble import NinjaBleDriver

    assert ninja_api.cached_driver(99, "A") is None
    assert ninja_api.cached_driver(3, "Z") is None
    saved = dict(ninja_api._DRIVERS)
    try:
        radio = NinjaBleDriver("t", 3, "E", backend="mock")
        ninja_api._DRIVERS[(3, "E", False)] = radio
        assert ninja_api.cached_driver(3, "e") is radio
    finally:
        ninja_api._DRIVERS.clear()
        ninja_api._DRIVERS.update(saved)


@pytest.mark.unit
def test_from_hardware_roundtrip(tmp_path, live_state, monkeypatch):
    """Live looks snapshot through the strict gate, persist, and reload."""
    from app.api import presets as presets_api

    class StubUniverse:
        emitted = True
        blackout = False

        def get_state(self):
            return DriverResult(ok=True, state={"channels": {"40": 26, "41": 128}})

    monkeypatch.setattr(presets_api, "get_shared_driver", lambda: StubUniverse())
    with _tmp_snapshot_studio(tmp_path) as studio_dir:
        saved = client.post("/api/studios/x/presets/from-hardware", json={"preset_id": "snap-1"})
        assert saved.status_code == 200
        body = saved.json()
        assert body["preset"] == "snap-1" and body["overwrote"] is False
        assert body["lights"]["snap-gvm"] == {
            "intensity": 12, "cct": 4500, "hue": 210, "saturation": 80, "page": "hsi",
        }
        assert body["lights"]["snap-ninja"] == {"intensity": 7, "cct": 5600}
        assert body["lights"]["snap-dmx"]["intensity"] == 10
        assert "cct" in body["lights"]["snap-dmx"]
        assert body["screens"]["snap-screen"] == {"theme": "live", "text": "hi"}
        skipped = {item["target"]: item["reason"] for item in body["skipped"]}
        assert "snap-strip" in skipped  # strips hold no readable state
        stored = json.loads((studio_dir / "presets.json").read_text(encoding="utf-8"))
        assert stored["snap-1"]["lights"]["snap-ninja"] == {"intensity": 7, "cct": 5600}
        dupe = client.post("/api/studios/x/presets/from-hardware", json={"preset_id": "snap-1"})
        assert dupe.status_code == 409
        assert (
            client.post(
                "/api/studios/x/presets/from-hardware",
                json={"preset_id": "snap-1", "overwrite": True},
            ).status_code
            == 200
        )


@pytest.mark.unit
def test_from_hardware_skips_and_errors(tmp_path, live_state, monkeypatch):
    """Scenes, untouched rigs, and bad ids fail honestly with named skips."""
    from app.api import presets as presets_api
    from app.drivers import gvm_ble

    class DarkUniverse:
        emitted = False
        blackout = False

        def get_state(self):
            return DriverResult(ok=True, state={"channels": {}})

    monkeypatch.setattr(presets_api, "get_shared_driver", lambda: DarkUniverse())
    with _tmp_snapshot_studio(tmp_path):
        assert client.post("/api/studios/nope/presets/from-hardware", json={"preset_id": "x"}).status_code == 404
        unknown = client.post(
            "/api/studios/x/presets/from-hardware", json={"preset_id": "x", "lights": ["ghost"]}
        )
        assert unknown.status_code == 422
        gvm_ble._PANEL_LOOKS["AA:BB:CC:DD:EE:99"] = {"scene": 3, "brightness": 20}
        scenic = client.post(
            "/api/studios/x/presets/from-hardware", json={"preset_id": "x", "lights": ["snap-gvm"], "screens": []}
        )
        assert scenic.status_code == 422
        assert "scene" in scenic.json()["detail"]
        dark = client.post(
            "/api/studios/x/presets/from-hardware", json={"preset_id": "x", "lights": ["snap-dmx"], "screens": []}
        )
        assert dark.status_code == 422
        assert "never emitted" in dark.json()["detail"]
        evil = client.post("/api/studios/x/presets/from-hardware", json={"preset_id": "../evil"})
        assert evil.status_code == 422
