# Codebase rundown: programming side

How the service is built, where things live, and the contracts every
change must honor.

## Layout

- `backend/app/main.py` — FastAPI assembly. Owns `app.state`
  (registries, config root, health, boot id, action log, show runs,
  modality runs).
  Degraded health on broken config, never a crash. Routers: specific
  bench routers first, generic `/{driver_id}` mock routes last.
- `backend/app/core/` — logging (configure-first), safety gates.
- `backend/app/config/` — loader + schemas + ranges resolver.
  `profiles/` (shared fixture specs), `studios/` (tracked fictional
  examples), `studios.local/` (real studios, git-ignored). Loader
  merges both roots; duplicate studio ids are an error. Ranges
  (`ranges.py`, served at `GET /api/studios/<id>/ranges`) merge profile
  spec + capabilities into the one effective span set every slider and
  the defaults gate read.
- `backend/app/registry/` — validated in-memory view (devices,
  zones, modes, presets in configured order, effective device =
  instance + profile).
- `backend/app/drivers/` — leaf hardware adapters, one transport
  each. No modes, UI, or AI.
- `backend/app/modules/` — capability composers over drivers. No raw
  hardware I/O.
- `backend/app/api/` — REST surface: bench bring-up endpoints per
  driver family plus studio/mode/preset/AI/show/defaults routes
  (incl. hardware snapshots, ranges, and profiles reads).
- `backend/app/static/` — dashboard (`index.html`), Test Bench
  (`bench.html`), control (`control.html`), kiosk page (`screen.html`),
  equipment editor (datapoints, zones, default sets, addresses),
  ordering admin (`ordering.html`), modality manager
  (`modality.html`), data map (`datamap.html`), OBS targets
  (`obs.html`), shared design system (`hub.css`).
- `backend/app/config/ordering.py` — per-studio custom list order
  (`ordering.json` id arrays, loaded once, atomically saved; see
  `config/README.md`). Registry id lists honor it everywhere.
- `backend/requirements*.txt` — runtime vs dev (+pytest, httpx) deps.
- `pyproject.toml` — the ruff lint gate (`ruff check backend tools tests`).
- `tools/` — bootstraps, deploy scripts (`deploy-win.ps1` for the
  hub), watchdog, TLS, kiosk helper, dual-listener runner
  (`hub-serve.py`), Pi/systemd files.
- `tests/unit|regression|hardware` — fast logic, golden in-out,
  hardware-gated (`STUDIO_HW=1`). Markers in `pytest.ini`.

## Entrypoints

- Prod: `tools/hub-serve.py` (one app, HTTPS 8000 + HTTP 8001).
- Dev: `uvicorn app.main:app --app-dir backend --port 8000`.
- Tests: `python -m pytest` from the repo root (full suite needs
  unsandboxed loopback for TestClient).

## Contracts

- `Driver` (`drivers/base.py`): `capabilities()`, `get_state()`,
  `apply(command)`, `safe_stop()`, `probe()`. Returns `DriverResult`
  (ok/state/error, JSON state). Reports, never raises, on hardware
  paths; `safe_stop` and `probe` never raise, period.
- `Module` (`modules/base.py`): `describe()` plus capability
  methods; `status()` aggregates drivers without raising.
- Open-loop honesty: drivers that cannot read hardware say
  reachability, not state. UI shows degraded/failed plainly.
- Bench-first bring-up: every capability lands as driver + module +
  bench endpoint + bench card, verified against hardware, before
  modes/AI touch it.

## Conventions that bite

- Gentle range: brightness past nominal 10% needs `allow_bright`
  (bench API + per-row UI tick). Automated tests stay dim.
- Photo-risk: scenes are `intense` until verified gentle by eye;
  `allow_intense` + UI confirm required (see module `describe()`).
- Stream-safe output: never echo BLE addresses, credentials, keys,
  or personal paths. GVM-style `_scrub()` helpers swap echoes for
  configured names.
- Stateful transports keep one cached driver per address/port
  (DMX universe buffer, Ninja look+counter) shared by bench and
  modes — never rebuild per request.
- Health probes are curl, https-first with http fallback: .NET
  Framework TLS cannot handshake with the hub's OpenSSL 3 stack.
- JSON on disk stays pure ASCII; always pass `encoding="utf-8"`.

## Tests and gates

- Every behavior change ships its smallest focused test in
  `tests/unit/` (capture vectors for protocol code). Full suite
  green before deploy; a red test covering your change blocks it.
  Known-red unrelated failures ship only disclosed, never silent.
- No CI: the gates are pytest + `ruff check` (pyproject config) +
  a headless-Edge console check on touched pages (node is gone from
  the dev box) + the deploy health check.

## Deploy pipeline

- `tools/deploy-win.ps1 -TargetHost <hub>` ships tracked files +
  wanted untracked + `studios.local` (never design notes), zips over
  SCP, restarts the `StudioHub` task, health-checks. Guard first:
  compile check, SSH check, per-file `studios.local` hash compare
  (same path + different bytes aborts unless `-Force`), hub-side
  timestamped backup (keeps 5). Resolve divergence with
  `tools/pull-hub-config.ps1` (review, then `-Apply`).
- Requirements changes need one hub-side pip install (deploy never
  installs deps). Task/firewall/port changes need explicit remote
  steps — the deploy only restarts the existing task.
- Commits and pushes happen only on explicit ask; uncommitted work
  stays reviewable in the tree. The `pi` remote is retired (hardware
  dead) — push `origin` only; never `git push pi`.
