# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hub-side helper: find a kiosk browser, install a logon kiosk launcher.

Runs ON the hub (no shell quoting involved). Usage:
  python tools/hub-kiosk.py recon            # print browser + console user
  python tools/hub-kiosk.py install <url>    # write Startup .bat for <url>
  python tools/hub-kiosk.py install-app <url>  # same, but runs our Tauri shell
  python tools/hub-kiosk.py launch <url>     # open the shell on the glass now

The browser launcher uses the browser's own --kiosk mode (sticks; plain F11
does not). The Tauri shell is a fixed exe with no chrome and no updater, so
it cannot nag or surprise-update. Either way the Startup-folder .bat needs
no stored credentials, unlike a per-user task.
"""

import os
import subprocess
import sys
import time

CANDIDATES = [
    ("edge", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ("edge", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    ("chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    ("chrome", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ("chrome", os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")),
    ("firefox", r"C:\Program Files\Mozilla Firefox\firefox.exe"),
    ("firefox", r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe"),
    ("brave", r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"),
]

KIOSK_FLAGS = {
    # Dedicated profile dir: first-run/restore/update nags stay contained
    # in a throwaway profile instead of leaking into the user's browser.
    "edge": "--kiosk --edge-kiosk-type=fullscreen --no-first-run "
    "--disable-session-crashed-bubble --user-data-dir=%LOCALAPPDATA%\\StudioKioskEdge",
    "chrome": "--kiosk --no-first-run --disable-session-crashed-bubble "
    "--user-data-dir=%LOCALAPPDATA%\\StudioKioskChrome",
    "firefox": "--kiosk",
    "brave": "--kiosk --no-first-run --disable-session-crashed-bubble",
}


def find_browser():
    for family, path in CANDIDATES:
        if path and os.path.isfile(path):
            return family, path
    return None, None


def console_user():
    try:
        out = subprocess.run(
            ["tasklist", "/v", "/fi", "IMAGENAME eq explorer.exe"],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if "explorer.exe" not in line or "Console" not in line:
            continue
        parts = line.split()
        try:
            console_at = next(i for i, part in enumerate(parts) if part == "Console")
        except StopIteration:
            continue
        for part in parts[console_at + 1:]:
            if "\\" in part:
                return part
    return None


APP_EXE = r"C:\studio\kiosk\studio-kiosk.exe"
TASK_NAME = "StudioKiosk"


def _upsert_task(url):
    """(Re)create the interactive task so schtasks /run reaches the glass."""
    subprocess.run(
        [
            "schtasks", "/create", "/tn", TASK_NAME,
            "/tr", f'"{APP_EXE}" {url}',
            "/sc", "ONLOGON", "/it", "/f",
        ],
        capture_output=True, text=True, timeout=60,
    )


def _running():
    found = subprocess.run(
        ["tasklist", "/fi", f"IMAGENAME eq {os.path.basename(APP_EXE)}",
         "/fo", "CSV", "/nh"],
        capture_output=True, text=True, timeout=30,
    )
    return os.path.basename(APP_EXE).lower() in (found.stdout or "").lower()


def launch(url):
    """Open the shell on the console glass now (ssh sessions can't spawn there)."""
    if not os.path.isfile(APP_EXE):
        print(f"kiosk shell missing; copy studio-kiosk.exe to {APP_EXE} first")
        return 1
    if _running():
        print("kiosk already running")
        return 0
    _upsert_task(url)
    started = subprocess.run(
        ["schtasks", "/run", "/tn", TASK_NAME],
        capture_output=True, text=True, timeout=60,
    )
    if started.returncode != 0:
        print(f"schtasks refused: {started.stderr.strip()}")
        return 1
    for _ in range(5):
        time.sleep(2)
        if _running():
            print("kiosk running on glass")
            return 0
    print("launched but not in tasklist after 10s; check the glass")
    return 1


def startup_dir(user):
    name = user.split("\\", 1)[1] if "\\" in user else user
    return os.path.join(
        r"C:\Users", name, r"AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup"
    )


def install_app(url, user):
    if not os.path.isfile(APP_EXE):
        print(f"kiosk shell missing; copy studio-kiosk.exe to {APP_EXE} first")
        return 1
    folder = startup_dir(user)
    os.makedirs(folder, exist_ok=True)
    bat = os.path.join(folder, "studio-kiosk.bat")
    with open(bat, "w", encoding="ascii") as handle:
        handle.write(f'start "" "{APP_EXE}" {url}\n')
    print(f"installed {bat}")
    _upsert_task(url)
    print(f"task {TASK_NAME} ready for remote launch")
    return 0


def main(argv):
    if len(argv) < 2 or argv[1] not in ("recon", "install", "install-app", "launch"):
        print("usage: hub-kiosk.py recon | install <url> | install-app <url> | launch <url>")
        return 2
    family, path = find_browser()
    user = console_user()
    print(f"browser={family or 'NONE'} path={path or 'NONE'} console={user or 'NONE'}")
    if argv[1] == "recon":
        return 0 if family else 1
    if len(argv) < 3:
        print("install needs a url")
        return 2
    if argv[1] == "install-app":
        if not user:
            print("no console explorer.exe; nobody is logged on at the glass")
            return 1
        return install_app(argv[2], user)
    if argv[1] == "launch":
        return launch(argv[2])
    if not family:
        print("no kiosk browser found; install Edge or Chrome on the hub first")
        return 1
    if not user:
        print("no console explorer.exe; nobody is logged on at the glass")
        return 1
    folder = startup_dir(user)
    os.makedirs(folder, exist_ok=True)
    bat = os.path.join(folder, "studio-kiosk.bat")
    with open(bat, "w", encoding="ascii") as handle:
        handle.write(f'start "" "{path}" {KIOSK_FLAGS[family]} {argv[2]}\n')
    print(f"installed {bat}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
