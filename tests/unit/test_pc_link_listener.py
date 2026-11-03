# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the main-PC listener helpers (no audio hardware)."""

import importlib.util
import sys
from pathlib import Path

import pytest

LISTENER = Path(__file__).resolve().parents[2] / "tools" / "pc-link" / "listener.py"


def _load_listener():
    spec = importlib.util.spec_from_file_location("pc_link_listener", LISTENER)
    module = importlib.util.module_from_spec(spec)
    sys.modules["pc_link_listener"] = module
    spec.loader.exec_module(module)
    return module


listener = _load_listener()

DEVICES = [
    {"name": "Microphone (Front)", "max_input_channels": 2, "max_output_channels": 0},
    {"name": "Speakers (Rear)", "max_input_channels": 0, "max_output_channels": 2},
]


class _FakeSD:
    def __init__(self, played=None, fail_play=False):
        self.played = played if played is not None else []
        self.fail_play = fail_play

    def query_devices(self):
        return DEVICES

    def play(self, samples, samplerate=None, device=None, blocking=True):
        if self.fail_play:
            raise RuntimeError("nope")
        self.played.append({"frames": len(samples), "samplerate": samplerate, "device": device})


@pytest.fixture
def fake_sd(monkeypatch):
    fake = _FakeSD()
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    return fake


@pytest.mark.unit
def test_load_config_round_trip_and_fallbacks(tmp_path):
    missing = tmp_path / "nope.json"
    assert listener.load_config(missing) == {}
    good = tmp_path / "local.json"
    good.write_text('{"rate": 48000}', encoding="utf-8")
    assert listener.load_config(good) == {"rate": 48000}
    bad = tmp_path / "list.json"
    bad.write_text("[1, 2]", encoding="utf-8")
    assert listener.load_config(bad) == {}


@pytest.mark.unit
def test_resolve_input_output_match(fake_sd):
    assert listener.resolve_input(None) is None
    assert listener.resolve_input(3) == 3
    assert listener.resolve_input("Front") == 0
    assert listener.resolve_output("Rear") == 1
    with pytest.raises(SystemExit):
        listener.resolve_input("Ghost Mic")
    with pytest.raises(SystemExit):
        listener.resolve_output("Ghost Out")


@pytest.mark.unit
def test_run_action_null_and_launch(monkeypatch, capsys):
    listener.run_action("ping", None)
    assert "no launch action" in capsys.readouterr().out
    launched = []
    monkeypatch.setattr("subprocess.Popen", lambda args, **kw: launched.append(args) or None)
    listener.run_action("obs", {"launch": "obs64.exe", "args": ["--tray"]})
    assert launched == [["obs64.exe", "--tray"]]


@pytest.mark.unit
def test_run_action_honors_cwd(monkeypatch):
    """Actions may set a working directory (portable OBS needs its own)."""
    seen = {}
    monkeypatch.setattr("subprocess.Popen", lambda args, **kw: seen.update(args=args, cwd=kw.get("cwd")) or None)
    listener.run_action("obs", {"launch": "obs64.exe", "cwd": "C:/obs/bin/64bit"})
    assert seen == {"args": ["obs64.exe"], "cwd": "C:/obs/bin/64bit"}


@pytest.mark.unit
def test_play_ack_sends_and_survives_failure(fake_sd, capsys):
    listener.play_ack("ack", 1, 44100)
    assert len(fake_sd.played) == 1
    assert fake_sd.played[0]["device"] == 1
    assert "sent ack" in capsys.readouterr().out
    fake_sd.fail_play = True
    listener.play_ack("ack", 1, 44100)  # logs, never raises
    assert "failed (ack)" in capsys.readouterr().out


class _FakeSock:
    def __init__(self, reply=None, fail_on=None):
        self.reply = reply if reply is not None else b"HTTP/1.1 101 Switching Protocols\r\n\r\n"
        self.fail_on = fail_on
        self.sent = b""

    def settimeout(self, timeout):
        pass

    def sendall(self, data):
        if self.fail_on == "send":
            raise OSError("send down")
        self.sent = data

    def recv(self, count):
        if self.fail_on == "recv":
            raise OSError("recv down")
        return self.reply

    def close(self):
        pass


@pytest.mark.unit
def test_obs_websocket_ready_handshake_shapes(monkeypatch):
    made = []

    def _connect(addr, timeout=None):
        sock = _FakeSock()
        made.append(sock)
        return sock

    monkeypatch.setattr("socket.create_connection", _connect)
    assert listener.obs_websocket_ready() is True
    assert b"Upgrade: websocket" in made[0].sent

    monkeypatch.setattr("socket.create_connection", lambda addr, timeout=None: _FakeSock(b"HTTP/1.1 400 Bad\r\n\r\n"))
    assert listener.obs_websocket_ready() is False
    monkeypatch.setattr("socket.create_connection", lambda addr, timeout=None: _FakeSock(fail_on="recv"))
    assert listener.obs_websocket_ready() is False

    def _down(addr, timeout=None):
        raise OSError("nothing listening")

    monkeypatch.setattr("socket.create_connection", _down)
    assert listener.obs_websocket_ready() is False


@pytest.mark.unit
def test_obs_watch_loop_reports_verdict(monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(listener, "obs_websocket_ready", lambda host, port: True)
    monkeypatch.setattr(listener, "play_ack", lambda command, device, rate: sent.append(command))
    listener.obs_watch_loop({"timeout_s": 5, "poll_s": 0.5}, 1, True, 44100)
    assert sent == ["obs_ready"]
    assert "obs_ready" in capsys.readouterr().out

    monkeypatch.setattr(listener, "obs_websocket_ready", lambda host, port: False)
    listener.obs_watch_loop({"timeout_s": 0, "poll_s": 0.5}, 1, True, 44100)
    assert sent == ["obs_ready", "obs_fail"]

    sent.clear()
    listener.obs_watch_loop({"timeout_s": 5, "poll_s": 0.5}, None, False, 44100)
    assert sent == []
    assert "no ack device" in capsys.readouterr().out


@pytest.mark.unit
def test_watch_number_clamps_with_warning(capsys):
    assert listener._watch_number({}, "port", 4455, 1, 65535) == 4455
    assert listener._watch_number({"port": "xx"}, "port", 4455, 1, 65535) == 4455
    assert listener._watch_number({"port": 99999}, "port", 4455, 1, 65535) == 4455
    assert "out of range" in capsys.readouterr().out


@pytest.mark.unit
def test_obs_already_up_guards_relaunch(monkeypatch):
    """watch_obs actions probe first; plain actions never probe."""
    monkeypatch.setattr(listener, "obs_websocket_ready", lambda host, port, timeout=3: True)
    assert listener.obs_already_up({"launch": "x", "watch_obs": True}, {"host": "h", "port": 1}) is True
    assert listener.obs_already_up({"launch": "x"}, {"host": "h", "port": 1}) is False
    assert listener.obs_already_up(None, {}) is False
    monkeypatch.setattr(listener, "obs_websocket_ready", lambda host, port, timeout=3: False)
    assert listener.obs_already_up({"launch": "x", "watch_obs": True}, {}) is False


@pytest.mark.unit
def test_listener_shares_hub_command_space():
    assert set(("ping", "obs", "ack", "obs_ready", "obs_fail")) <= set(listener.COMMANDS)
