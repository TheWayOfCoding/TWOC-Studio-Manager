# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for glass kiosk auto-launch (mocked processes, both platforms)."""

import pytest

from app.modules import kiosk

SHOW = {
    "steps": [
        {"screen": {"b": {}, "a": {}}},
        {"screen": {"a": {}}},
        {"screen": {}},
    ]
}


class Proc:
    """Minimal CompletedProcess stand-in for mocked subprocess.run."""

    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.fixture()
def windows(monkeypatch):
    monkeypatch.setattr(kiosk, "_on_windows", lambda: True)
    monkeypatch.setattr(kiosk.os.path, "isfile", lambda path: True)


@pytest.fixture()
def posix(monkeypatch):
    monkeypatch.setattr(kiosk, "_on_windows", lambda: False)


def test_screens_in_show_ordered_unique():
    assert kiosk.screens_in_show(SHOW) == ["b", "a"]


def test_screens_in_show_empty():
    assert kiosk.screens_in_show({"steps": []}) == []


def test_kiosk_url_is_hub_local():
    assert kiosk.kiosk_url("kqc-screen") == "http://localhost:8000/screen/kqc-screen"


def test_ensure_for_show_no_screens_skips_quietly():
    assert kiosk.ensure_kiosk_for_show({"steps": []}) == "no-screens"


def test_ensure_for_show_uses_first_screen(monkeypatch):
    seen = []
    monkeypatch.setattr(kiosk, "ensure_kiosk", lambda url: seen.append(url) or "launched")
    assert kiosk.ensure_kiosk_for_show(SHOW) == "launched"
    assert seen == ["http://localhost:8000/screen/b"]


def test_windows_already_running_skips_launch(monkeypatch, windows):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return Proc(stdout='"studio-kiosk.exe","1234","Console","1","9,999 K"\n')

    monkeypatch.setattr(kiosk.subprocess, "run", fake_run)
    assert kiosk.ensure_kiosk("http://localhost:8000/screen/b") == "already-running"
    assert len(calls) == 1 and calls[0][0] == "tasklist"


def test_windows_launches_through_interactive_task(monkeypatch, windows):
    calls = []
    outputs = [Proc(stdout="INFO: No tasks are running\n"), Proc(), Proc()]

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return outputs.pop(0)

    monkeypatch.setattr(kiosk.subprocess, "run", fake_run)
    url = "http://localhost:8000/screen/b"
    assert kiosk.ensure_kiosk(url) == "launched"
    create, run = calls[1], calls[2]
    assert create[:4] == ["schtasks", "/create", "/tn", "StudioKiosk"]
    assert "/it" in create and url in create[create.index("/tr") + 1]
    assert run == ["schtasks", "/run", "/tn", "StudioKiosk"]


def test_windows_no_exe_skips(monkeypatch, windows):
    monkeypatch.setattr(kiosk.os.path, "isfile", lambda path: False)
    calls = []
    monkeypatch.setattr(kiosk.subprocess, "run", lambda *a, **k: calls.append(a) or Proc())
    assert kiosk.ensure_kiosk("http://x") == "skipped: shell not deployed"
    assert calls == []


def test_windows_schtasks_refused(monkeypatch, windows):
    outputs = [Proc(stdout="INFO: No tasks are running\n"), Proc(), Proc(stderr="denied", returncode=1)]

    def fake_run(argv, **kwargs):
        return outputs.pop(0)

    monkeypatch.setattr(kiosk.subprocess, "run", fake_run)
    assert kiosk.ensure_kiosk("http://x") == "skipped: denied"


def test_windows_never_raises(monkeypatch, windows):
    def boom(argv, **kwargs):
        raise OSError("nope")

    monkeypatch.setattr(kiosk.subprocess, "run", boom)
    assert kiosk.ensure_kiosk("http://x") == "skipped: nope"


def test_posix_already_running(monkeypatch, posix):
    monkeypatch.setattr(kiosk.subprocess, "run", lambda *a, **k: Proc(returncode=0))
    assert kiosk.ensure_kiosk("http://x") == "already-running"


def test_posix_launches_detached(monkeypatch, posix):
    monkeypatch.setattr(kiosk.subprocess, "run", lambda *a, **k: Proc(returncode=1))
    monkeypatch.setattr(kiosk.shutil, "which", lambda name: "/usr/bin/studio-kiosk")
    spawned = []

    class FakePopen:
        def __init__(self, argv, **kwargs):
            spawned.append((argv, kwargs))

    monkeypatch.setattr(kiosk.subprocess, "Popen", FakePopen)
    assert kiosk.ensure_kiosk("http://x") == "launched"
    assert spawned[0][0] == ["/usr/bin/studio-kiosk", "http://x"]
    assert spawned[0][1]["start_new_session"] is True


def test_posix_no_exe_skips(monkeypatch, posix):
    monkeypatch.setattr(kiosk.subprocess, "run", lambda *a, **k: Proc(returncode=1))
    monkeypatch.setattr(kiosk.shutil, "which", lambda name: None)
    assert kiosk.ensure_kiosk("http://x") == "skipped: shell not on PATH"
