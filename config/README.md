# Config layout

- `profiles/` — shared model profiles (generic fixture specs, public-safe).
- `studios/` — tracked **example** studios only (fictional data for tests/demos).
- `studios.local/` — your real studios. **Git-ignored, never committed.**
  Same file layout as `studios/`: `studio.json`, `devices.json`, `zones.json`,
  `modes.json`, `presets.json`.

The loader reads both studio roots; a duplicate studio id across roots is an
error. Keep personal hardware details in `studios.local/` only.

## plug-map.json (studios.local/)

`studios.local/plug-map.json` maps Kasa plug ids to LAN hosts for modes power:

```json
{"plugs": [{"plug": "plug-1", "host": "192.0.2.10", "zone": "pc"}]}
```

Entries are claimed by zone, so a studio only touches its own plugs. Plugs on
newer account-locked firmware may add `"username"`/`"password"` per entry;
modes passes them to the driver, and they are never echoed in responses.
The bench discovery card finds current hosts; the map itself is hand-maintained
— re-discover after a stale host, then edit the host here and redeploy.

## obs.json (studios.local/)

`studios.local/obs.json` maps studio ids to their OBS WebSocket endpoint for
mode scene switches (absent file or entry = honest "not wired" skip):

```json
{"studios": {"home-studio": {"host": "192.0.2.5", "port": 4455, "password": "..."}}}
```

`port` defaults to 4455; omit `password` when OBS has no WebSocket password.
Enable OBS WebSocket in OBS (Tools → WebSocket Server Settings) and confirm
the scene names in `modes.json` match the OBS collection exactly.

## shows.json (per studio in studios.local/)

`<studio>/shows.json` holds presentation sequences: `{"<id>": {"label", "steps":
[{lights?, screen?, scenes?, hold_s?, caption?, label?, fade_s?}]}}`. Each step
applies through the modes gate, narrates the applied result to the studio
screens (caption + color wash derived from what actually applied), then holds.
`scenes` steps need one run-time confirmation; stop jumps to the final step so
a show always lands somewhere sane. Power/OBS stay out of shows for now.
