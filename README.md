# TWOC Studio Hub

One-click studio director for content creators: zone power, lights, OBS scenes,
and backdrop screens driven from one LAN web UI, with an AI lighting layer on top.
Runs on a Raspberry Pi or a Windows Hub host; developed on a PC and deployed over the LAN.

**Status:** Live on the Windows Hub host, demo-ready: every light family
drives through bench, modes, presets, and AI; OBS scene control with a
dashboard card; backdrop screens with push + audio bed; voice-prompted AI
looks; undo + action log; health API and watchdog task. GVM HSI is
live-verified, Ninja BLE runs all three fixtures with DMX retained on the
bench, the IR strip maps hues to measured colors, and the AI director is
live-proven on real hardware.

See `HACKATHON.md` for the demo story: the 60-second tour, every
controlled family, the touch-tone audio wire, and the war stories.

## What it does

- **Modes:** one click powers zones, sets lights, switches OBS, updates
  screens — with per-mode fades and honest degrade-by-name when anything
  is unwired or offline.
- **Lights:** GVM BLE panels (brightness/CCT/HSI/scenes), Viltrox Ninja
  fixtures over BLE broadcast (brightness/CCT/power), USB-DMX faders, and
  an IR shelf strip (16 calibrated colors) — every family on the bench,
  in modes, and under AI.
- **AI director:** prompt + target + scope (all/zones/lights) over mock or
  live providers, with per-fixture baselines, AI power caps, one critique
  pass, and save-as-preset. Voice input on the dashboard (Chrome/Edge).
- **Screens:** kiosk page with pushed themes/text, WebAudio bed, and
  self-reload across hub restarts.
- **Presentations:** captioned multi-step shows with narration wash,
  stop-to-final, and per-step result reporting.

## Pages

- `/` dashboard: health, modes, zones, power, presets, shows, OBS, AI look
- `/control` one-asset remote (phone and OBS-dock friendly)
- `/bench` hardware bring-up per driver family
- `/equipment` per-device facts editor
- `/screen/<id>` kiosk backdrop page

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
Web UI: `https://<hub-ip>:8000` (dashboard, accept the self-signed warning once),
`https://<hub-ip>:8000/bench`, or plain `http://<hub-ip>:8001` for either page.
The hub serves both from one process (`tools/hub-serve.py`); the local kiosk
uses plain HTTP on 8001.

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
- `docs/` — protocol + wiring + environment rundowns (public-safe)
- `tools/` — Pi + Windows bootstraps, service definitions, deploy scripts

## Privacy rule

Personal studio data (devices, zones, room details) lives in `config/studios.local/`
and `Design-Documents/`, both git-ignored. Only fictional example data is committed.

## License

AGPL-3.0-or-later. See `LICENSE`.
