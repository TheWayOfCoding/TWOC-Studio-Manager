# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the main-PC boot-and-OBS job (injected link, no audio)."""

import threading

import pytest

from app.modules.pcboot import new_job, run_boot


class FakeLink:
    def __init__(self, script, wol_note="3 magic packets sent"):
        self.script = list(script)
        self.calls = []
        self.wol_note = wol_note

    def send_wait(self, command, wait_s):
        self.calls.append((command, wait_s))
        if not self.script:
            return {"heard": None, "others": []}
        return {"heard": self.script.pop(0), "others": []}

    def wol(self):
        if isinstance(self.wol_note, Exception):
            raise self.wol_note
        return self.wol_note


def _ok_scene(scenes):
    def go(scene):
        scenes.append(scene)
        return True, ""

    return go


@pytest.mark.unit
def test_boot_happy_path_sets_scene():
    link = FakeLink([None, "ack", "obs_ready"])
    scenes = []
    job = run_boot(new_job("s"), link, _ok_scene(scenes), sleep=lambda s: None)
    assert job["state"] == "ready"
    assert job["scene"] == "Face Full View"
    assert scenes == ["Face Full View"]
    assert [call[0] for call in link.calls] == ["ping", "ping", "obs"]
    assert link.calls[0][1] == 8.0 and link.calls[2][1] == 60.0
    assert job["error"] is None


@pytest.mark.unit
def test_boot_ping_timeout_is_honest():
    link = FakeLink([None] * 5)
    job = run_boot(new_job("s"), link, _ok_scene([]), sleep=lambda s: None, cfg={"ping_attempts": 3})
    assert job["state"] == "timeout"
    assert "3 pings" in job["error"]
    assert len(link.calls) == 3


@pytest.mark.unit
def test_boot_stops_between_windows():
    link = FakeLink(["ack", "obs_ready"])
    stop = threading.Event()
    stop.set()
    job = run_boot(new_job("s"), link, _ok_scene([]), sleep=lambda s: None, stop=stop)
    assert job["state"] == "stopped"
    assert link.calls == []


@pytest.mark.unit
def test_boot_retries_failed_launches():
    link = FakeLink(["ack", "obs_fail", "obs_ready"])
    job = run_boot(new_job("s"), link, _ok_scene([]), sleep=lambda s: None)
    assert job["state"] == "ready"
    assert [call[0] for call in link.calls] == ["ping", "obs", "obs"]


@pytest.mark.unit
def test_boot_obs_never_ready_fails():
    link = FakeLink(["ack", None, None, None])
    job = run_boot(
        new_job("s"), link, _ok_scene([]), sleep=lambda s: None, cfg={"obs_attempts": 3, "obs_window": 1}
    )
    assert job["state"] == "obs_failed"
    assert "3 launches" in job["error"]


@pytest.mark.unit
def test_boot_scene_failure_keeps_obs_up():
    link = FakeLink(["ack", "obs_ready"])
    job = run_boot(new_job("s"), link, lambda scene: (False, "nope"), sleep=lambda s: None)
    assert job["state"] == "scene_failed"
    assert "Face Full View" in job["error"]


@pytest.mark.unit
def test_boot_empty_scene_skips_cut():
    link = FakeLink(["ack", "obs_ready"])
    scenes = []
    job = new_job("s")
    job["scene"] = ""
    job = run_boot(job, link, _ok_scene(scenes), sleep=lambda s: None)
    assert job["state"] == "ready"
    assert scenes == []


@pytest.mark.unit
def test_boot_collection_cut_runs_before_scene():
    order = []
    link = FakeLink(["ack", "obs_ready"])
    job = run_boot(
        new_job("s", collection="ScottyPhoton"),
        link,
        lambda scene: (order.append(("scene", scene)) or (True, "")),
        sleep=lambda s: None,
        collect_set=lambda name: (order.append(("collection", name)) or (True, "")),
    )
    assert job["state"] == "ready"
    assert order == [("collection", "ScottyPhoton"), ("scene", "Face Full View")]


@pytest.mark.unit
def test_boot_collection_failure_reports():
    link = FakeLink(["ack", "obs_ready"])
    job = run_boot(
        new_job("s", collection="Nope"),
        link,
        _ok_scene([]),
        sleep=lambda s: None,
        collect_set=lambda name: (False, "no collection Nope"),
    )
    assert job["state"] == "collection_failed"
    assert "Nope" in job["error"]


@pytest.mark.unit
def test_boot_link_failure_fails_fast():
    class Boom(FakeLink):
        def send_wait(self, command, wait_s):
            raise RuntimeError("no audio backend")

    job = run_boot(new_job("s"), Boom([]), _ok_scene([]), sleep=lambda s: None)
    assert job["state"] == "link_error"
    assert "no audio backend" in job["error"]


@pytest.mark.unit
def test_boot_wol_failure_does_not_abort():
    link = FakeLink(["ack", "obs_ready"], wol_note=ValueError("bad mac"))
    job = run_boot(new_job("s"), link, _ok_scene([]), sleep=lambda s: None)
    assert job["state"] == "ready"
    assert job["phases"][0]["wol"].startswith("wol skipped")


@pytest.mark.unit
def test_boot_obs_command_targets_session():
    """The launch phase plays the job's obs_command (per-session addressing)."""
    assert new_job("s")["obs_command"] == "obs"
    link = FakeLink(["ack", "obs_ready"])
    job = run_boot(new_job("s", obs_command="obs_bench"), link, _ok_scene([]), sleep=lambda s: None)
    assert job["state"] == "ready"
    assert job["obs_command"] == "obs_bench"
    assert [call[0] for call in link.calls] == ["ping", "obs_bench"]
    launching = [phase for phase in job["phases"] if phase["phase"] == "launching"]
    assert launching and all(phase["command"] == "obs_bench" for phase in launching)
