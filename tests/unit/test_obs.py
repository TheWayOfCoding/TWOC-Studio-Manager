# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the OBS scaffold (injected fake client, no sockets)."""

import pytest

from app.core.safety import SafetyViolation
from app.drivers.base import DriverResult
from app.drivers.obs_ws import ObsWsDriver
from app.modules.obsshow import ObsModuleError, ObsShowModule


class FakeObsClient:
    """Scripted OBS WebSocket v5 peer."""

    def __init__(self, scenes=("Main", "BRB"), fail_on=()):
        self.scenes = list(scenes)
        self.current = self.scenes[0] if self.scenes else None
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
def test_driver_reports_connection_failure():
    driver = ObsWsDriver("obs", "obs-pc", client=FakeObsClient(fail_on=("GetSceneList",)))
    result = ObsShowModule("obs", {"ws": driver}).list_scenes()
    assert result.ok is False
    assert "boom" in result.error


@pytest.mark.unit
def test_stream_needs_confirmation(module):
    with pytest.raises(SafetyViolation, match="unconfirmed stream start"):
        module.set_streaming("start")
    with pytest.raises(SafetyViolation, match="unconfirmed stream stop"):
        module.set_streaming("stop")
    assert module.set_streaming("start", confirmed=True).ok is False  # scaffold: unimplemented


@pytest.mark.unit
def test_state_offline_without_client():
    result = ObsWsDriver("obs", "obs-pc").get_state()
    assert result.ok is True
    assert result.state["connected"] is False


@pytest.mark.unit
def test_safe_stop_disconnects(module):
    assert module.drivers["ws"].safe_stop().state == {"connected": False}
