# TWOC Studio Hub

One-click studio director for content creators: zone power, lights, OBS scenes,
and backdrop screens driven from one LAN web UI, with an AI lighting layer on top.
Runs on a Raspberry Pi or a Windows Hub host; developed on a PC and deployed over the LAN.

**Status:** Live on the Windows Hub host: real USB-DMX / Kasa / IR / GVM-BLE
drivers, modes engine, OBS + screens + AI-director scaffolds, mode dashboard,
Test Bench, health API, and a watchdog task. DMX is verified against two
Viltrox lights (dwell-paced fades); the OBS handshake and screen persistence
are still scaffold.

## Quickstart (PC)

```powershell
python -m venv .venv
.venv\Scripts\pip install -r backend\requirements-dev.txt
.venv\Scripts\python -m pytest
.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --port 8000
```

Then open http://127.0.0.1:8000 and http://127.0.0.1:8000/bench.

## Deploy (Windows Hub host)

One-time SSH key (passwordless deploys): on this PC `ssh-keygen -t ed25519`,
then on the Hub host in an elevated PowerShell append your `id_ed25519.pub`
to `C:\ProgramData\ssh\administrators_authorized_keys` and run:
`icacls C:\ProgramData\ssh\administrators_authorized_keys /inheritance:r /grant SYSTEM:F /grant Administrators:F`

First time on the host: copy the tree plus `config/studios.local/` to
`C:\studio\studio-hub` (USB, file share, or VNC transfer), then in an
elevated PowerShell there:
`powershell -ExecutionPolicy Bypass -File C:\studio\studio-hub\tools\win-bootstrap.ps1`

Afterwards `tools\deploy-win.ps1 -TargetHost <hub-ip>` ships the tree, restarts
the task, and health-checks (or set `STUDIO_HUB_HOST` once and run it bare).
The Hub auto-starts at boot (the `StudioHub` task's boot trigger), and a
`StudioHubWatchdog` task (every 2 min, created by the bootstrap) restarts it
when `/api/health` stays dark — see `tools/hub-watchdog.ps1`.
Web UI: `http://<hub-ip>:8000` (dashboard), `http://<hub-ip>:8000/bench`.

## Deploy (Pi)

```powershell
git push pi main
```

First time on the Pi: `SUDO_PASSWORD=... bash ~/studio-hub/tools/pi-bootstrap.sh`
installs the service. Afterwards `tools/deploy.ps1 -TargetHost <pi-ip>` pushes
and health-checks (or set `STUDIO_HUB_HOST` once).

## Layout

- `backend/app/` — FastAPI service: `core/`, `config/`, `registry/`, `drivers/`,
  `modules/`, `api/`, `static/`
- `config/` — JSON schemas live with the loader; `profiles/` + example studio here,
  real studios in git-ignored `config/studios.local/`
- `tests/` — `unit/`, `regression/` (golden), `hardware/` (Pi only, `STUDIO_HW=1`)
- `tools/` — Pi + Windows bootstraps, service definitions, deploy scripts

## Privacy rule

Personal studio data (devices, zones, room details) lives in `config/studios.local/`
and `Design-Documents/`, both git-ignored. Only fictional example data is committed.

## License

AGPL-3.0-or-later. See `LICENSE`.
