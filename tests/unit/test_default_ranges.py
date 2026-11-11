# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for profile-driven value ranges (single source of truth).

Ranges live in profiles/*.json spec blocks (per-model truth); the ranges
resolver serves them to every page and the defaults gate validates against
them. Devices without a profile range fall back to the documented generic
outer bounds. Changing a profile range after the fact must never break
loads: stored sets keep their bytes and only new saves face the gate.
"""

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


def _set(device_id, values, set_id="t"):
    return {"set_id": set_id, "devices": {device_id: values}, "plugs": {}}


DUAL_DEVICES = [
    {"id": "t-ninja", "name": "T Ninja", "type": "light", "zone": "bench",
     "profile": "viltrox-ninja-400", "driver": "ninja",
     "driverAddress": {"channel": 3, "group": "F"}},
    {"id": "dual-1", "name": "Dual One", "type": "light", "zone": "bench",
     "profile": "viltrox-ninja-400", "driver": "ninja",
     "driverAddress": {"channel": 3, "group": "E", "dmx": {"dimmer": 40, "cct": 41}},
     "transport": "ble"},
]


@contextmanager
def _tmp_dual_studio(tmp_path):
    """Tmp local studio plus a BLE-only Ninja and a dual-protocol fixture."""
    with _tmp_local_studio(tmp_path) as studio_dir:
        from app.config.loader import check_references, load_profiles, load_studio
        from app.main import app as _app
        from app.registry import Registry

        devices_path = studio_dir / "devices.json"
        devices = json.loads(devices_path.read_text(encoding="utf-8"))
        devices.extend(DUAL_DEVICES)
        devices_path.write_text(json.dumps(devices), encoding="utf-8")
        loaded = load_studio(tmp_path, "x")
        check_references(loaded, load_profiles(tmp_path), "x")
        _app.state.registries["x"] = Registry.from_loaded(loaded, load_profiles(tmp_path))
        yield studio_dir


@pytest.mark.unit
def test_defaults_reject_cct_outside_profile_range(tmp_path):
    """demo-key uses demo-flood (cct 3200..5600): 2500 must fail, edges pass."""
    client = TestClient(app)
    with _tmp_local_studio(tmp_path):
        base = "/api/studios/x/defaults"
        assert client.post(base, json=_set("demo-key", {"cct": 2500}, "t1")).status_code == 422
        assert client.post(base, json=_set("demo-key", {"cct": 3200}, "t2")).status_code == 200
        assert client.post(base, json=_set("demo-key", {"cct": 5600, "intensity": 5}, "t3")).status_code == 200
        assert client.post(base, json=_set("demo-key", {"cct": 5601}, "t4")).status_code == 422


@pytest.mark.unit
def test_defaults_generic_fallback_when_profile_has_no_range(tmp_path):
    """demo-rgb has no cctRange: generic 2500..8500 applies; hue stays open."""
    client = TestClient(app)
    with _tmp_local_studio(tmp_path):
        base = "/api/studios/x/defaults"
        assert client.post(base, json=_set("demo-rgb", {"cct": 2500}, "t1")).status_code == 200
        assert client.post(base, json=_set("demo-rgb", {"hue": 200, "saturation": 50}, "t2")).status_code == 200
        assert client.post(base, json=_set("demo-rgb", {"hue": 361}, "t3")).status_code == 422
        assert client.post(base, json=_set("demo-rgb", {"intensity": 101}, "t4")).status_code == 422


@pytest.mark.unit
def test_defaults_capability_gate_unchanged(tmp_path):
    """demo-key (rgb-less) still refuses hue/saturation after the rework."""
    client = TestClient(app)
    with _tmp_local_studio(tmp_path):
        base = "/api/studios/x/defaults"
        assert client.post(base, json=_set("demo-key", {"hue": 200}, "t1")).status_code == 422
        assert client.post(base, json=_set("demo-key", {"saturation": 10}, "t2")).status_code == 422


@pytest.mark.unit
def test_ranges_endpoint_serves_profile_and_generic_sources(tmp_path):
    """One resolver for every page: profile ranges win, gaps say generic."""
    client = TestClient(app)
    with _tmp_local_studio(tmp_path):
        response = client.get("/api/studios/x/ranges")
        assert response.status_code == 200
        devices = response.json()["devices"]
        key_cct = devices["demo-key"]["values"]["cct"]
        assert [key_cct["min"], key_cct["max"]] == [3200, 5600]
        assert key_cct["source"] == "profile:demo-flood"
        rgb_cct = devices["demo-rgb"]["values"]["cct"]
        assert [rgb_cct["min"], rgb_cct["max"]] == [2500, 8500]
        assert rgb_cct["source"] == "generic"
        assert devices["demo-key"]["values"]["hue"]["supported"] is False
        assert devices["demo-rgb"]["values"]["hue"]["supported"] is True
        assert client.get("/api/studios/nope/ranges").status_code == 404


@pytest.mark.unit
def test_stored_out_of_range_sets_still_load(tmp_path):
    """Narrowing a profile range later must not break studio loads."""
    client = TestClient(app)
    with _tmp_local_studio(tmp_path):
        base = "/api/studios/x/defaults"
        assert client.post(base, json=_set("demo-rgb", {"cct": 8500})).status_code == 200
        profile_path = tmp_path / "profiles" / "demo-rgb.json"
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        profile["spec"]["cctRange"] = [3200, 5600]
        profile_path.write_text(json.dumps(profile), encoding="utf-8")
        from app.config.loader import check_references, load_profiles, load_studio

        loaded = load_studio(tmp_path, "x")
        check_references(loaded, load_profiles(tmp_path), "x")  # must not raise
        assert loaded["defaults"]["t"]["devices"]["demo-rgb"] == {"cct": 8500}


@pytest.mark.unit
def test_defaults_page_gated_to_panel_family(tmp_path):
    """page (cct/hsi) is a GVM panel concept: Ninja/DMX saves refuse it."""
    client = TestClient(app)
    with _tmp_dual_studio(tmp_path):
        base = "/api/studios/x/defaults"
        assert client.post(base, json=_set("t-ninja", {"page": "hsi"}, "p1")).status_code == 422
        assert client.post(base, json=_set("dual-1", {"page": "cct"}, "p2")).status_code == 422
        assert client.post(base, json=_set("demo-key", {"page": "cct"}, "p3")).status_code == 200


@pytest.mark.unit
def test_defaults_transport_dual_fixtures_only(tmp_path):
    """transport saves on dual-address fixtures; BLE-only ones refuse it."""
    client = TestClient(app)
    with _tmp_dual_studio(tmp_path):
        base = "/api/studios/x/defaults"
        assert client.post(base, json=_set("dual-1", {"transport": "dmx"}, "t1")).status_code == 200
        assert client.post(base, json=_set("dual-1", {"transport": "ble"}, "t2")).status_code == 200
        assert client.post(base, json=_set("dual-1", {"transport": "wifi"}, "t3")).status_code == 422
        assert client.post(base, json=_set("t-ninja", {"transport": "ble"}, "t4")).status_code == 422
        assert client.post(base, json=_set("demo-key", {"transport": "ble"}, "t5")).status_code == 422


@pytest.mark.unit
def test_ranges_screen_choices_page_and_transport_shapes(tmp_path):
    """Ranges serve screen option lists and gate page/transport per device."""
    client = TestClient(app)
    with _tmp_dual_studio(tmp_path):
        devices = client.get("/api/studios/x/ranges").json()["devices"]
        screen = devices["demo-screen"]["values"]
        assert "work" in screen["theme"]["choices"] and "live" in screen["theme"]["choices"]
        assert "fireplace" in screen["animation"]["choices"]
        assert screen["animation"]["strict"] is True
        assert "page" in devices["demo-key"]["values"]  # mock panel keeps pages
        assert "page" not in devices["t-ninja"]["values"]
        assert "page" not in devices["dual-1"]["values"]
        assert devices["dual-1"]["values"]["transport"] == {"enum": ["ble", "dmx"]}
        assert "transport" not in devices["t-ninja"]["values"]
        assert "transport" not in devices["demo-key"]["values"]
