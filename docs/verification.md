# Verification contract — TWOC Studio Hub

How to verify every code change: automated tests where possible,
structured manual checklists where not.

**Definition of done:** a work item is not complete until all
applicable tiers pass.

## Tier 1 — Automated tests

Single command, no hardware, no external processes beyond the dev
machine. Catches logic regressions, serialization breaks, contract
drift, and algorithm output changes.

```powershell
.venv\Scripts\python -m pytest
.venv\Scripts\ruff check backend tools tests
node --check <each touched page>.html
```

- `tests/unit` (fast logic, mocked hardware), `tests/regression`
  (golden studio snapshots), `tests/hardware` (real devices, only
  under `STUDIO_HW=1`).
- Lint gate is the `pyproject.toml` ruff config (pyflakes +
  pycodestyle errors, E501 off). Ruff must be clean — no new
  warnings carried forward.
- Optional: `pytest --cov=backend/app` for coverage on large changes.
- Network-restricted sandboxes break this tier (TestClient tests
  need loopback sockets): run Tier 1 unsandboxed.
- No "known reds" carried forward without an explicit user call.

## Tier 2 — Manual web-UI smoke

Run by a human after any page change (`/`, `/control`, `/bench`,
`/equipment`, `/ordering`, `/modality`, `/screen/<id>`). Catches
layout breaks, control-state bugs, and stale-JS issues automation
cannot see.

- Fresh-load every touched page; after any hub deploy, press F5 on
  dashboard/bench/control/equipment/ordering/modality (only the
  kiosk self-reloads — everything else serves cached JS otherwise).
- Browser console: zero errors.
- Exercise each touched card end to end against the live hub.
- Confirm page-marker strings are present in the served page.

## Tier 3 — Manual hardware integration

Run by a human after any driver, module, modes/AI, or config-shape
change. Catches transport failures, addressing mistakes, and
firmware quirks that only surface on the real rig.

General flow, every time:

1. Deploy via `tools/deploy-win.ps1` (ships code, restarts the hub
   task, health-checks; verifies plug-map hashes before touching
   hub-local config).
2. Confirm hub health + both studios listed; spot-verify hub file
   hashes match the repo on driver changes.
3. Trials start dim (1–5%); never above nominal 10% (DMX 26) unless
   the user explicitly requested that brighter test.
4. Eyeball-confirm on the physical device or meter — `ok:true` is
   not on-air proof.
5. After any hub restart: re-push kiosk screen content (in-memory
   state is wiped; show texts are user-owned — ask, don't invent)
   and F5 the bench page.

Per-family checklists:

- **Kasa plugs:** prime discovery on page load; confirm alias
  resolution (no hardcoded-host dependence); allow one silent
  first-contact heal, then honest-red on genuine failure.
- **GVM BLE:** phone in airplane mode (or force-stopped app) so it
  doesn't hold the panel connection; bench power-toggle + eyeball
  for panel identity; named panels only, never addresses on screen.
- **DMX:** Ninja 400 must show the connection icon (upper-left)
  under a valid stream; verify dimmer + CCT response; watch for
  flicker (marginal frames); polarity swap is the known fix for
  total silence on new adapters.
- **Ninja BLE:** never trust `Started/ok:true` alone — confirm with
  a witnessed send (eyeball, or the cross-PC watcher in
  `tools/ninja-watch.py`; the hub cannot hear its own frames).
  First publish after a hub restart should carry the start-heal
  flag; after any dongle swap or reboot, restart the BT service
  path, then verify with one production send.
- **IR strip:** bench raw-send + eyeball; color names follow the
  canonical calibration labels, not the remote's printing.
- **Screens/kiosk:** re-push theme/text after restarts; confirm
  audio audibly (tone track is the check signal); double-ESC exit
  must work from the glass.
- **PC-link:** loop test first (`ping` → `ack`, `confirmed:true`);
  exact full WASAPI endpoint names on Windows (no MME substring
  matches); levels at 100% both ends; OBS seat starts only with the
  user's word (GUI launch rule).
- **OBS:** status shows connected + collection + scenes; switch
  both directions including spaced names; boot job only with the
  user's word.
- **AI director:** mock provider first (pins hold), then live
  provider with explicit scope; eyeball every targeted fixture.

Standing safety rules: never cycle plug/bench power unannounced
(strips feed camera rigs + monitors); manual-only protection zones
hold unless explicitly overridden.

## Privacy gate (before any public push)

- Content-grep the pushed tree: zero real MACs (only `AA:BB`
  placeholders), zero LAN IPs (only `192.0.2.x` doc range), zero
  emails, zero personal names, zero keys/secrets, zero home paths.
- Always grep file *content* across tracked files — never infer
  cleanliness from filenames.
- Never tracked: `studios.local/`, `Design-Documents/`,
  `captures/`, `logs/`, `Portable-OBS/`, `tools/pc-link/*.log`,
  `scratch/`, hub TLS material, `link.json`/`obs.json`/plug
  credentials. (The hub ships what's needed at deploy time.)

## Extraction gates (spun-out open-source projects)

Each extracted project carries a lighter form of the same contract:

- **Tier 1:** ported automated tests that run from a clean checkout
  with one command.
- **Tier 2:** the README quickstart, verified from a clean checkout
  exactly as written.
- **Tier 3:** a proof run against real hardware before release.
- Privacy gate applies unchanged. License is the author's call per
  project (the hub is `AGPL-3.0-or-later`; extracts may go MIT for
  adoption).
