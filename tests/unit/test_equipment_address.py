# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for editable driver addresses (BLE/DMX connection values)."""

import json
import shutil
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config"
EQUIPMENT_HTML = CONFIG_ROOT.parent / "backend" / "app" / "static" / "equipment.html"

ADDR_DEVICES = [
    {"id": "addr-ninja", "name": "Addr Ninja", "type": "light", "zone": "bench",
     "profile": "viltrox-ninja-400", "driver": "ninja",
     "driverAddress": {"channel": 3, "group": "F"}},
    {"id": "addr-dual", "name": "Addr Dual", "type": "light", "zone": "bench",
     "profile": "viltrox-ninja-400", "driver": "ninja",
     "driverAddress": {"channel": 3, "group": "E", "dmx": {"dimmer": 40, "cct": 41}},
     "transport": "dmx"},
    {"id": "addr-gvm", "name": "Addr GVM", "type": "light", "zone": "bench",
     "profile": "gvm-1500d", "driver": "gvm",
     "driverAddress": {"ble": "AA:BB:CC:DD:EE:99"}},
]


@contextmanager
def _tmp_addr_studio(tmp_path):
    """Tmp local studio plus BLE-only, dual, and GVM fixtures."""
    from app.config.loader import check_references, load_all, load_profiles, load_studio
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
    devices.extend(ADDR_DEVICES)
    devices_path.write_text(json.dumps(devices), encoding="utf-8")

    data = load_all(tmp_path)
    saved = (app.state.config_root, dict(app.state.registries), list(app.state.studio_list))
    app.state.config_root = tmp_path
    app.state.registries = {"x": Registry.from_loaded(data["studios"]["x"], data["profiles"])}
    app.state.studio_list = [{"id": "x", "label": "X", "source": "studios.local"}]
    try:
        loaded = load_studio(tmp_path, "x")
        check_references(loaded, load_profiles(tmp_path), "x")
        yield studio_dir
    finally:
        app.state.config_root, registries, studio_list = saved
        app.state.registries.clear()
        app.state.registries.update(registries)
        app.state.studio_list = studio_list


@pytest.mark.unit
def test_put_address_adds_dmx_half(tmp_path):
    """A BLE-only Ninja gains a DMX map; ranges offer the interface after."""
    client = TestClient(app)
    with _tmp_addr_studio(tmp_path) as studio_dir:
        saved = client.put("/api/studios/x/devices/addr-ninja/address",
                           json={"driverAddress": {"channel": 3, "group": "F", "dmx": {"dimmer": 20}}})
        assert saved.status_code == 200
        body = saved.json()
        assert body["address"] == {"channel": 3, "group": "F", "dmx": {"dimmer": 20}}
        assert body["transport_coerced"] is False
        on_disk = json.loads((studio_dir / "devices.json").read_text(encoding="utf-8"))
        kept = [item for item in on_disk if item["id"] == "addr-ninja"][0]
        assert kept["driverAddress"]["dmx"] == {"dimmer": 20}
        ranges = client.get("/api/studios/x/ranges").json()["devices"]
        assert ranges["addr-ninja"]["values"]["transport"] == {"enum": ["ble", "dmx"]}


@pytest.mark.unit
def test_put_address_dropping_half_coerces_transport(tmp_path):
    """Removing the DMX half while on dmx moves the pick to ble (reported)."""
    client = TestClient(app)
    with _tmp_addr_studio(tmp_path):
        saved = client.put("/api/studios/x/devices/addr-dual/address",
                           json={"driverAddress": {"channel": 3, "group": "E"}})
        assert saved.status_code == 200
        assert saved.json()["transport_coerced"] is True
        assert saved.json()["transport"] == "ble"
        got = client.get("/api/studios/x/devices/addr-dual").json()["device"]
        assert got["transport"] == "ble"
        assert "dmx" not in got["driverAddress"]
        ranges = client.get("/api/studios/x/ranges").json()["devices"]
        assert "transport" not in ranges["addr-dual"]["values"]


@pytest.mark.unit
def test_put_address_rejects_bad_shapes(tmp_path):
    """Per-family shapes: BLE pair, DMX ints, MACs, no stray keys."""
    client = TestClient(app)
    with _tmp_addr_studio(tmp_path):
        base = "/api/studios/x/devices/addr-ninja/address"
        assert client.put(base, json={"driverAddress": {"channel": 0, "group": "F"}}).status_code == 422
        assert client.put(base, json={"driverAddress": {"channel": 20, "group": "F"}}).status_code == 422
        assert client.put(base, json={"driverAddress": {"channel": 3, "group": "G"}}).status_code == 422
        assert client.put(base, json={"driverAddress": {"channel": 3, "group": "AB"}}).status_code == 422
        assert client.put(base, json={"driverAddress": {"channel": 3}}).status_code == 422
        assert client.put(base, json={"driverAddress": {"group": "F"}}).status_code == 422
        assert client.put(base, json={"driverAddress": {}}).status_code == 422
        assert client.put(base, json={"driverAddress": {"channel": 3, "group": "F", "ble": "AA:BB:CC:DD:EE:99"}}).status_code == 422
        dmx = "/api/studios/x/devices/addr-dual/address"
        assert client.put(dmx, json={"driverAddress": {"dmx": {"dimmer": 0}}}).status_code == 422
        assert client.put(dmx, json={"driverAddress": {"dmx": {"dimmer": 513}}}).status_code == 422
        assert client.put(dmx, json={"driverAddress": {"dmx": {"cct": 41}}}).status_code == 422
        assert client.put(dmx, json={"driverAddress": {"dmx": {"dimmer": 40, "cct": 999}}}).status_code == 422
        assert client.put(dmx, json={"driverAddress": {"dmx": {"dimmer": 40, "foo": 1}}}).status_code == 422
        gvm = "/api/studios/x/devices/addr-gvm/address"
        assert client.put(gvm, json={"driverAddress": {"ble": "not-a-mac"}}).status_code == 422
        assert client.put(gvm, json={"driverAddress": {"channel": 3, "group": "A"}}).status_code == 422
        assert client.put(gvm, json={"driverAddress": {}}).status_code == 422
        assert client.put("/api/studios/x/devices/demo-key/address",
                          json={"driverAddress": {"channel": 1, "group": "A"}}).status_code == 422
        assert client.put("/api/studios/x/devices/demo-screen/address",
                          json={"driverAddress": {}}).status_code == 422
        assert client.put("/api/studios/x/devices/nope/address",
                          json={"driverAddress": {}}).status_code == 404


@pytest.mark.unit
def test_put_address_normalizes_group_and_mac(tmp_path):
    """Lowercase groups/MACs save canonical (upper) without complaint."""
    client = TestClient(app)
    with _tmp_addr_studio(tmp_path):
        saved = client.put("/api/studios/x/devices/addr-ninja/address",
                           json={"driverAddress": {"channel": 3, "group": "f"}})
        assert saved.json()["address"]["group"] == "F"
        mac = client.put("/api/studios/x/devices/addr-gvm/address",
                         json={"driverAddress": {"ble": "aa:bb:cc:dd:ee:99"}})
        assert mac.json()["address"] == {"ble": "AA:BB:CC:DD:EE:99"}


@pytest.mark.unit
def test_put_address_refuses_tracked_studios():
    """Address saves are local-only, like every other equipment write."""
    client = TestClient(app)
    response = client.put("/api/studios/example-studio/devices/demo-key/address",
                          json={"driverAddress": {"channel": 3, "group": "A"}})
    assert response.status_code == 422
    assert "local" in response.json()["detail"]


@pytest.mark.unit
def test_equipment_address_editor_present():
    """Device card edits BLE/DMX halves with driver-linked hints."""
    html = EQUIPMENT_HTML.read_text(encoding="utf-8")
    for marker in (
        "Save address",
        "eqSaveAddr()",
        "eq-addr-channel",
        "eq-addr-group",
        "eq-addr-dimmer",
        "eq-addr-cct",
        "eq-addr-ble",
        "dsRefreshRanges()",
        "/devices/",
        "/address",
    ):
        assert marker in html
