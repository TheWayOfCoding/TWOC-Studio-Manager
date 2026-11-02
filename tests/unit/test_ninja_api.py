# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the Ninja bench API (mock backend only, no radio).

The API caches one driver per address, so each test uses a distinct
channel/group pair to stay order-independent.
"""

from app.api.ninja import _fixed_cct
from app.main import app
from app.registry import Registry
from fastapi.testclient import TestClient

client = TestClient(app)


def test_scenes_table():
    body = client.get("/api/testbench/ninja/scenes").json()
    assert [s["index"] for s in body["scenes"]] == list(range(10))
    assert body["scenes"][9]["name"] == "TV"
    assert all(s["photo_risk"] == "intense" for s in body["scenes"])


def test_lights_list_shape():
    body = client.get("/api/testbench/ninja/lights").json()
    assert isinstance(body["lights"], list)
    for light in body["lights"]:
        assert {"id", "name", "studio", "channel", "group"} <= set(light)


def test_set_needs_target():
    assert client.post("/api/testbench/ninja/set", json={"mock": True}).status_code == 422
    both = {"light": "home-studio/x", "channel": 3, "group": "A", "mock": True}
    assert client.post("/api/testbench/ninja/set", json=both).status_code == 422
    unknown = {"light": "home-studio/nope", "mock": True}
    assert client.post("/api/testbench/ninja/set", json=unknown).status_code == 404


def test_set_gentle_gate():
    hot = {"channel": 11, "group": "A", "brightness": 25, "mock": True}
    assert client.post("/api/testbench/ninja/set", json=hot).status_code == 422
    hot["allow_bright"] = True
    assert client.post("/api/testbench/ninja/set", json=hot).status_code == 200


def test_set_scene_gate_and_risk():
    base = {"channel": 12, "group": "A", "scene": 9, "brightness": 5, "mock": True}
    assert client.post("/api/testbench/ninja/set", json=base).status_code == 422
    base["allow_intense"] = True
    response = client.post("/api/testbench/ninja/set", json=base)
    assert response.status_code == 200
    assert response.json()["state"]["photo_risk"] == "intense"


def test_set_pristine_needs_brightness():
    body = {"channel": 17, "group": "A", "cct": 5500, "mock": True}
    response = client.post("/api/testbench/ninja/set", json=body)
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "brightness unknown" in response.text


def test_set_merges_across_calls():
    first = {"channel": 13, "group": "B", "brightness": 7, "mock": True}
    assert client.post("/api/testbench/ninja/set", json=first).status_code == 200
    second = {"channel": 13, "group": "B", "cct": 5500, "mock": True}
    body = client.post("/api/testbench/ninja/set", json=second).json()
    assert body["ok"] is True
    payload = bytes.fromhex(body["state"]["applied"][0]["state"]["sends"][0]["payload"])
    assert payload[10] == 7  # brightness carried over
    assert payload[9] == 55  # cct applied


def test_set_rejects_bad_values():
    base = {"channel": 14, "group": "A", "mock": True}
    assert client.post("/api/testbench/ninja/set", json={**base, "power": "maybe"}).status_code == 422
    assert client.post("/api/testbench/ninja/set", json={**base, "scene": 99}).status_code == 422
    assert client.post("/api/testbench/ninja/set", json={**base, "repeats": 9}).status_code == 422
    assert client.post("/api/testbench/ninja/set", json=base).status_code == 422  # nothing to set


def test_raw_validates():
    base = {"channel": 15, "group": "A", "mock": True}
    good = "1aff4c000215ef1900370703000a0000000002fe5c07000a006ec5"
    assert client.post("/api/testbench/ninja/raw", json={**base, "payload": good}).status_code == 200
    assert client.post("/api/testbench/ninja/raw", json={**base, "payload": "zz"}).status_code == 422
    assert client.post("/api/testbench/ninja/raw", json={**base, "payload": "00"}).status_code == 422


def test_state_and_probe_shapes():
    body = {"channel": 16, "group": "A", "mock": True}
    state = client.post("/api/testbench/ninja/state", json=body).json()
    assert state["ok"] is True
    assert state["state"]["backend"] == "mock"
    assert state["state"]["counter"] == 0
    probe = client.post("/api/testbench/ninja/probe", json=body).json()
    assert probe == {"ok": True, "state": {"backend": "mock", "transmits": False}, "error": None}


def test_fixed_cct_helper():
    assert _fixed_cct({"fixedTemp": True, "colorTempK": 5600}) == 5600
    assert _fixed_cct({"ble": True}) is None  # bi-color profile
    assert _fixed_cct({}) is None
    assert _fixed_cct(None) is None
    assert _fixed_cct("5600") is None
    assert _fixed_cct({"fixedTemp": True}) is None
    assert _fixed_cct({"fixedTemp": True, "colorTempK": 5550}) is None
    assert _fixed_cct({"fixedTemp": True, "colorTempK": "5600"}) is None
    assert _fixed_cct({"fixedTemp": True, "colorTempK": True}) is None
    assert _fixed_cct({"fixedTemp": False, "colorTempK": 5600}) is None


def test_state_via_light_seeds_fixed_cct():
    """Channel 18 is fresh, so the light path seeds 5600 K pre-send; a
    brightness-only send then broadcasts the seeded temp (byte 9 == 56)."""
    studio = {"id": "t", "name": "t"}
    devices = [
        {
            "id": "key",
            "name": "Key",
            "profile": "viltrox-ninja-10",
            "driverAddress": {"channel": 18, "group": "A"},
        }
    ]
    profiles = {
        "viltrox-ninja-10": {
            "spec": {"fixedTemp": True, "colorTempK": 5600},
        }
    }
    registries = app.state.registries
    saved = dict(registries)
    registries.clear()
    registries["t"] = Registry(studio, devices, [], [], {}, profiles)
    try:
        state = client.post(
            "/api/testbench/ninja/state", json={"light": "t/key", "mock": True}
        ).json()
        assert state["state"]["look"]["cct"] == 5600
        assert state["state"]["transmitted"] is False
        sent = client.post(
            "/api/testbench/ninja/set",
            json={"light": "t/key", "brightness": 7, "mock": True},
        ).json()
        payload = bytes.fromhex(sent["state"]["applied"][0]["state"]["sends"][0]["payload"])
        assert payload[9] == 56
    finally:
        registries.clear()
        registries.update(saved)
