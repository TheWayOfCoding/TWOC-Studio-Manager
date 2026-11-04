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
`{"zones": [...], "devices": [...], "modes": [...], "presets": [...], "plugs": [...]}`.
Edited on the `/ordering` page (drag/drop blocks, one-click A–Z / Z–A per
collection). The file is tiny, loads once into the registry, and saves
atomically (tmp + replace) — no database, no per-request disk reads. Ids
missing from the file append alphabetically by id; unknown ids are ignored
on read and refused on save. Only local studios are writable. Zones list
their device collections from the same order (`devices` array per zone in
`GET /api/studios/<id>/zones`).

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

`studios.local/obs.json` maps studio ids to their OBS WebSocket endpoint for
mode scene switches (absent file or entry = honest "not wired" skip):

```json
{"studios": {"home-studio": {"host": "192.0.2.5", "port": 4455, "password": "..."}}}
```

`port` defaults to 4455; omit `password` when OBS has no WebSocket password.
Enable OBS WebSocket in OBS (Tools → WebSocket Server Settings) and confirm
the scene names in `modes.json` match the OBS collection exactly.

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
