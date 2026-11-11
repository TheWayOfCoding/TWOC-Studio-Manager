# Config layout

- `profiles/` — shared model profiles (generic fixture specs, public-safe).
- `studios/` — tracked **example** studios only (fictional data for tests/demos).
- `studios.local/` — your real studios. **Git-ignored, never committed.**
  Same file layout as `studios/`: `studio.json`, `devices.json`, `zones.json`,
  `modes.json`, `presets.json`.

The loader reads both studio roots; a duplicate studio id across roots is an
error. Keep personal hardware details in `studios.local/` only.

## ordering.json (per studio, studios.local/)

`studios.local/<id>/ordering.json` holds custom list order as id arrays:
`{"zones": [...], "devices": [...], "modes": [...], "presets": [...],
"plugs": [...], "panels": [...], "modalities": [...]}`.
Edited on the `/ordering` page (drag/drop blocks, one-click A–Z / Z–A per
collection). The file is tiny, loads once into the registry, and saves
atomically (tmp + replace) — no database, no per-request disk reads. Ids
missing from the file append alphabetically by id; unknown ids are ignored
on read and refused on save. Only local studios are writable. Zones list
their device collections from the same order (`devices` array per zone in
`GET /api/studios/<id>/zones`); `panels` orders the dashboard cards
client-side by `data-panel` id.

## plug-map.json (studios.local/)

`studios.local/plug-map.json` maps Kasa plug ids to aliases + fallback hosts
for modes power:

```json
{"plugs": [{"plug": "plug-1", "alias": "Studio Plug 1", "host": "192.0.2.10", "zone": "pc"}]}
```

Plugs are identified by **alias**: at modes-build time each alias resolves to
its live LAN host via cached discovery (one broadcast refreshes all, 5-min
TTL), so DHCP drift is harmless and the map never goes stale. The stored
`host` is offline fallback only. Entries are claimed by zone, so a studio
only touches its own plugs. Plugs on newer account-locked firmware may add
`"username"`/`"password"` per entry; modes passes them to the driver, and
they are never echoed in responses. The bench discovery card shows live
hosts; the map itself stays hand-maintained for alias/zone/creds.

## obs.json (studios.local/)

`studios.local/obs.json` holds named OBS WebSocket endpoints plus each
studio's selected default (absent file or entry = honest "not wired"
skip). Manage it on the Administration → OBS targets page (add/update/
delete, per-target Test, per-studio defaults); the dashboard OBS card
can also switch a studio's target. Hand-editing works too:

```json
{"targets": {"main": {"host": "192.0.2.5", "port": 4455, "password": "enc:..."}},
 "studios": {"home-studio": {"target": "main"}}}
```

`port` defaults to 4455; `password: null` means the OBS has no WebSocket
password. Passwords rest encrypted (Fernet key in
`studios.local/.obs-key`, created on first use) and no API ever echoes
one — Test proves the stored credentials without revealing them. Both
files are git-ignored (all of `studios.local/` is) and ship to the hub
with the normal deploy, so targets entered on either box work on both.
Old single-endpoint files (`{"studios": {id: {"host", ...}}}`) still
resolve, and migrate to a named target on the first admin save.

Enable OBS WebSocket in each OBS install (Tools → WebSocket Server
Settings) and confirm the scene names in `modes.json` match the OBS
collection exactly.

## meta.json (studios.local/)

`studios.local/meta.json` holds the dev.meta.ai key for the AI director's
`meta` provider (absent file = honest "not configured" 503):

```json
{"api_key": "...", "model": "muse-spark-1.3-contributor"}
```

`model` defaults to the contributor build when omitted. The key is sent to
api.meta.ai only, never logged or echoed in responses. Create one key per
use (name the hub key for the hub); it ships to the hub with the normal
`studios.local` deploy files and never commits.

## link.json (studios.local/)

`studios.local/link.json` holds the PC-link settings (hub side, git-ignored):

```json
{"wol_mac": "AA:BB:CC:DD:EE:FF", "audio_out": "Line Out", "audio_in": "Line In"}
```

`wol_mac` is the main PC's wired NIC address (`ipconfig /all` on that PC —
paste it into this file, never into chat); `/api/link/wol` wakes it without
opening anything on that machine. `audio_out` matches a hub output by name
substring (see `/api/link/devices`); `audio_in` matches a hub input for the
reverse ack wire (see `/api/link/inputs`). Per-request `device` /
`listen_device` override both. Neither value is ever echoed in responses.

## shows.json (per studio in studios.local/)

`<studio>/shows.json` holds presentation sequences: `{"<id>": {"label", "steps":
[{lights?, screen?, scenes?, hold_s?, caption?, label?, fade_s?}]}}`. Each step
applies through the modes gate, narrates the applied result to the studio
screens (caption + color wash derived from what actually applied), then holds.
`scenes` steps need one run-time confirmation; stop jumps to the final step so
a show always lands somewhere sane. Power/OBS stay out of shows for now.

## modalities.json (per studio)

`<studio>/modalities.json` holds the smart collections the dashboard runs:
`{"<id>": {"label", "kind", "beats": [...]}}`. `kind` is one of
`mode` / `preset` / `show` / `custom` (default `custom`); each beat carries
`label`, `hold_s`, `stay`, and an `actions` object using the same keys as a
mode (`powerOn`, `lights`, `screen`, `obsScene`, ...). Single-beat
`mode`/`preset` modalities behave like one-click looks; multi-beat `show`
modalities step through with holds; `stop-to-final` always lands somewhere
sane. Edited on the `/modality` page (forms-first, JSON look fields) with
run/stop/status API beside the modes endpoints. The modes/presets/shows
engine underneath is unchanged — modalities are the demo-facing layer.

## defaults.json (per studio) + defaultSet

`<studio>/defaults.json` holds named **default sets**: per-device value
snapshots plus Kasa plug states, one of which a studio assigns as its
default via `studio.json` → `defaultSet`:

```json
{"default": {
  "label": "Default",
  "devices": {"demo-key": {"intensity": 50, "cct": 4600}},
  "plugs": {"plug-1": {"on": true}}}}
```

Set ids are system-generated from the label (slug + numeric suffix on
collision) and stay fixed once created; the label is the display name.
Light values use preset vocabulary (`power`, `intensity`, `cct`,
`hue`, `saturation`, `page`, `transport`); IR strips take `power` +
`color`; screens take `theme` + `text` + `animation` (theme/animation
options served from the screen driver); other controllable types take
flat values. `page` is GVM-panel-only; `transport` (`ble`/`dmx`) saves
only on dual-address fixtures and reset applies it first, routing the
look through the picked interface. Numeric spans come from each
fixture's profile spec via the shared ranges resolver
(`backend/app/config/ranges.py`, served at
`GET /api/studios/<id>/ranges`); profiles that omit a range fall back
to the documented generic outer bounds. Entries must name known
controllable devices (validated on save against those effective spans,
plus capability checks: no hue for RGB-less fixtures, no CCT for
fixed-temp ones). The file is optional — studios without one simply
have no defaults. Managed on the Equipment page (Default sets card:
dropdown builder with range-aware inputs + quick-assign, JSON fallback,
create/edit/delete + assign/clear); the assigned set seeds the Control
page and powers one-click reset per asset, per plug, and
everything-at-once. Control inputs resolve session value → studio
default → hardcoded, CCT sliders render each fixture's own span, and
reset transmits the saved levels exactly (reset-all always allows
bright).

## Per-light baselines: usual + aiCap

Each light device carries its normal everyday look in `usual`:

```json
{"id": "ninja-400", "usual": {"intensity": 2, "cct": 5600}, "aiCap": {"intensity": 10}}
```

`usual` is the baseline the orchestrators reason from: the AI manifest
includes it verbatim, and the director prompt defines it as normal
lighting (moody/cozy asks go below it, neutral asks stay near it).
Keep it truthful to the live everyday level — the key light's entry
matters most, since every relative look keys off it.

`aiCap` is the ceiling the AI may not exceed (near-misses clamp and are
reported; user modes/presets keep the strict gate instead). `usual` may
itself sit above `aiCap` when the everyday level is brighter than AI
should ever command — the clamp still applies.

Turning lights off: `intensity` 0 means dark on every family (DMX uses
0; GVM panels also accept `"power": "off"`). The AI may direct 0 when
the request wants a light off; the critique pass flags it so a human
can tell blackout from mistake.

## Ninja BLE lights (driverAddress)

Devices with `"driver": "ninja"` address Weeylite/Viltrox fixtures over the
broadcast protocol in `docs/ninja-ble.md` (no pairing; the hub advertises):

```json
{"id": "ninja-10", "driver": "ninja", "driverAddress": {"channel": 3, "group": "A"}}
```

`channel` is 1-19, `group` is A-F. The bench also drives raw channel/group
pairs with no config (direct mode). One driver instance is cached per
address so the merged look and broadcast counter continue across calls.
Dual-interface fixtures carry both halves plus a `transport` pick:

```json
{"id": "ninja-400", "driver": "dmx",
 "driverAddress": {"channel": 3, "group": "B", "dmx": {"dimmer": 1, "cct": 2}},
 "transport": "dmx"}
```

Fixed-temp fixtures take a dimmer-only map (`{"dimmer": 20}`, no `cct`).
Addresses edit on the Equipment page (device card Address section:
validated per driver family, ≥1 half must stay) or by hand; every
surface follows the `transport` pick, and default sets can store it.
