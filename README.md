# TWOC Studio Hub

One-click studio director for content creators: zone power, lights, OBS scenes,
and backdrop screens driven from one LAN web UI, with an AI lighting layer on top.
Runs on a Raspberry Pi or a Windows Hub host; developed on a PC and deployed over the LAN.

**Status:** Live on the Windows Hub host, demo-ready: every light family
drives through bench, modes, modalities, and AI; OBS scene control with a
dashboard card; backdrop screens with push + audio bed; voice-prompted AI
looks; undo + action log; health API and watchdog task. GVM HSI is
live-verified, the Ninja 400 runs over DMX while the Ninja 10/20 run over
BLE broadcast, the IR strip maps hues to measured colors, and the AI
director is live-proven on real hardware.

See `HACKATHON.md` for the demo story: the 60-second tour, every
controlled family, the touch-tone audio wire, and the war stories.

## What it does

- **Modes:** one click powers zones, sets lights, switches OBS, updates
  screens — with per-mode fades and honest degrade-by-name when anything
  is unwired or offline.
- **Lights:** GVM BLE panels (brightness/CCT/HSI/scenes), Ninja 400 over
  USB-DMX (dimmer + CCT faders), Ninja 10/20 over BLE broadcast
  (brightness/CCT/soft power), and an IR shelf strip (16 calibrated
  colors) — every family on the bench, in modes and modalities, and
  under AI.
- **AI director:** prompt + target + scope (all/zones/lights) over mock or
  live providers, with per-fixture baselines, AI power caps, one critique
  pass, and save-as-preset. Voice input on the dashboard (Chrome/Edge).
- **Screens:** kiosk page with pushed themes/text, WebAudio bed, and
  self-reload across hub restarts.
- **Presentations:** captioned multi-step shows with narration wash,
  stop-to-final, and per-step result reporting.
- **Zones & plugs:** eight zones in plug order — Hub Manager (no plug),
  Workstation Computer/Content/Background (Plugs 1–3), Hands-On and
  Hands-On Screens (Plugs 4–5), Ambient Mood (Plug 6), Room (fixed
  lights, no plugs) — each listing its device collection; the six
  official Kasa plugs (Studio Plug 1–6) drive by name from bench and
  control, with discovery primed on page load so switches answer fast.
- **Ordering:** `/ordering` drag/drop page sets custom list order
  (zones, devices, modes, presets, plugs, panels, modalities) with
  one-click A–Z sorts, persisted as one small JSON file per studio.
- **Modalities:** smart collections (kind mode/preset/show/custom) run
  from the dashboard and `/modality`, with run/stop/undo/snapshot —
  the one-click demo surface over the modes engine.
- **PC link:** the show PC boots over the LAN (Wake-on-LAN) and takes
  DTMF touch-tone commands down a 3.5mm audio wire — per-seat OBS
  start, reverse acks, optional DTMF shutdown — with zero ports open
  on that machine (`docs/pc-link.md`).
- **Hub power:** Hub Manager restarts or fully shuts down the hub PC
  (optionally everything-off first, plus a DTMF shutdown of the
  workstation over the audio wire), always behind a final confirmation.

## Pages

- `/` dashboard: health, modalities (run/stop/undo/snapshot), zones, power, OBS, AI look
- `/modality` Modality Manager: add/edit/remove the smart collections above
- `/control` one-asset remote (phone and OBS-dock friendly) + Kasa plugs
- `/bench` hardware bring-up per driver family (incl. official Kasa plugs)
- `/equipment` per-device facts editor + zone-move editor
- `/ordering` drag/drop list-ordering admin
- `/screen/<id>` kiosk backdrop page

**Implementing from source?** Follow `docs/runbook.md` — the full
sequence from download to a working studio (config, plugs, hub,
kiosk, workstation link, bring-up). Package/driver inventory:
`docs/dependencies.md`.

## Quickstart (PC)

```powershell
python -m venv .venv
.venv\Scripts\pip install -r backend\requirements-dev.txt
.venv\Scripts\python -m pytest
.venv\Scripts\ruff check backend tools tests
.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --port 8000
```

Tests: `tests/unit` (fast logic, mocked hardware), `tests/regression`
(golden studio snapshots), `tests/hardware` (real devices, `STUDIO_HW=1`
only). Lint gate is the `pyproject.toml` ruff config (pyflakes +
pycodestyle errors); coverage via `pytest --cov=backend/app`.

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

## Deploy (Pi) — RETIRED

The Pi hardware is dead and the `pi` git remote is retired; the Windows
Hub host above is the only deploy target and the public git remote is the
only backup. The Pi files (`tools/pi-bootstrap.sh`,
`tools/studio-hub.service`, `tools/deploy.ps1`) stay in the tree for
reference only — do not `git push pi`.

## Layout

- `backend/app/` — FastAPI service: `core/`, `config/`, `registry/`, `drivers/`,
  `modules/`, `api/`, `static/`
- `config/` — JSON schemas live with the loader; `profiles/` + example studio here,
  real studios in git-ignored `config/studios.local/`
- `tests/` — `unit/`, `regression/` (golden), `hardware/` (Pi only, `STUDIO_HW=1`)
- `docs/` — protocol + wiring + environment rundowns (public-safe)
- `tools/` — Windows bootstraps, service definitions, deploy scripts
  (Pi files retired with the dead hardware, kept for reference)

## Privacy rule

Personal studio data (devices, zones, room details) lives in `config/studios.local/`
and `Design-Documents/`, both git-ignored. Only fictional example data is committed.

## License

AGPL-3.0-or-later. See `LICENSE`.
