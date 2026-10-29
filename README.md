# TWOC Studio Hub

One-click studio director for content creators: zone power, lights, OBS scenes,
and backdrop screens driven from one LAN web UI, with an AI lighting layer on top.
Runs on a Raspberry Pi; developed on a PC and deployed with `git push pi`.

**Status:** Phase 0 scaffold — config model, mock drivers, Test Bench shell, health API.

## Quickstart (PC)

```powershell
python -m venv .venv
.venv\Scripts\pip install -r backend\requirements-dev.txt
.venv\Scripts\python -m pytest
.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --port 8000
```

Then open http://127.0.0.1:8000 and http://127.0.0.1:8000/bench.

## Deploy (Pi)

```powershell
git push pi main
```

First time on the Pi: `SUDO_PASSWORD=... bash ~/studio-hub/tools/pi-bootstrap.sh`
installs the service. Afterwards `tools/deploy.ps1` pushes and health-checks.

## Layout

- `backend/app/` — FastAPI service: `core/`, `config/`, `registry/`, `drivers/`,
  `modules/`, `api/`, `static/`
- `config/` — JSON schemas live with the loader; `profiles/` + example studio here,
  real studios in git-ignored `config/studios.local/`
- `tests/` — `unit/`, `regression/` (golden), `hardware/` (Pi only, `STUDIO_HW=1`)
- `tools/` — Pi bootstrap, systemd unit, deploy script

## Privacy rule

Personal studio data (devices, zones, room details) lives in `config/studios.local/`
and `Design-Documents/`, both git-ignored. Only fictional example data is committed.

## License

AGPL-3.0-or-later. See `LICENSE`.
