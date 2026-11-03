# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the PC-link API and Wake-on-LAN sender."""

import pytest
from fastapi.testclient import TestClient

from app.drivers.wol import WolError, magic_packet, parse_mac
from app.main import app

client = TestClient(app)


@pytest.mark.unit
def test_parse_mac_accepts_separators():
    assert parse_mac("AA:BB:CC:DD:EE:FF") == bytes.fromhex("aabbccddeeff")
    assert parse_mac("AA-BB-CC-DD-EE-FF") == bytes.fromhex("aabbccddeeff")
    assert parse_mac("aabbccddeeff") == bytes.fromhex("aabbccddeeff")
    for bad in ("", "AA:BB:CC", "ZZ:BB:CC:DD:EE:FF", None, 123):
        with pytest.raises(WolError):
            parse_mac(bad)


@pytest.mark.unit
def test_magic_packet_shape():
    payload = magic_packet(bytes.fromhex("010203040506"))
    assert len(payload) == 102  # 6 sync + 16 x 6
    assert payload[:6] == b"\xff" * 6
    assert payload[6:12] == bytes.fromhex("010203040506")
    with pytest.raises(WolError):
        magic_packet(b"short")


@pytest.mark.unit
def test_wake_broadcasts_without_echoing_mac(monkeypatch):
    from app.drivers import wol as wol_mod

    sent = []

    class FakeSocket:
        def setsockopt(self, *args):
            pass

        def sendto(self, payload, addr):
            sent.append((payload, addr))

        def close(self):
            pass

    monkeypatch.setattr(wol_mod.socket, "socket", lambda *a: FakeSocket())
    assert wol_mod.wake("AA:BB:CC:DD:EE:FF") == 3
    assert len(sent) == 3
    assert all(addr == ("255.255.255.255", 9) for _, addr in sent)
    assert sent[0][0][:6] == b"\xff" * 6


@pytest.mark.unit
def test_link_devices_reports_backend(monkeypatch):
    from app.api import link as link_api
    from app.drivers.base import DriverResult

    monkeypatch.setattr(
        link_api, "device_state", lambda: DriverResult(ok=True, state={"devices": [{"index": 1}]})
    )
    body = client.get("/api/link/devices").json()
    assert body["ok"] is True
    assert body["state"] == {"devices": [{"index": 1}]}


@pytest.mark.unit
def test_link_send_validates_and_plays(monkeypatch):
    from app.api import link as link_api

    seen = {}
    monkeypatch.setattr(link_api, "_link_config", lambda root: {})
    monkeypatch.setattr(
        link_api, "send_tones", lambda frame, device=None: seen.update(frame=frame, device=device) or {"digits": frame, "seconds": 0.8}
    )
    bad = client.post("/api/link/send", json={"command": "nope"})
    assert bad.status_code == 422
    assert "unknown link command" in bad.json()["detail"]
    ok = client.post("/api/link/send", json={"command": "ping"})
    assert ok.status_code == 200
    assert ok.json()["state"]["command"] == "ping"
    assert seen["frame"].startswith("#")


@pytest.mark.unit
def test_link_inputs_reports_backend(monkeypatch):
    from app.api import link as link_api
    from app.drivers.base import DriverResult

    monkeypatch.setattr(
        link_api, "input_state", lambda: DriverResult(ok=True, state={"devices": [{"index": 2}]})
    )
    body = client.get("/api/link/inputs").json()
    assert body["ok"] is True
    assert body["state"] == {"devices": [{"index": 2}]}


@pytest.mark.unit
def test_link_listen_decodes_or_honest_empty(monkeypatch):
    from app.api import link as link_api
    from app.drivers.link_audio import LinkError

    seen = {}
    monkeypatch.setattr(link_api, "_link_config", lambda root: {"audio_in": "Mic"})
    monkeypatch.setattr(
        link_api,
        "listen_once",
        lambda seconds, device=None: seen.update(seconds=seconds, device=device)
        or {"command": "ack", "digits": "02", "seconds": 2.0},
    )
    heard = client.post("/api/link/listen", json={"seconds": 2.0})
    assert heard.status_code == 200
    assert heard.json()["state"]["command"] == "ack"
    assert seen == {"seconds": 2.0, "device": "Mic"}

    monkeypatch.setattr(
        link_api, "listen_once", lambda seconds, device=None: {"command": None, "digits": None, "seconds": 2.0}
    )
    empty = client.post("/api/link/listen", json={})
    assert empty.status_code == 200
    assert empty.json()["state"]["command"] is None

    def _boom(seconds, device=None):
        raise LinkError("no audio backend: pip install sounddevice on the hub")

    monkeypatch.setattr(link_api, "listen_once", _boom)
    assert client.post("/api/link/listen", json={}).status_code == 422
    assert client.post("/api/link/listen", json={"seconds": 99}).status_code == 422


@pytest.mark.unit
def test_link_send_and_wait_confirms_expected_ack(monkeypatch):
    from app.api import link as link_api

    seen = {}
    monkeypatch.setattr(link_api, "_link_config", lambda root: {"audio_out": "Out", "audio_in": "In"})
    monkeypatch.setattr(
        link_api, "send_tones", lambda frame, device=None: seen.update(tx=frame, device=device) or {"digits": frame}
    )
    monkeypatch.setattr(
        link_api,
        "listen_all",
        lambda seconds, device=None: seen.update(wait=seconds, rx=device)
        or {"commands": ["ack", "obs_ready"], "seconds": 60.0},
    )
    body = client.post("/api/link/send-and-wait", json={"command": "obs"}).json()
    assert body["ok"] is True
    state = body["state"]
    assert state["confirmed"] is True
    assert state["heard"] == "obs_ready"
    assert state["others"] == ["ack"]
    assert state["expected"] == ["obs_ready", "obs_fail"]
    assert seen["tx"].startswith("#") and seen["device"] == "Out"
    assert seen["wait"] == 60.0 and seen["rx"] == "In"


@pytest.mark.unit
def test_link_send_and_wait_timeout_and_listen_error(monkeypatch):
    from app.api import link as link_api
    from app.drivers.link_audio import LinkError

    monkeypatch.setattr(link_api, "_link_config", lambda root: {})
    monkeypatch.setattr(link_api, "send_tones", lambda frame, device=None: {"digits": frame})
    monkeypatch.setattr(link_api, "listen_all", lambda seconds, device=None: {"commands": [], "seconds": 8.0})
    quiet = client.post("/api/link/send-and-wait", json={"command": "ping", "wait_s": 8}).json()
    assert quiet["state"]["confirmed"] is False
    assert quiet["state"]["heard"] is None

    def _boom(seconds, device=None):
        raise LinkError("no audio backend: pip install sounddevice on the hub")

    monkeypatch.setattr(link_api, "listen_all", _boom)
    deaf = client.post("/api/link/send-and-wait", json={"command": "ping"}).json()
    assert deaf["ok"] is True and deaf["state"]["confirmed"] is False
    assert "no audio backend" in deaf["state"]["listen_error"]

    assert client.post("/api/link/send-and-wait", json={"command": "ack"}).status_code == 422
    assert client.post("/api/link/send-and-wait", json={"command": "nope"}).status_code == 422
    assert client.post("/api/link/send-and-wait", json={"command": "ping", "wait_s": 1}).status_code == 422
    assert client.post("/api/link/send-and-wait", json={"command": "ping", "wait_s": 999}).status_code == 422


@pytest.mark.unit
def test_link_config_get_redacts_mac(monkeypatch):
    from app.api import link as link_api

    monkeypatch.setattr(
        link_api, "_link_config", lambda root: {"audio_out": "Out", "audio_in": "In", "wol_mac": "AA:BB:CC:DD:EE:FF"}
    )
    response = client.get("/api/link/config")
    assert response.status_code == 200
    assert response.json() == {"audio_out": "Out", "audio_in": "In", "wol_configured": True}
    assert "AA:BB:CC:DD:EE:FF" not in response.text


@pytest.mark.unit
def test_link_config_set_validates_and_persists(monkeypatch, tmp_path):
    import json

    from app.api.link import LinkConfigBody, _save_link_config
    from app.drivers import link_audio

    monkeypatch.setattr(
        link_audio, "output_devices", lambda: [{"index": 3, "name": "Realtek Out", "channels": 2}]
    )
    monkeypatch.setattr(
        link_audio, "input_devices", lambda: [{"index": 5, "name": "Realtek Mic", "channels": 1}]
    )
    root = tmp_path / "cfg"
    (root / "studios.local").mkdir(parents=True)
    (root / "studios.local" / "link.json").write_text(json.dumps({"wol_mac": "AA:BB:CC:DD:EE:FF"}), encoding="utf-8")

    saved = _save_link_config(str(root), LinkConfigBody(audio_out="realtek out", audio_in=5))
    assert saved == {"audio_out": "realtek out", "audio_in": 5, "verified": {"audio_out": True, "audio_in": True}}
    stored = json.loads((root / "studios.local" / "link.json").read_text(encoding="utf-8"))
    assert stored == {"wol_mac": "AA:BB:CC:DD:EE:FF", "audio_out": "realtek out", "audio_in": 5}

    partial = _save_link_config(str(root), LinkConfigBody(audio_out=None))
    assert partial["audio_out"] is None and partial["audio_in"] == 5

    from app.drivers.link_audio import LinkError

    with pytest.raises(LinkError, match="unknown audio out"):
        _save_link_config(str(root), LinkConfigBody(audio_out="nope"))
    with pytest.raises(LinkError, match="unknown audio in"):
        _save_link_config(str(root), LinkConfigBody(audio_in=99))


@pytest.mark.unit
def test_link_config_set_maps_errors(monkeypatch):
    from app.api import link as link_api
    from app.drivers.link_audio import LinkError

    monkeypatch.setattr(link_api, "_save_link_config", lambda root, body: {"audio_out": None, "audio_in": None})
    assert client.post("/api/link/config", json={}).status_code == 200

    def _boom(root, body):
        raise LinkError("unknown audio out")

    monkeypatch.setattr(link_api, "_save_link_config", _boom)
    bad = client.post("/api/link/config", json={"audio_out": "nope"})
    assert bad.status_code == 422
    assert "unknown audio out" in bad.json()["detail"]


@pytest.mark.unit
def test_link_wol_needs_config_then_sends(monkeypatch):
    from app.api import link as link_api

    monkeypatch.setattr(link_api, "_link_config", lambda root: {})
    assert client.post("/api/link/wol").status_code == 422
    monkeypatch.setattr(link_api, "_link_config", lambda root: {"wol_mac": "AA:BB:CC:DD:EE:FF"})
    monkeypatch.setattr(link_api, "wake", lambda mac: 3)
    body = client.post("/api/link/wol").json()
    assert body == {"ok": True, "state": {"packets": 3}}
