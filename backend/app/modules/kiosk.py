# SPDX-License-Identifier: AGPL-3.0-or-later
"""Glass kiosk auto-launch: bring the viewer up when a show needs it.

Best-effort by contract — every public function catches its own errors and
returns a short status string, so a show run never fails for kiosk reasons.

Windows launches through an interactive scheduled task (/IT): the backend may
run in a non-interactive session, where a direct spawn would never reach the
glass. POSIX tries a detached spawn of studio-kiosk from PATH.
"""

import os
import shutil
import subprocess
import sys

from ..core.logging import get_logger

log = get_logger("kiosk")

HUB_PORT = 8001  # plain-HTTP listener (hub-serve.py); HTTPS stays on 8000
WIN_TASK = "StudioKiosk"
WIN_EXE_NAME = "studio-kiosk.exe"
WIN_EXE = r"C:\studio\kiosk\studio-kiosk.exe"  # written by the kiosk deploy
POSIX_EXE = "studio-kiosk"


def _on_windows():
    return sys.platform == "win32"


def kiosk_url(screen_id):
    """Local-viewer URL for one screen id (the kiosk runs on the hub)."""
    return f"http://localhost:{HUB_PORT}/screen/{screen_id}"


def screens_in_show(show):
    """Screen ids a show narrates to, first-referenced first, deduped."""
    seen = []
    for step in show.get("steps", []):
        for screen_id in (step.get("screen") or {}):
            if screen_id not in seen:
                seen.append(screen_id)
    return seen


def ensure_kiosk(url):
    """Bring the glass viewer up on url; never raises."""
    try:
        if _on_windows():
            return _ensure_windows(url)
        return _ensure_posix(url)
    except Exception as exc:  # noqa: BLE001 - kiosk must never break a show
        log.warning("kiosk ensure failed: %s", exc)
        return f"skipped: {exc}"


def ensure_kiosk_for_show(show):
    """Open the glass on the show's first screen, when it names any."""
    screens = screens_in_show(show)
    if not screens:
        return "no-screens"
    return ensure_kiosk(kiosk_url(screens[0]))


def _run(argv):
    return subprocess.run(argv, capture_output=True, text=True, timeout=30)


def _ensure_windows(url):
    if not os.path.isfile(WIN_EXE):
        return "skipped: shell not deployed"
    found = _run(["tasklist", "/fi", f"IMAGENAME eq {WIN_EXE_NAME}", "/fo", "CSV", "/nh"])
    if WIN_EXE_NAME.lower() in (found.stdout or "").lower():
        return "already-running"
    _run(
        [
            "schtasks", "/create", "/tn", WIN_TASK,
            "/tr", f'"{WIN_EXE}" {url}',
            "/sc", "ONLOGON", "/it", "/f",
        ]
    )
    started = _run(["schtasks", "/run", "/tn", WIN_TASK])
    if started.returncode != 0:
        detail = (started.stderr or "").strip() or "schtasks refused"
        return f"skipped: {detail}"
    return "launched"


def _ensure_posix(url):
    found = _run(["pgrep", "-f", POSIX_EXE])
    if found.returncode == 0:
        return "already-running"
    exe = shutil.which(POSIX_EXE)
    if not exe:
        return "skipped: shell not on PATH"
    subprocess.Popen(
        [exe, url],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return "launched"
