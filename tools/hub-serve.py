# SPDX-License-Identifier: AGPL-3.0-or-later
"""Dual-listener Hub entrypoint: one app, two doors.

Serves the SAME FastAPI app (shared in-memory screen state, action log,
show runs) over:
- HTTPS on 8000: LAN dashboard + browser mic input (needs a secure context)
- plain HTTP on 8001: hub-local kiosk + simple LAN clients

Without a TLS pair (fresh box, pre-TLS) it serves plain HTTP on 8000 only,
preserving the old single-listener behavior.

Both listeners live in ONE process so a task restart can never leave them
split-brained; if either listener dies the process exits nonzero and the
watchdog restarts it. Server.serve() (not run()) is used so neither
listener installs signal handlers; schtasks /End force-kills the task.

Ports/dir overridable: STUDIO_HTTPS_PORT, STUDIO_HTTP_PORT, STUDIO_TLS_DIR.

Single instance: the first thing amain() does is claim an exclusive,
non-blocking lock (STUDIO_LOCK_FILE, default hub-serve.lock beside the
TLS dir, outside the deploy tree). A second starter (deploy racing the
watchdog — Windows lets both bind the same ports) prints and exits 3
instead of splitting in-memory state with the winner. OS locks release
on process death, so a crash can never wedge the next start.
"""

import asyncio
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

import uvicorn  # noqa: E402

from app.main import create_app  # noqa: E402

HTTPS_PORT_DEFAULT = 8000
HTTP_PORT_DEFAULT = 8001
HTTP_FALLBACK_PORT = 8000  # pre-TLS single listener keeps the old port
TLS_DIR_DEFAULT = r"C:\studio\tls"
LOCK_FILENAME = "hub-serve.lock"
LOCK_EXIT_CODE = 3

_lock_handle = None  # held open for process life once claimed


def tls_pair_present(tls_dir):
    """True when a keypair exists (hub-tls.ps1 writes hub.key + hub.crt)."""
    return os.path.isfile(os.path.join(tls_dir, "hub.key")) and os.path.isfile(
        os.path.join(tls_dir, "hub.crt")
    )


def listener_plan(tls_dir, https_port=HTTPS_PORT_DEFAULT, http_port=HTTP_PORT_DEFAULT):
    """Ordered (scheme, port) listeners: TLS pair -> https+http, else http only."""
    if tls_pair_present(tls_dir):
        return [("https", https_port), ("http", http_port)]
    return [("http", HTTP_FALLBACK_PORT)]


def default_lock_path(tls_dir):
    """Lock file beside the TLS dir (C:\\studio, never inside the deploy tree)."""
    return os.path.join(os.path.dirname(os.path.abspath(tls_dir)), LOCK_FILENAME)


def try_claim_lock(lock_path):
    """Non-blocking exclusive claim. Open handle on success, None when held.

    The lock is an OS byte-range/flock held by the open handle: it dies
    with the process, so no stale cleanup is ever needed. The file holds
    the winner's PID for eyeballing only.
    """
    parent = os.path.dirname(os.path.abspath(lock_path))
    os.makedirs(parent, exist_ok=True)
    handle = open(lock_path, "w", encoding="utf-8")  # noqa: PTH123 - lock file by design
    try:
        handle.write(str(os.getpid()))
        handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            handle.close()
        except OSError:  # noqa: BLE001 - loser cleanup is best effort
            pass
        return None
    return handle


def _server(app, scheme, port, tls_dir):
    kwargs = {"app": app, "host": "0.0.0.0", "port": port, "log_config": None}
    if scheme == "https":
        kwargs["ssl_keyfile"] = os.path.join(tls_dir, "hub.key")
        kwargs["ssl_certfile"] = os.path.join(tls_dir, "hub.crt")
    return uvicorn.Server(uvicorn.Config(**kwargs))


def restart_bluetooth_service(runner=subprocess.run, platform_name=None):
    """Restart the Windows Bluetooth service once. Clean exit True, else False.

    Boot heal for the TX-silent stack wedge (field finding, Nov 3): after
    a hub reboot the stack can accept BLE publishers that never air. This
    runs pre-serve on every hub start (boot, deploy, watchdog restart),
    when no BLE connection exists yet to drop. Best-effort: non-Windows,
    failure, and timeout all return False and never block serving.
    """
    if (platform_name or os.name) != "nt":
        return False
    try:
        done = runner(
            ["powershell", "-NoProfile", "-Command", "Restart-Service bthserv -Force"],
            capture_output=True,
            timeout=30,
            check=False,
        )
        return done.returncode == 0
    except Exception:  # noqa: BLE001 - heal must never break startup
        return False


async def amain():
    """Run every planned listener; return process exit code."""
    global _lock_handle
    tls_dir = os.environ.get("STUDIO_TLS_DIR", TLS_DIR_DEFAULT)
    lock_path = os.environ.get("STUDIO_LOCK_FILE") or default_lock_path(tls_dir)
    _lock_handle = try_claim_lock(lock_path)
    if _lock_handle is None:
        print(f"another hub-serve holds {lock_path}; exiting", flush=True)
        return LOCK_EXIT_CODE
    healed = restart_bluetooth_service()
    print(f"bluetooth service restart: {'ok' if healed else 'skipped-or-failed'}", flush=True)
    try:  # dongle swap/reseat watcher for the Ninja advertiser self-heal
        from app.drivers.ninja_ble import WinRtAdvertiser

        WinRtAdvertiser.start_adapter_monitor()
    except Exception as exc:  # noqa: BLE001 - monitor is best-effort
        print(f"adapter monitor not started: {exc}", flush=True)
    https_port = int(os.environ.get("STUDIO_HTTPS_PORT", HTTPS_PORT_DEFAULT))
    http_port = int(os.environ.get("STUDIO_HTTP_PORT", HTTP_PORT_DEFAULT))
    app = create_app()
    plan = listener_plan(tls_dir, https_port, http_port)
    print(f"listeners: {', '.join(f'{scheme} :{port}' for scheme, port in plan)}", flush=True)
    servers = [_server(app, scheme, port, tls_dir) for scheme, port in plan]
    tasks = [asyncio.ensure_future(server.serve()) for server in servers]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    errors = [task.exception() for task in done if task.exception() is not None]
    for error in errors:
        print(f"listener failed: {error}", flush=True)
    return 1


def main():
    """Entry point (lets tests import this module without serving)."""
    return asyncio.run(amain())


if __name__ == "__main__":
    sys.exit(main())
