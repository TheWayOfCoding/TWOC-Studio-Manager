# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for presentation sequences (tmp studios, mock drivers, fake sleep)."""

import json
import threading
from contextlib import contextmanager

import pytest

from app.drivers.mock import MockDriver
from app.drivers.screen import SCREEN_STATE, ScreenDriver
from app.modules.modes import ModesModule
from app.modules.screens import ScreensModule
from app.modules.shows import ShowsError, list_shows, load_show, run_show
from app.registry import Registry


@pytest.fixture(autouse=True)
def clean_screen_state():
    SCREEN_STATE.clear()
    yield
    SCREEN_STATE.clear()


@pytest.fixture()
def stage():
    """Real modes + screens pipeline over mock drivers (no hardware)."""
    loaded = {
        "studio": {"id": "x", "label": "X"},
        "devices": [
            {"id": "key", "name": "Key Light", "zone": "tzone", "type": "light",
             "driver": "mock", "controllable": True, "profile": "tp"},
            {"id": "wall", "name": "Wall", "zone": "tzone", "type": "screen", "driver": "screen"},
        ],
        "zones": [{"id": "tzone", "label": "T"}],
        "modes": [],
        "presets": {},
    }
    profiles = {
        "tp": {
            "spec": {"intensityRange": [0, 100], "cctRange": [3200, 5600],
                     "hueRange": [0, 360], "saturationRange": [0, 100]},
            "capabilities": {"intensity": True, "cct": True, "rgb": True},
        }
    }
    registry = Registry.from_loaded(loaded, profiles)
    lights = {"key": MockDriver("key", initial_state={})}
    modes = ModesModule("modes", registry, lights=lights)
    screens = ScreensModule("screens", {"wall": ScreenDriver("screen-wall", "wall")})
    return {"modes": modes, "screens": screens, "registry": registry}


def _write_shows(tmp_path, shows):
    studio = tmp_path / "studios.local" / "x"
    studio.mkdir(parents=True)
    (studio / "shows.json").write_text(json.dumps(shows), encoding="utf-8")
    return tmp_path


@pytest.mark.unit
def test_list_and_load_roundtrip(tmp_path):
    root = _write_shows(
        tmp_path,
        {
            "demo": {
                "label": "Demo",
                "steps": [
                    {"label": "peak", "lights": {"key": {"intensity": 100}}, "hold_s": 2},
                    {"screen": {"wall": {"text": "Intermission"}}, "hold_s": 1},
                ],
            }
        },
    )
    assert list_shows(root, "x") == [{"id": "demo", "label": "Demo", "steps": 2}]
    show = load_show(root, "x", "demo")
    assert show["label"] == "Demo"
    assert show["steps"][0]["hold_s"] == 2
    assert show["steps"][1]["lights"] == {}


@pytest.mark.unit
def test_load_rejects_unknown_show_and_missing_file(tmp_path):
    root = _write_shows(tmp_path, {"demo": {"label": "D", "steps": []}})
    with pytest.raises(ShowsError, match="unknown show"):
        load_show(root, "x", "nope")
    with pytest.raises(ShowsError, match="no shows file"):
        load_show(root, "ghost", "demo")


@pytest.mark.unit
def test_load_rejects_malformed_steps(tmp_path):
    bad_shows = {
        "no-steps": {"label": "N"},
        "empty-steps": {"label": "E", "steps": []},
        "bad-hold": {"label": "B", "steps": [{"hold_s": -1}]},
        "bad-lights": {"label": "B", "steps": [{"lights": ["key"]}]},
        "bad-scene": {"label": "B", "steps": [{"scenes": {"key": 99}}]},
        "bad-key": {"label": "B", "steps": [{"pyrotechnics": True}]},
        "empty-step": {"label": "B", "steps": [{"hold_s": 1}]},
    }
    root = _write_shows(tmp_path, bad_shows)
    for show_id in bad_shows:
        with pytest.raises(ShowsError, match=show_id):
            load_show(root, "x", show_id)


def _show(*steps):
    return {"label": "Test", "steps": steps}


@pytest.mark.unit
def test_run_applies_steps_holds_and_narrates(stage):
    holds = []
    show = _show(
        {"label": "peak", "lights": {"key": {"intensity": 100, "cct": 5000}}, "hold_s": 2},
        {"label": "mood", "lights": {"key": {"hue": 220, "saturation": 60}}, "hold_s": 1},
    )
    result = run_show(stage["modes"], stage["screens"].apply_mapping, show, sleep=holds.append)
    assert result["state"] == "done" and result["ok"] is True
    assert abs(sum(holds) - 3.0) < 1e-6 and len(holds) > 2
    assert stage["modes"].lights["key"].state == {"intensity": 100, "cct": 5000, "hue": 220, "saturation": 60}
    wall = SCREEN_STATE["wall"]
    assert wall["text"] == "Key Light · hue 220 · saturation 60"
    assert wall["wash"] == {"color": "hsl(220,60%,55%)", "opacity": 0.7}
    assert wall["theme"] == "show"


@pytest.mark.unit
def test_run_validates_all_steps_before_moving(stage):
    show = _show(
        {"lights": {"key": {"intensity": 10}}, "hold_s": 1},
        {"lights": {"ghost": {"intensity": 10}}, "hold_s": 1},
    )
    with pytest.raises(ShowsError, match="ghost"):
        run_show(stage["modes"], stage["screens"].apply_mapping, show, sleep=lambda s: None)
    assert stage["modes"].lights["key"].calls == []
    assert SCREEN_STATE == {}


@pytest.mark.unit
def test_scenes_need_confirmation_and_narrate_by_name(stage):
    show = _show({"lights": {"key": {"intensity": 72}}, "scenes": {"key": 3}, "hold_s": 1})
    with pytest.raises(ShowsError, match="confirm"):
        run_show(stage["modes"], stage["screens"].apply_mapping, show, sleep=lambda s: None)
    assert stage["modes"].lights["key"].calls == []
    result = run_show(
        stage["modes"], stage["screens"].apply_mapping, show,
        sleep=lambda s: None, confirm_scenes=True,
    )
    assert result["ok"] is True
    assert ("apply", {"scene": 3}) in stage["modes"].lights["key"].calls
    wall = SCREEN_STATE["wall"]
    assert wall["text"] == "Key Light · intensity 72 · scene 3 (Candle)"
    assert wall["theme"] == "candle"
    assert wall["wash"] == {"color": "#ff9a2a", "opacity": 0.5}


@pytest.mark.unit
def test_plan_validates_scene_and_screen_ids(stage):
    bad_scenes = _show({"lights": {"key": {"intensity": 10}}, "scenes": {"ghost": 3}})
    with pytest.raises(ShowsError, match="ghost"):
        run_show(stage["modes"], stage["screens"].apply_mapping, bad_scenes, sleep=lambda s: None,
                 confirm_scenes=True)
    bad_screen = _show({"screen": {"ghost": {"text": "x"}}})
    with pytest.raises(ShowsError, match="ghost"):
        run_show(stage["modes"], stage["screens"].apply_mapping, bad_screen, sleep=lambda s: None)
    not_screen = _show({"screen": {"key": {"text": "x"}}})
    with pytest.raises(ShowsError, match="not a screen"):
        run_show(stage["modes"], stage["screens"].apply_mapping, not_screen, sleep=lambda s: None)
    assert stage["modes"].lights["key"].calls == []


@pytest.mark.unit
def test_stop_jumps_to_final_step(stage):
    stop = threading.Event()
    holds = []

    def sleep_and_stop(seconds):
        holds.append(seconds)
        stop.set()

    show = _show(
        {"label": "one", "lights": {"key": {"intensity": 10}}, "hold_s": 1},
        {"label": "two", "lights": {"key": {"intensity": 50}}, "hold_s": 1},
        {"label": "home", "lights": {"key": {"intensity": 15, "cct": 5000}}, "hold_s": 9},
    )
    result = run_show(stage["modes"], stage["screens"].apply_mapping, show, sleep=sleep_and_stop, stop=stop)
    assert result["state"] == "stopped"
    assert holds == [0.2]
    assert stage["modes"].lights["key"].state == {"intensity": 15, "cct": 5000}
    assert SCREEN_STATE["wall"]["text"] == "Key Light · intensity 15 · cct 5000"


@pytest.mark.unit
def test_failures_narrated_honestly_and_show_goes_on(stage):
    stage["modes"].lights["key"] = MockDriver("key", initial_state={}, fail_on=("intensity",))
    show = _show(
        {"lights": {"key": {"intensity": 100}}, "hold_s": 1},
        {"lights": {"key": {"cct": 5000}}, "hold_s": 1},
    )
    result = run_show(stage["modes"], stage["screens"].apply_mapping, show, sleep=lambda s: None)
    assert result["state"] == "done" and result["ok"] is False
    first_caption = result["steps"][0]["caption"]
    assert "FAILED" in first_caption
    assert result["steps"][0]["wash"] == {"color": "#ff2a2a", "opacity": 0.45}
    assert result["steps"][1]["ok"] is True


@pytest.mark.unit
def test_screen_only_step_and_caption_override(stage):
    show = _show(
        {"label": "title", "screen": {"wall": {"text": "GVM Showcase"}}, "hold_s": 1},
        {"label": "branded", "lights": {"key": {"intensity": 5}}, "caption": "Encore", "hold_s": 1},
    )
    result = run_show(stage["modes"], stage["screens"].apply_mapping, show, sleep=lambda s: None)
    assert result["ok"] is True
    assert result["steps"][0]["caption"] == "GVM Showcase"
    assert SCREEN_STATE["wall"]["text"] == "Encore"


@contextmanager
def _tmp_shows_app(tmp_path, shows):
    """Real app pointed at a tmp config root whose studio x has shows.json."""
    import shutil
    from pathlib import Path

    from app.main import app

    config_fixture = Path(__file__).resolve().parents[2] / "config"
    shutil.copytree(config_fixture / "profiles", tmp_path / "profiles")
    studio_dir = tmp_path / "studios.local" / "x"
    shutil.copytree(config_fixture / "studios" / "example-studio", studio_dir)
    meta_path = studio_dir / "studio.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["id"] = "x"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    devices_path = studio_dir / "devices.json"
    devices = json.loads(devices_path.read_text(encoding="utf-8"))
    devices.append({"id": "wall", "name": "Wall", "zone": "bench", "type": "screen", "driver": "screen"})
    devices_path.write_text(json.dumps(devices), encoding="utf-8")
    (studio_dir / "shows.json").write_text(json.dumps(shows), encoding="utf-8")

    from app.config.loader import load_all
    from app.registry import Registry

    data = load_all(tmp_path)
    saved = (app.state.config_root, dict(app.state.registries), getattr(app.state, "show_runs", None))
    app.state.config_root = tmp_path
    app.state.registries = {"x": Registry.from_loaded(data["studios"]["x"], data["profiles"])}
    app.state.show_runs = {}
    try:
        yield
    finally:
        for slot in app.state.show_runs.values():
            slot["stop"].set()
            slot["thread"].join(timeout=5)
        app.state.config_root, registries, runs = saved
        app.state.registries.clear()
        app.state.registries.update(registries)
        app.state.show_runs = runs if runs is not None else {}


def _wait_status(client, show_id, want, tries=50):
    import time as _time

    for _ in range(tries):
        body = client.get(f"/api/studios/x/shows/{show_id}/status").json()
        if body["state"] in want:
            return body
        _time.sleep(0.1)
    raise AssertionError(f"status never reached {want}: {body!r}")


@pytest.mark.unit
def test_shows_api_lifecycle(tmp_path):
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    shows = {
        "quick": {
            "label": "Quick",
            "steps": [{"screen": {"wall": {"text": "hi"}}, "hold_s": 0.05}],
        },
        "long": {
            "label": "Long",
            "steps": [
                {"screen": {"wall": {"text": "waiting"}}, "hold_s": 30},
                {"screen": {"wall": {"text": "landed"}}, "hold_s": 0.05},
            ],
        },
        "scary": {
            "label": "Scary",
            "steps": [{"lights": {"demo-key": {"intensity": 10}}, "scenes": {"demo-key": 3}, "hold_s": 0.05}],
        },
    }
    with _tmp_shows_app(tmp_path, shows):
        listed = client.get("/api/studios/x/shows")
        assert listed.status_code == 200
        assert {"id": "quick", "label": "Quick", "steps": 1} in listed.json()["shows"]

        assert client.get("/api/studios/x/shows/nope/status").status_code == 404
        assert client.post("/api/studios/x/shows/nope/run", json={}).status_code == 404
        scary = client.post("/api/studios/x/shows/scary/run", json={})
        assert scary.status_code == 422

        run = client.post("/api/studios/x/shows/quick/run", json={})
        assert run.status_code == 200
        assert run.json()["state"] == "running"
        done = _wait_status(client, "quick", ("done",))
        assert done["ok"] is True
        assert done["failed"] == []

        run = client.post("/api/studios/x/shows/long/run", json={})
        assert run.status_code == 200
        busy = client.post("/api/studios/x/shows/quick/run", json={})
        assert busy.status_code == 409
        stopped = client.post("/api/studios/x/shows/long/stop")
        assert stopped.status_code == 200
        final = _wait_status(client, "long", ("stopped",))
        assert final["step"] == 1


@pytest.mark.unit
def test_show_ignores_ai_cap(stage):
    """Authored intensities (even 100) reach the panel, cap or no cap."""
    stage["registry"]._devices["key"]["aiCap"] = {"intensity": 10}
    show = _show({"label": "peak", "lights": {"key": {"intensity": 100}}, "hold_s": 0.05})
    result = run_show(stage["modes"], stage["screens"].apply_mapping, show, sleep=lambda s: None)
    assert result["ok"] is True
    assert stage["modes"].lights["key"].state == {"intensity": 100}


@pytest.mark.unit
def test_show_scene_applies_last_through_modes(stage):
    """Confirmed scenes ride the same modes apply as the step's lights."""
    show = _show({"lights": {"key": {"intensity": 72}}, "scenes": {"key": 3}, "hold_s": 0.05})
    result = run_show(
        stage["modes"], stage["screens"].apply_mapping, show,
        sleep=lambda s: None, confirm_scenes=True,
    )
    assert result["ok"] is True
    assert stage["modes"].lights["key"].calls == [
        ("apply", {"intensity": 72}),
        ("apply", {"scene": 3}),
    ]
