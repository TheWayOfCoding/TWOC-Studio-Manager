# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the OBS scaffold (injected fake client, no sockets)."""

import pytest

from app.core.safety import SafetyViolation
from app.drivers.base import DriverResult
from app.drivers.obs_ws import ObsWsDriver
from app.modules.obsshow import ObsModuleError, ObsShowModule


class FakeObsClient:
    """Scripted OBS WebSocket v5 peer."""

    def __init__(self, scenes=("Main", "BRB"), fail_on=(), collections=("Untitled", "ScottyPhoton")):
        self.scenes = list(scenes)
        self.current = self.scenes[0] if self.scenes else None
        self.collections = list(collections)
        self.current_collection = self.collections[0] if self.collections else None
        self.streaming = False
        self.calls = []
        self.fail_on = set(fail_on)
        self.closed = False

    def call(self, request, data):
        self.calls.append((request, dict(data)))
        if request in self.fail_on:
            raise ConnectionError(f"{request} boom")
        if request == "GetVersion":
            return {"obsVersion": "30.0-fake"}
        if request == "GetSceneList":
            return {"scenes": [{"sceneName": name} for name in self.scenes], "currentProgramSceneName": self.current}
        if request == "SetCurrentProgramScene":
            name = data["sceneName"]
            if name not in self.scenes:
                raise KeyError(f"no scene {name}")
            self.current = name
            return {}
        if request == "GetSceneCollectionList":
            return {
                "sceneCollections": list(self.collections),
                "currentSceneCollectionName": self.current_collection,
            }
        if request == "SetCurrentSceneCollection":
            name = data["sceneCollectionName"]
            if name not in self.collections:
                raise KeyError(f"no collection {name}")
            self.current_collection = name
            return {}
        if request == "StartStream":
            self.streaming = True
            return {}
        if request == "StopStream":
            self.streaming = False
            return {}
        if request == "GetStreamStatus":
            return {"outputActive": self.streaming}
        raise KeyError(f"unexpected {request}")

    def close(self):
        self.closed = True


@pytest.fixture()
def module():
    driver = ObsWsDriver("obs", "obs-pc", client=FakeObsClient())
    return ObsShowModule("obs", {"ws": driver})


@pytest.mark.unit
def test_module_needs_ws_driver():
    with pytest.raises(ObsModuleError, match="ws"):
        ObsShowModule("obs", {})


@pytest.mark.unit
def test_list_and_switch(module):
    scenes = module.list_scenes()
    assert scenes.ok is True
    assert [s["sceneName"] for s in scenes.state["scenes"]] == ["Main", "BRB"]
    switched = module.switch_scene("BRB")
    assert switched.ok is True
    assert switched.state == {"scene": "BRB"}
    assert module.list_scenes().state["currentProgramSceneName"] == "BRB"


@pytest.mark.unit
def test_switch_rejects_empty_name(module):
    with pytest.raises(ObsModuleError, match="scene name"):
        module.switch_scene("")


@pytest.mark.unit
def test_list_and_set_collection(module):
    listed = module.list_collections()
    assert listed.ok is True
    assert listed.state["sceneCollections"] == ["Untitled", "ScottyPhoton"]
    assert listed.state["currentSceneCollectionName"] == "Untitled"
    switched = module.set_collection("ScottyPhoton")
    assert switched.ok is True
    assert switched.state == {"collection": "ScottyPhoton"}
    assert module.list_collections().state["currentSceneCollectionName"] == "ScottyPhoton"
    with pytest.raises(ObsModuleError, match="collection name"):
        module.set_collection("")


@pytest.mark.unit
def test_driver_reports_connection_failure():
    driver = ObsWsDriver("obs", "obs-pc", client=FakeObsClient(fail_on=("GetSceneList",)))
    result = ObsShowModule("obs", {"ws": driver}).list_scenes()
    assert result.ok is False
    assert "boom" in result.error


@pytest.mark.unit
def test_connect_retries_then_succeeds(monkeypatch):
    import app.drivers.obs_ws as obs_ws

    attempts, sleeps = [], []

    def flaky(host, port, password, timeout=10):
        attempts.append(1)
        if len(attempts) < 3:
            raise ConnectionError("refused")
        return FakeObsClient()

    monkeypatch.setattr(obs_ws, "_default_client", flaky)
    driver = ObsWsDriver("obs", "obs-pc", connect_attempts=4, connect_delay_s=2.0, sleep=sleeps.append)
    result = driver.apply({"action": "switch", "scene": "BRB"})
    assert result.ok
    assert result.state == {"scene": "BRB"}
    assert len(attempts) == 3
    assert sleeps == [2.0, 2.0]


@pytest.mark.unit
def test_connect_exhaustion_reports_last_error(monkeypatch):
    import app.drivers.obs_ws as obs_ws

    def down(host, port, password, timeout=10):
        raise ConnectionError("down")

    sleeps = []
    monkeypatch.setattr(obs_ws, "_default_client", down)
    driver = ObsWsDriver("obs", "obs-pc", connect_attempts=3, sleep=sleeps.append)
    result = driver.apply({"action": "switch", "scene": "BRB"})
    assert result.ok is False
    assert "down" in result.error
    assert sleeps == [2.0, 2.0]


@pytest.mark.unit
def test_connect_default_single_attempt(monkeypatch):
    import app.drivers.obs_ws as obs_ws

    attempts, sleeps = [], []
    monkeypatch.setattr(obs_ws, "_default_client", lambda *a, **k: attempts.append(1) or FakeObsClient())
    driver = ObsWsDriver("obs", "obs-pc", sleep=sleeps.append)
    assert driver.apply({"action": "switch", "scene": "BRB"}).ok
    assert len(attempts) == 1
    assert sleeps == []


@pytest.mark.unit
def test_stream_needs_confirmation(module):
    with pytest.raises(SafetyViolation, match="unconfirmed stream start"):
        module.set_streaming("start")
    with pytest.raises(SafetyViolation, match="unconfirmed stream stop"):
        module.set_streaming("stop")
    assert module.set_streaming("start", confirmed=True).ok is True
    assert module.stream_status().state == {"outputActive": True}
    assert module.set_streaming("stop", confirmed=True).ok is True
    assert module.stream_status().state == {"outputActive": False}


@pytest.mark.unit
def test_switch_falls_back_to_safe_scene(module):
    switched = module.switch_scene("Nope", safe_scene="BRB")
    assert switched.ok is True
    assert switched.state == {"scene": "BRB", "fallback": True, "requested": "Nope"}
    assert module.list_scenes().state["currentProgramSceneName"] == "BRB"


@pytest.mark.unit
def test_switch_safe_fallback_can_fail(module):
    failed = module.switch_scene("Nope", safe_scene="AlsoNope")
    assert failed.ok is False
    assert "Nope" in failed.error


@pytest.mark.unit
def test_state_offline_without_client():
    result = ObsWsDriver("obs", "obs-pc").get_state()
    assert result.ok is True
    assert result.state["connected"] is False


@pytest.mark.unit
def test_safe_stop_disconnects(module):
    assert module.drivers["ws"].safe_stop().state == {"connected": False}


class FakeSocket:
    """Scripted websocket-client connection: queued inbound frames, recorded sends."""

    def __init__(self, inbound):
        import json as _json

        self._json = _json
        self._inbound = [self._json.dumps(frame) for frame in inbound]
        self.sent = []
        self.closed = False

    def send(self, payload):
        self.sent.append(self._json.loads(payload))

    def recv(self):
        return self._inbound.pop(0)

    def close(self):
        self.closed = True


class FakeWebsocketModule:
    def __init__(self, sock):
        self.sock = sock
        self.urls = []

    def create_connection(self, url, timeout=None):
        self.urls.append((url, timeout))
        return self.sock


def _patch_websocket(monkeypatch, sock):
    import sys

    module = FakeWebsocketModule(sock)
    monkeypatch.setitem(sys.modules, "websocket", module)
    return module


def _hello(auth=None):
    data = {"obsWebSocketVersion": "5.5", "rpcVersion": 1}
    if auth is not None:
        data["authentication"] = auth
    return {"op": 0, "d": data}


@pytest.mark.unit
def test_live_handshake_without_auth_and_event_skip(monkeypatch):
    """Hello -> Identify -> Identified; stray events are skipped while awaiting."""
    from app.drivers import obs_ws

    sock = FakeSocket(
        [
            _hello(),
            {"op": 2, "d": {"negotiatedRpcVersion": 1}},
            {"op": 5, "d": {"eventType": "CurrentProgramSceneChanged", "eventData": {}}},
            {
                "op": 7,
                "d": {
                    "requestType": "GetVersion",
                    "requestId": "1",
                    "requestStatus": {"result": True, "code": 100},
                    "responseData": {"obsVersion": "31.0-live"},
                },
            },
        ]
    )
    wsmod = _patch_websocket(monkeypatch, sock)
    client = obs_ws._default_client("obs-pc", 4455, None)
    assert wsmod.urls == [("ws://obs-pc:4455", 10)]
    assert sock.sent[0] == {"op": 1, "d": {"rpcVersion": 1, "eventSubscriptions": 0}}
    assert client.call("GetVersion", {}) == {"obsVersion": "31.0-live"}
    assert sock.sent[1]["op"] == 6
    assert sock.sent[1]["d"]["requestType"] == "GetVersion"
    client.close()
    assert sock.closed is True


@pytest.mark.unit
def test_live_handshake_with_auth_matches_v5_algorithm(monkeypatch):
    """Password auth uses the v5 secret: b64(sha256(b64(sha256(pw+salt))+challenge))."""
    import base64
    import hashlib

    from app.drivers import obs_ws

    salt, challenge = "salty1234567890ab", "challenger1234567890abcdef"
    sock = FakeSocket(
        [
            _hello({"challenge": challenge, "salt": salt}),
            {"op": 2, "d": {"negotiatedRpcVersion": 1}},
            {
                "op": 7,
                "d": {
                    "requestType": "GetSceneList",
                    "requestId": "1",
                    "requestStatus": {"result": True, "code": 100},
                    "responseData": {"scenes": []},
                },
            },
        ]
    )
    _patch_websocket(monkeypatch, sock)
    client = obs_ws._default_client("obs-pc", 4455, "hunter2")
    secret = base64.b64encode(hashlib.sha256(("hunter2" + salt).encode()).digest()).decode()
    expected = base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()
    assert sock.sent[0]["d"]["authentication"] == expected
    assert client.call("GetSceneList", {}) == {"scenes": []}


@pytest.mark.unit
def test_live_handshake_missing_password_and_failed_request(monkeypatch):
    from app.drivers import obs_ws

    sock = FakeSocket([_hello({"challenge": "c", "salt": "s"})])
    _patch_websocket(monkeypatch, sock)
    with pytest.raises(obs_ws.ObsError, match="requires a password"):
        obs_ws._default_client("obs-pc", 4455, None)

    sock = FakeSocket(
        [
            _hello(),
            {"op": 2, "d": {"negotiatedRpcVersion": 1}},
            {
                "op": 7,
                "d": {
                    "requestType": "SetCurrentProgramScene",
                    "requestId": "1",
                    "requestStatus": {"result": False, "code": 600, "comment": "No scene"},
                },
            },
        ]
    )
    _patch_websocket(monkeypatch, sock)
    client = obs_ws._default_client("obs-pc", 4455, None)
    with pytest.raises(obs_ws.ObsError, match="No scene"):
        client.call("SetCurrentProgramScene", {"sceneName": "Missing"})
