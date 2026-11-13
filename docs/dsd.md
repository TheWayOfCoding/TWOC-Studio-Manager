# Data Structure Document (DSD): TWOC Studio Hub

What every persisted and in-memory structure is, which field is the key,
and how the pieces link. The normative schemas live in
`backend/app/config/schemas/`; the field-by-field companion is
[the data dictionary](data-dictionary.md); the live browser view is the
Administration → Data map page (`/datamap`, `backend/app/static/datamap.html`).

## 1. Key policy (the short answer)

Equipment pieces **do** have a unique key: `device.id`.

- `id` is the primary key of every entity (studio, device, zone, mode,
  preset, profile, default set, modality, show). All links use ids.
- `name` (devices) and `label` (everything else) are display-only and
  freely editable; renaming never breaks a link because nothing links by
  name. Device names edit via
  `PUT /api/studios/{studio}/devices/{device}/properties`
  (`backend/app/api/equipment.py`).
- `id` values are immutable: no rename endpoint exists. Identity fields
  (`id`, `type`, `powerPlug`, `driver`) are deliberately read-only on save
  so an edit can never rewire the studio. The one connection-level
  exception is `driverAddress` (BLE channel/group, DMX map, GVM MAC),
  editable through a validated save (`PUT .../address`) whose shapes
  follow what each driver family needs; dropping an interface half moves
  the `transport` pick with it and says so.
- Id spelling: devices, zones, modes, profiles, studios match
  `^[a-z0-9][a-z0-9_-]*$` (schema-enforced). Preset and default-set ids
  match `[A-Za-z0-9][A-Za-z0-9_-]{0,63}` (code-enforced in
  `api/orchestrator.py` and `api/defaults.py`); `assign` is reserved
  for default sets.
- Duplicate ids are rejected at load (`loader.load_studio`) and duplicate
  studio ids across the `studios/` + `studios.local/` roots are an error.

The only name-keyed stores are outside the studio graph: OBS target names
in `studios.local/obs.json` (each studio points at one by name) and audio
device substring matches in `studios.local/link.json` (hardware discovery,
not a foreign key).

## 2. Entity catalog

| Entity | Lives in | Key | Links out (all by id) |
|---|---|---|---|
| Studio | `studio.json` | `id` | `defaultMode` → mode, `defaultSet` → default set |
| Device | `devices.json` | `id` | `zone` → zone, `profile` → profile, `powerGroup`/`powerPlug` → power map |
| Profile | `profiles/*.json` | `id` | none (shared model truth) |
| Zone | `zones.json` | `id` | `powerGroups[]` → power groups |
| Mode | `modes.json` | `id` | `zones[]`, `powerOn/Off[]`, `lights{}/screen{}` device ids |
| Preset | `presets.json` key | key | `lights{}/screen{}` device ids |
| Default set | `defaults.json` key | key | `devices{}` device ids, `plugs{}` plug ids |
| Modality | `modalities.json` key | key | beat actions: groups, zones, device ids |
| Show | `shows.json` key | key | step `lights/screen/scenes` device ids |
| Ordering | `ordering.json` | — | id arrays per collection |
| Plug map | `studios.local/plug-map.json` | `plug` | `zone` claim per entry |
| OBS | `studios.local/obs.json` | target name | studio → target name |

## 3. Relationship map

```
profile 1──┐
           │ device.profile (optional)
studio 1───┼── devices[] ── device.zone ──► zone ── powerGroups[] ──► power group
           │      │  ▲
           │      │  │  presets/defaults/modes/modalities/shows reference device ids
           │      ▼  │
           │   powerPlug ──► plug-map plug (soft: unknown plugs never match)
           │
           ├── zones[] / modes[] / presets{} / modalities{} / defaults{}
           ├── shows{} (file-read per request, not in Registry)
           ├── ordering{} (display order only, never a link)
           └── defaultMode ──► mode │ defaultSet ──► default set

studios.local/obs.json: studio id ──► target name ──► {host, port, password}
```

Hard links (loader `check_references` refuses to boot when broken):
device→zone, device→profile, mode→zones, preset→lights devices,
default-set→devices, studio→defaultMode, studio→defaultSet.
Soft links (resolved at plan/apply time, degrade honestly when missing):
power groups, plugs, OBS scenes/collections/targets, IR color labels,
`mode.screen`/`preset.screen` keys, show and modality device ids.

## 4. File layout

```
config/
  profiles/*.json              shared model specs (public-safe, tracked)
  studios/<id>/                tracked EXAMPLE studios only (fictional data)
  studios.local/               real studios (git-ignored, deployed to the hub)
    plug-map.json              Kasa alias/host/zone/creds map
    obs.json + .obs-key        named OBS endpoints + studio selection
    meta.json                  Meta vendor key (hub-local, optional)
    orchestrator.json          local/free provider endpoints (hub-local)
    link.json                  PC-link audio + WoL settings
    <id>/studio.json           id, label, defaultMode, defaultSet
    <id>/devices.json          device instances (array)
    <id>/zones.json            zones (array)
    <id>/modes.json            modes (array)
    <id>/presets.json          presets (object)
    <id>/defaults.json         default sets (optional object)
    <id>/modalities.json       modalities (optional object)
    <id>/shows.json            shows (optional object)
    <id>/ordering.json         display order (optional object)
    <id>/ir-codes/*.json       captured IR labels per strip
```

Both studio roots load; a studio id may exist in only one.

## 5. Value ranges: single source of truth

Per-model numeric spans live in the profile `spec`
(`intensityRange`, `cctRange`, `hueRange`, `saturationRange`) and
capability gates in profile `capabilities`
(`intensity`/`cct`/`rgb` plus transport flags). The resolver
`backend/app/config/ranges.py` merges spec + capabilities into one
effective range per property per device:

- Profile range present → it wins; source reported as
  `profile:<id>` (e.g. GVM 1500D CCT 3200..5600).
- Profile omits the range → documented generic outer bounds
  (`GENERIC_RANGES`: intensity 0..100, CCT 2500..8500, hue 0..360,
  saturation 0..100); source reported as `generic`.
- Capability missing (`rgb: false`, `cct: false`) → the key is
  unsupported and saves are rejected naming the fixture.
- Devices without a profile pass through with generic bounds and let
  the driver report, mirroring the modes gate.

Per-device value shapes: `page` resolves only for GVM panels (and mock
panels); `transport` (`ble`/`dmx`) resolves only for dual-address
fixtures; screens resolve `theme`/`animation` option lists from the
screen driver (animations strict, themes suggestive).

Consumers of the resolver (nothing keeps its own copy):

- `api/defaults.py` validates default-set light values through
  `check_light_values` — the same spans the editor shows.
- `GET /api/studios/{studio}/ranges` (`api/equipment.py`) serves the
  resolved map; the Equipment Default-sets builder, the Control page
  sliders, and the data map read it.
- `modules/modes.py` enforces the same profile ranges at plan time
  (`LIGHT_RANGES` + `_check_light_ranges`) and clamps orchestrator asks
  to `orchCap` + profile ranges, reporting every clamp.
- The orchestrator manifest (`orchestrator/director.py`) derives each
  light's `controls` block and the `screen_options` lists from the
  resolver — the model sees the same spans the sliders show.

Post-change safety: stored modes/presets/defaults keep their bytes when
a profile range narrows later — the loader checks id references only,
never value spans — so the studio still boots. Only new saves and new
applies face the gate, and UIs render out-of-range stored values as
warnings preserved until edited, never silently clamped on read.

Related per-light baselines (not ranges): device `usual` is the everyday
look the orchestrator reasons from; device `orchCap.intensity` is the
ceiling orchestrator plans may not exceed (near-misses clamp and report;
user content keeps the strict gate; legacy `aiCap` still honored). See
`config/README.md`.

## 6. Lifecycles

Load: `loader.load_all` validates JSON against schemas, checks id
uniqueness and `check_references`, then builds one `Registry` per studio
(`backend/app/registry/store.py`). `registry.effective_device(id)` merges
the instance with its profile's `spec`/`capabilities`/`effects`; every
read API serves effective devices. Broken config yields degraded health,
never a crash (`main.create_app`).

Save: every mutating endpoint validates first, writes atomically
(tmp file + `os.replace`), reloads the studio, re-runs
`check_references`, and swaps the registry — so a save that would break
a link fails with the file untouched. Only `studios.local/` studios are
writable; tracked examples are read-only (422).

Runtime-only state (never on disk): GVM panel looks
(`drivers/gvm_ble._PANEL_LOOKS`), Ninja broadcast cache
(`api/ninja._DRIVERS`), DMX universe buffer (`drivers/dmx_usb` shared
driver), kiosk screen state (`drivers/screen.SCREEN_STATE`), action log,
show/modality run slots (`app.state`). Hardware snapshots
(`POST .../presets/from-hardware`, `api/presets.py`) read exactly this
state — uncommanded fixtures are skipped and named, never guessed.

## 7. Where to look

- Schemas: `backend/app/config/schemas/*.schema.json`
- Loader + reference checks: `backend/app/config/loader.py`
- Ranges: `backend/app/config/ranges.py`
- Registry: `backend/app/registry/store.py`
- Save gates: `api/equipment.py`, `api/defaults.py`,
  `api/orchestrator.py` (`save_preset`), `api/modality.py`,
  `modules/modes.py`, `modules/modality.py`, `modules/shows.py`
- Pages: `backend/app/static/equipment.html` (Default sets),
  `control.html`, `datamap.html` (visual map)
- Tests: `tests/unit/test_default_ranges.py`,
  `test_defaults_api.py`, `test_loader.py`, `test_registry.py`
