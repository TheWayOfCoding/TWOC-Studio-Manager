# Runbook: from source download to a working studio

Every step, in order, to implement this project from a fresh source
tree. Detail lives in the linked docs; this file is the sequence.
Nothing here needs a database or a build step for the web UI.

## 0. What you need

- A Windows hub PC (always on, LAN) and a dev PC with git + Python.
- Hardware as applicable: Kasa HS103 plugs, GVM panels, Viltrox Ninja
  fixtures, USB-DMX dongle, YS-IRTM IR module, 3.5mm cables.
  Inventory and roles: `docs/dependencies.md`. Setup facts:
  `docs/environment.md`.

## 1. Get the source

```powershell
git clone <repo-url> TWOC-Studio-Manager
cd TWOC-Studio-Manager
```

(A downloaded zip works identically; only the deploy step assumes a
git working tree for its file list.)

## 2. Verify on the dev PC (no hardware)

```powershell
python -m venv .venv
.venv\Scripts\pip install -r backend\requirements-dev.txt
.venv\Scripts\python -m pytest          # expect: all passed, hardware skipped
.venv\Scripts\ruff check backend tools tests
.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --port 8000
```

Open http://127.0.0.1:8000 — the tracked example studio (`config/studios/
example-studio`, fictional) drives every page against mock drivers.

## 3. Create your real studio config

1. Copy `config/studios/example-studio/` to
   `config/studios.local/<your-studio-id>/` (git-ignored; real data
   never commits).
2. Edit `studio.json` (id must match the folder), `zones.json`,
   `devices.json`, `modes.json`, `presets.json`. Schemas are enforced
   at load (`backend/app/config/schemas/`); field reference:
   `config/README.md`.
3. Zone/plug layout used by the reference rig (adapt freely):
   Hub Manager (no plug) → workstation zones on Plugs 1–3 →
   hands-on on Plug 4 → hands-on screens Plug 5 → mood Plug 6 →
   Room (fixed lights, no plugs).
4. Restart the dev server: `/api/health` must stay `ok` and your
   studio appears in `/api/studios`. Broken config degrades health
   with the error named — fix before continuing.

## 4. Kasa plugs

1. In the Kasa app, name the plugs `Studio Plug 1` … `Studio Plug 6`.
2. Create `config/studios.local/plug-map.json` (shape in
   `config/README.md`): alias + zone + powerGroup per plug. Hosts are
   DHCP fallbacks only — control resolves by alias.
3. Bench → Discover: all plugs should answer (no login on current
   firmware). Bench "Kasa smart plugs — official names" + Control
   page then switch each by name.

## 5. Hub host: first-time setup

1. Copy the tree plus `config/studios.local/` to the hub
   (e.g. `C:\studio\studio-hub`; no spaces in the path).
2. Elevated PowerShell on the hub:
   `powershell -ExecutionPolicy Bypass -File C:\studio\studio-hub\tools\win-bootstrap.ps1`
   (installs Python, builds `.venv`, detects the IR port, opens
   firewall 8000/8001, registers the `StudioHub` + watchdog tasks,
   starts the hub, health-checks).
3. Optional HTTPS (needed for browser mic): run `tools\hub-tls.ps1`
   on the hub once. Plain HTTP on 8001 works without it.

## 6. Deploy loop (after first setup)

1. One-time passwordless SSH (see the header of
   `tools/deploy-win.ps1` for the key + ACL steps).
2. `tools\deploy-win.ps1 -TargetHost <hub-ip>` — ships the tree +
   `studios.local`, restarts the task, health-checks.
3. Requirements changes need one hub-side
   `.venv\Scripts\pip install -r backend\requirements.txt`.

## 7. Kiosk backdrop screen (optional)

1. Build or copy `studio-kiosk.exe` (`kiosk-shell/README.md`) to
   `C:\studio\kiosk\` on the hub.
2. On the hub: `python tools\hub-kiosk.py install-app http://localhost:8001/screen/<screen-id>`
   where `<screen-id>` is a `driver: screen` device in your studio.
3. The page self-reloads across hub restarts; re-push show text after
   restarts (screen state is in-memory by design).

## 8. Workstation link (OBS boot + DTMF shutdown)

1. Per user account that runs the show (reference rig: autologin
   `creative` on the 9900x): follow the per-user install in
   `tools/pc-link/README.md` — listener + fresh `local.json` +
   Portable OBS copy in that profile, calibrated, then Startup entry.
2. Enable Wake-on-LAN there (BIOS + NIC checklist in the same README).
3. Hub side `config/studios.local/link.json`: `wol_mac` (never in
   chat/repo), `audio_out`, `audio_in` (pick via bench PC-link card).
4. Wire cable A (hub out → workstation mic), cable B (reverse acks).
   Bench: Send+Wait `ping` must return `confirmed`, then Boot+OBS.
5. Optional DTMF shutdown: `actions.shutdown` opt-in in the
   workstation `local.json` (`docs/pc-link.md`).

## 9. OBS and AI (optional integrations)

- OBS: `config/studios.local/obs.json` (host/port/password from OBS
  WebSocket settings) + scene/collection names in `modes.json` must
  match OBS exactly (`config/README.md`).
- AI: mock provider works with zero setup. Live provider:
  `config/studios.local/meta.json` key (`config/README.md`), model
  defaults sensibly when omitted.

## 10. Bring-up, bench-first (in this order)

Bench page, one family at a time, each verified by eye before modes:

1. Kasa official plugs (state + toggle).
2. GVM panels (Probe → power 5% → brightness/CCT/HSI).
3. Ninja lights (Probe → 5%; if silent with ok:true, restart the hub
   program — the BT heal does the rest; `docs/ninja-ble.md`).
4. DMX bench (if used) and IR strip (capture codes per
   `docs/ir-capture.md`).
5. Backdrop screen push + audio bed, then presentations.
6. PC-link loop + first boot job (step 8).

Then dashboard: apply each mode, watch subsystems report ready (or
honestly degraded-by-name — fix wiring, never silence).

## 11. Polish and go-live checks

- `/ordering`: drag/drop your list order (A–Z buttons per collection).
- Dashboard zones list each zone's device collection — eyeball against
  the room.
- Hub Manager card: test Restart, then Power down (final confirmation)
  only when ready to press the hub's power button.
- Full gate before calling it done:
  `.venv\Scripts\python -m pytest` green + one cold-start run of your
  opening mode end to end.

## Day-to-day

- Change config → `deploy-win` → hard-refresh pages (kiosk refreshes
  itself). Change code → same. Change deps → deploy + hub pip install.
- Stuck Bluetooth → dashboard Restart hub program. Stuck anything →
  `/api/health` first, then the family doc in `docs/`.
