# Environment rundown: everything this project needs

What to install, plug in, and configure — hub host, dev PC, network,
hardware adapters, accounts, and the one-off reverse-engineering kit.
Facts below mirror the repo's scripts and requirements files.

## Hub host (runs the show)

- Windows 10/11 PC on the LAN, always on. First-time setup is
  `tools\win-bootstrap.ps1` (elevated): installs Python 3.14 if
  missing, builds `.venv`, detects the IR port, opens the firewall,
  registers tasks, starts the hub.
- Python deps (`backend/requirements.txt`, pip into `.venv`):
  fastapi, uvicorn, jsonschema, python-kasa, pyserial, bleak,
  websocket-client, and winsdk (Windows-only marker — Pi installs
  skip it). Deploy ships code, not deps: after a requirements change,
  run pip install on the hub once.
- Bluetooth: a BLE-capable radio with the `bthserv` service running
  (current hub: USB BT5 adapter). Used by GVM (GATT client) and
  Ninja (advertisement publisher). No pairing is ever done on the OS.
- USB-DMX adapter (current: DSD SH-RS09B, FTDI chip) on a known COM
  port, XLR→RJ45 wiring per `docs/dmx-wiring.md`, 120-ohm terminator
  on the last fixture.
- IR module (YS-IRTM over USB serial), port in `IR_PORT`
  (auto-detected at bootstrap); wiring in `docs/ir-wiring.md`.
- Kasa plugs on the LAN, no login needed on current firmware
  (per-plug username/password in plug-map only for newer AES models).
- LAN + SSH: OpenSSH server, one ed25519 key for passwordless
  deploys (see `tools/deploy-win.ps1` header). No inbound internet.
- Scheduled tasks (SYSTEM): `StudioHub` (runs `tools/hub-serve.py`:
  HTTPS 8000 + HTTP 8001 from one process), `StudioHubWatchdog`
  (every 2 min, restarts on dark health, skips 3 min after boot).
- TLS (optional but live): `C:\studio\tls\hub.key` + `hub.crt`,
  self-signed 10-year CA via `tools/hub-tls.ps1`. Never in the repo.
  Trust = one browser warning (or install the CA per device).
- Kiosk (optional): Edge `--kiosk` to the local screen page, Startup
  `.bat` written by `tools/hub-kiosk.py` (waits for hub health, reaps
  stale instances). Speakers on the hub enable audio beds.
- Ports/URLs: 8000 = HTTPS dashboard/bench/API, 8001 = plain HTTP
  (kiosk + simple clients), 22 = SSH. Health: `/api/health`.

## Dev PC

- Windows + Python 3.14 + git + SSH client + curl.exe (ships with
  Windows). `.venv` from `backend/requirements-dev.txt` (runtime
  deps plus pytest and httpx). Full suite runs here unsandboxed
  (sandboxed shells block the loopback sockets TestClient needs).
- Android platform-tools (`adb`) for phone captures (see below).
- Optional: Portable OBS (git-ignored) as the OBS WebSocket target;
  a camera for color calibration captures.

## Network and accounts

- One LAN; hub on a stable address (`STUDIO_HUB_HOST` env or
  `-TargetHost`). Kasa plugs DHCP — the alias resolver absorbs drift.
- Secrets (all git-ignored, hub-local): SSH private key (dev PC),
  `config/studios.local/meta.json` (AI provider key),
  per-plug credentials (newer firmware), OBS WebSocket password.
  Never paste any of these in chat or on stream.

## Reverse-engineering kit (phone, one-off per protocol)

- Android phone + vendor app (GVM LED, Weeylite Pro), light paired
  through the app only (never OS Bluetooth settings).
- Developer options: Bluetooth HCI snoop on. Retrieval depends on
  the build: file pull when exposed, else live `btsnoop_net`
  (phone localhost:8872 over `adb forward`) — the path this repo's
  `captures/ninja10/*.py` tooling uses. Last resort: bugreport zip,
  extracting only the snoop entry and deleting the rest immediately.
- Captures live in `captures/` (git-ignored): scripts, tap logs with
  PC+phone clocks, UI dumps, `.btsnoop` files. Never commit them.

## Dormant / alternate paths

- Raspberry Pi service files (`tools/pi-bootstrap.sh`,
  `tools/studio-hub.service`, `tools/deploy.ps1`) still exist from
  before the hub migrated to Windows. Untested since the move.
- Tauri kiosk shell (`kiosk-shell/`) builds but is not what the hub
  runs today (Edge kiosk is).
