# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the dual-listener entrypoint (no sockets; plan only)."""

import importlib.util
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"


def load_serve():
    """Import tools/hub-serve.py (hyphenated name needs spec loading)."""
    spec = importlib.util.spec_from_file_location("hub_serve", TOOLS_DIR / "hub-serve.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plan_dual_when_pair_present(tmp_path):
    serve = load_serve()
    (tmp_path / "hub.key").write_text("k")
    (tmp_path / "hub.crt").write_text("c")
    assert serve.listener_plan(str(tmp_path)) == [("https", 8000), ("http", 8001)]


def test_plan_http_only_when_pair_missing(tmp_path):
    serve = load_serve()
    assert serve.listener_plan(str(tmp_path)) == [("http", 8000)]


def test_plan_http_only_when_pair_partial(tmp_path):
    serve = load_serve()
    (tmp_path / "hub.key").write_text("k")
    assert serve.listener_plan(str(tmp_path)) == [("http", 8000)]


def test_plan_respects_port_args(tmp_path):
    serve = load_serve()
    (tmp_path / "hub.key").write_text("k")
    (tmp_path / "hub.crt").write_text("c")
    assert serve.listener_plan(str(tmp_path), https_port=8443, http_port=8080) == [
        ("https", 8443),
        ("http", 8080),
    ]


class _Done:
    def __init__(self, returncode):
        self.returncode = returncode


def test_bt_heal_runs_powershell_on_windows():
    serve = load_serve()
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        return _Done(0)

    assert serve.restart_bluetooth_service(runner=runner, platform_name="nt") is True
    assert "Restart-Service bthserv" in " ".join(calls[0])


def test_bt_heal_reports_failure_and_skips_non_windows():
    serve = load_serve()
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        return _Done(1)

    assert serve.restart_bluetooth_service(runner=runner, platform_name="nt") is False
    assert serve.restart_bluetooth_service(runner=runner, platform_name="posix") is False
    assert len(calls) == 1  # posix never reaches the runner


def test_bt_heal_never_raises():
    serve = load_serve()

    def runner(cmd, **kwargs):
        raise OSError("no shell")

    assert serve.restart_bluetooth_service(runner=runner, platform_name="nt") is False
