# Data Dictionary: TWOC Studio Hub

Field-by-field companion to [the DSD](dsd.md). Keys and links use `id`
everywhere; `name`/`label` are display-only. Schemas:
`backend/app/config/schemas/`. Conventions below: Req = required by schema
or code; Key = PK (primary key), FK (foreign key), Soft (resolved at
plan/apply, degrades honestly), — (plain attribute).

## studio.json (one per studio)

| Field | Type | Req | Key | Meaning, example |
|---|---|---|---|---|
| id | string `^[a-z0-9][a-z0-9_-]*$` | yes | PK | Studio key, matches directory name. `home-studio` |
| label | string 1.. | yes | — | Display name. `Home Studio` |
| description | string | no | — | Free text shown in pickers. |
| defaultMode | string | no | FK→mode | Mode applied on boot/select. `work` |
| defaultSet | string | no | FK→default set | Default set seeding Control + resets. `default` |

## devices.json (array, one per studio)

Identity (`id`, `type`, `powerPlug`, `driver`) is read-only after creation;
descriptive fields edit via `PUT .../devices/{id}/properties`.

| Field | Type | Req | Key | Meaning, example |
|---|---|---|---|---|
| id | string `^[a-z0-9][a-z0-9_-]*$` | yes | PK | Stable key; every preset/mode/default references it. `gvm-pc` |
| name | string 1.. | yes | — | Display name, freely editable. `Workstation GVM 1500D` |
| type | enum-ish string | yes | — | `light`, `lightstrip`, `screen`, `camera`, `computer`, `recorder` |
| zone | string | yes | FK→zone | Exactly one zone; moves via `PUT .../zone`. `hands-on` |
| profile | string | no | FK→profile | Model spec id. `gvm-1500d` |
| powerGroup | string/null | no | Soft→power group | Mains group for power plans. `hands-on` |
| powerPlug | string/null | no | Soft→plug | Kasa plug id in plug-map. `plug-4` |
| powerOffPolicy | `auto`/`manual-only`/`never` | no | — | All-off behavior; default `auto`. |
| bootOrder | int ≥ 0 | no | — | Power-up sequencing slot. |
| estWatts | number ≥ 0 | no | — | Planning estimate. `75` |
| controllable | bool | no | — | False = hand-operated, skipped by plans. Default true |
| driver | string/null | no | — | Family: `gvm`, `ninja`, `dmx`, `ir`, `kasa`, `screen`, `mock`, null |
| driverAddress | object | no | — | Driver-linked address, editable via `PUT .../address` (validated per family). `gvm`: `{ble: MAC}`. `ninja`/`dmx`: `{channel 1–19, group A–F}` and/or `{dmx: {dimmer 1–512, cct?}}` — BLE arrives as a pair or not at all, CCT omits on fixed-temp single-channel maps, ≥1 half must stay. `kasa`: `{plug}` (read-only: plug wiring is identity). Dropping the half the `transport` pick points at moves the pick and reports it. |
| transport | `ble`/`dmx` | no | — | Dual-protocol pick; every surface follows it. |
| position | string | no | — | Human placement note. |
| coords | `{x,y,z}` numbers | no | — | Meters in studio space. |
| aimedAt | string | no | — | Aim target. `subject` |
| distanceToSubjectM | number ≥ 0 | no | — | Throw distance for AI reasoning. |
| modifiers | array | no | — | Shapers: `{type, size, diffusionLayers, notes}`. |
| role | string | no | — | Lighting role: `key`, `background`, `mood`, `room`. |
| usual | object | no | — | Everyday look baseline for AI (`intensity`, `cct`, …). |
| aiCap | object | no | — | AI ceiling, e.g. `{intensity: 30}`; user content unaffected. |
| exposure | object | no | — | Cameras: `{iso, shutter, aperture, whiteBalanceK}`. |
| details | object | no | — | Freeform `{label: text/number}` datapoints; Equipment page. |
| notes | string | no | — | Operator notes. |

Effective device (API view, `registry.effective_device`): instance plus
`spec`, `capabilities`, `effects` merged from the profile (or empty when
no profile).

## profiles/*.json (shared model specs)

| Field | Type | Req | Key | Meaning, example |
|---|---|---|---|---|
| id | string `^[a-z0-9][a-z0-9_-]*$` | yes | PK | Model key. `viltrox-ninja-400` |
| kind | string | yes | — | Model family. `light`, `lightstrip` |
| spec | object | no | — | Model truth: `maxWatts`, `*Range` pairs, `fixedTemp`, `colorTempK`, `cri`, … |
| spec.intensityRange | `[min,max]` | no | — | Percent span, usually `[0,100]`. |
| spec.cctRange | `[min,max]` | no | — | Kelvin span, e.g. `[3200,5600]`. |
| spec.hueRange | `[min,max]` | no | — | Degrees, usually `[0,360]`. |
| spec.saturationRange | `[min,max]` | no | — | Percent, usually `[0,100]`. |
| capabilities | object | no | — | `intensity`/`cct`/`rgb` bools + `ble`/`dmx`/`ir` transports. |
| effects | array | no | — | `{name, photoRisk: none/mild/intense}` scene table. |
| notes | string | no | — | Capture/bench provenance. |

## zones.json (array, one per studio)

| Field | Type | Req | Key | Meaning, example |
|---|---|---|---|---|
| id | string `^[a-z0-9][a-z0-9_-]*$` | yes | PK | `hands-on` |
| label | string 1.. | yes | — | `Hands-On` |
| description | string | no | — | Free text. |
| powerGroups | string[] | no | Soft→groups | Groups switched with this zone. |
| autoStartObs | bool | no | — | Zone power-on also kicks the OBS boot job. |
| dependsOn | string | no | Soft→zone | Ordering hint (hands-on-screens → hands-on). |

## modes.json (array, one per studio)

| Field | Type | Req | Key | Meaning, example |
|---|---|---|---|---|
| id | string `^[a-z0-9][a-z0-9_-]*$` | yes | PK | `pc_stream` |
| label | string 1.. | yes | — | `PC Stream` |
| zones | string[] | no | FK→zones | Zones this mode covers. |
| intent | `live`/`offline` | no | — | Dashboard intent badge. |
| powerOn/powerOff | string[] | no | Soft→groups | Groups to switch (validated at plan). |
| lights | `{device: look}` | no | Soft→devices | Generic looks (`intensity`, `cct`, `hue`, `saturation`, `power`, `page`). |
| screen | `{screen: spec}` | no | Soft→screens | `theme`/`text`/`animation`/`wash` per screen. |
| obsScene | string/null | no | Soft→OBS | Must match the OBS collection exactly. |
| obsCollection | string | no | Soft→OBS | Collection to cut to. |
| autoStartObs | bool | no | — | Boot the PC first, then cut scene. |
| toggles | object | no | — | Dashboard toggle states (`backCamera`). |
| fade_s | number | no | — | Default dwell seconds per light step. |

## presets.json (object, one per studio)

Key = preset id (`[A-Za-z0-9][A-Za-z0-9_-]{0,63}`). Lights/screen only —
presets never touch power or OBS.

| Field | Type | Req | Key | Meaning, example |
|---|---|---|---|---|
| label | string 1.. | yes | — | `Late Night` |
| lights | `{device: look}` | no | FK→devices | Keys checked at load; values gated by `ModesModule` on save. |
| screen | `{screen: spec}` | no | Soft→screens | Theme/text/wash per screen. |
| fade_s | number | no | — | Default dwell for the apply. |

Light look keys: `power` (`on`/`off`), `intensity` 0..100, `cct` Kelvin,
`hue` 0..360, `saturation` 0..100, `page` (`cct`/`hsi`). Spans clamp to the
profile via the ranges resolver; scenes are bench-only (automation refuses
them without an explicit show confirmation).

## defaults.json (optional object, one per studio)

Named equipment-value snapshots. Key = set id (preset pattern + reserved
`assign`). Managed on the Equipment page; the studio's `defaultSet` seeds
Control inputs and one-click reset.

| Field | Type | Req | Key | Meaning, example |
|---|---|---|---|---|
| label | string 1.. | yes | — | `Evening` |
| devices | `{device: values}` | no | FK→devices | Non-empty per device; unknown/uncontrollable rejected on save. |
| plugs | `{plug: {on}}` | no | Soft→plugs | `{"plug-1": {"on": true}}`; existence NOT checked. |

Device value shapes by family: lights take the preset look keys above;
IR strips take `power` + `color` (saved code label); screens take `theme` +
`text` + `animation` (each ≤ 500 chars); other controllable types take flat
text/number/bool values.

## modalities.json (optional object, one per studio)

Smart demo-facing collections over the unchanged modes/presets/shows engine.

| Field | Type | Req | Key | Meaning, example |
|---|---|---|---|---|
| label | string 1.. | yes | — | `PC Stream` |
| kind | `mode`/`preset`/`show`/`custom` | no | — | One-click vs stepping behavior; default `custom`. |
| notes | string | no | — | Operator notes. |
| beats | array 1.. | yes | — | Ordered steps; the last beat stays active. |

Beat: `{label, hold_s 0..3600, stay, power_delay_s 0..5, fade_s 0..60,
actions}`. Actions: `powerOn`/`powerOff` groups, `lights` device looks,
`zoneLights` (zone → look expanded to its controllable lights),
`scenes` (device → scene index), `screen`, `obsScene`, `obsCollection`.
`stay` is rejected on non-final beats.

## shows.json (optional object, one per studio)

Presentation sequences. Read per request (`modules/shows.py`), not held in
the Registry; each step applies through the modes gate.

| Field | Type | Req | Key | Meaning, example |
|---|---|---|---|---|
| label | string 1.. | yes | — | `GVM Showcase` |
| steps | array | yes | — | `{lights?, screen?, scenes?, hold_s?, caption?, label?, fade_s?}` |

`scenes` steps need one run-time confirmation; stop jumps to the final step.
Power and OBS stay out of shows.

## ordering.json (optional object, one per studio)

Display order only — never a link. `{zones[], devices[], modes[],
presets[], plugs[], panels[], modalities[]}` of ids. Missing ids append
alphabetically; unknown ids are ignored on read, refused on save. Edited on
`/ordering`.

## Hub-level files (studios.local/)

plug-map.json: `{plugs: [{plug, alias, host, zone, username?, password?}]}`.
`plug` is the key; `alias` resolves to the live LAN host at plan time (5-min
discovery TTL, DHCP-proof); `host` is offline fallback; `zone` claims the
entry for one studio area. Credentials never echo in responses.

obs.json: `{targets: {name: {host, port=4455, password}},
studios: {studio: {target}}}`. Target names are the keys; absent file/entry
means honestly "not wired". Passwords rest Fernet-encrypted (`.obs-key`);
no API echoes one. Managed on `/obs`.

meta.json: `{api_key, model?}` for the AI `meta` provider; absent means 503.

link.json: `{wol_mac, audio_out, audio_in}` for the PC link; audio matches
by device-name substring at request time (see `api/link.py`).

ir-codes/&lt;device&gt;.json: `{label: {code: [b0,b1,b2], works, notes}}`
captured strip labels; replayed by `IrModule`.

## Runtime-only state (memory, never disk)

| Store | Code | Content |
|---|---|---|
| Panel looks | `drivers/gvm_ble._PANEL_LOOKS` | Last-sent BLE echoes per MAC (snapshot source). |
| Ninja cache | `api/ninja._DRIVERS` | Merged look + counter per channel/group. |
| Universe buffer | `drivers/dmx_usb` shared driver | 512 levels + `emitted`/`blackout` flags. |
| Screen state | `drivers/screen.SCREEN_STATE` | Theme/text/wash/audio per screen. |
| Action log | `app.state.action_log` | Per-studio apply history; undo replays the tail. |
| Run slots | `app.state.show_runs`, `modality_runs` | At most one run per studio each. |
| Registries | `app.state.registries` | Validated per-studio view (rebuilt on every save). |

Snapshots (`POST .../presets/from-hardware`) read exactly the first four
stores: fixtures never report back, so anything uncommanded since hub start
is skipped and named, never guessed.

