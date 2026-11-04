# PC link (audio wire + Wake-on-LAN)

Control from the hub to the main PC with zero ports open there:
magic packets boot it, DTMF tone bursts down a 3.5mm wire act on it.
One cable (hub TX -> main RX) is the whole control path; an optional
second cable (main TX -> hub RX) carries reverse acks in the same
framing. Nothing listens on the network on the main PC either way.

## Cables

| Cable | From | To | Carries |
|---|---|---|---|
| A (required) | hub line-out (or USB dongle) | main-PC mic-in (front panel) | ping, obs |
| B (optional) | main-PC line-out (rear) | hub mic-in (Line In) | ack, obs_ready, obs_fail |

Keep the link jacks off the show-audio path: tones sharing a wire with
the audio bed get jammed (framing prevents false fires, not jamming),
so prefer a dedicated link output on each end.

## Wire protocol

- Transport: 3.5mm analog, mono, any rate 8k-96k; the decoder adapts.
- Framing: DTMF preamble `#` (200ms) + two data digits (120ms each) +
  one check digit, 80-100ms gaps. ~0.8s per command.
- Alphabet: standard DTMF; data digits are `0-9ABCD` (14 symbols, 196
  codes); check = index sum mod 14. Corrupt frames decode to nothing.
- Commands: `ping` (no-op loop test), `obs` (launch the show seat's
  OBS), `obs_bench` (code 06: launch the test-bench seat's OBS),
  `shutdown` (code 05: power the workstation down; listener runs it
  only when `actions.shutdown.shutdown` is true in its local.json,
  with an abort window via `shutdown /a`), `ack` (command received),
  `obs_ready` (OBS websocket up), `obs_fail` (launch attempted,
  websocket never came). Seven of 196 codes used — no capacity concern.
- One wire, two seats: both listeners on a shared PC hear every burst,
  but each `local.json` allow-lists only its own launch command (`obs`
  on the show seat, `obs_bench` on the test-bench seat), so a launch
  tone fires exactly one session — never a double launch. `ping` stays
  shared: an ack from either seat proves the wire path is live, while
  `obs_ready` proves the ADDRESSED seat launched. The boot job's
  `target` (`workstation`/`testbench`) picks the tone; the dashboard
  PC-link card and the bench boot card expose both.
- Multi-frame windows: long captures can hold several bursts (ack then
  obs_ready); the decoder finds each preamble-delimited frame in order.

Implementation: `backend/app/linkproto.py` (stdlib-only generation +
Goertzel decode, shared by the hub player, both listeners, and the tests).

## Hub side

- `GET /api/link/devices` — audio outputs (diagnostic; pick the wire).
- `GET /api/link/inputs` — audio inputs (diagnostic; pick the ack wire).
- `POST /api/link/test` — pure calibration tone for level-setting.
- `POST /api/link/send {command, device?}` — framed burst, fire-and-forget.
- `POST /api/link/listen {seconds, device?}` — capture 0.5-6s on a hub
  input and decode one reverse ack (command None = honest empty).
- `POST /api/link/send-and-wait {command, wait_s?, device?, listen_device?}`
  — send one command, then capture ONE window (ping 8s, obs 60s
  defaults, 2-90s range) and decode every burst in it. Returns the
  first expected ack (`ack` for ping, `obs_ready`/`obs_fail` for obs),
  any other bursts heard, and `confirmed`. Single-capture, so a burst
  straddling a chunk boundary can never be missed.
- `POST /api/link/wol` — magic packet x3 from `studios.local/link.json`
  (`wol_mac`, `audio_out`, `audio_in`; git-ignored, never echoed).
- Bench card "PC link" wraps devices/inputs/test/send/send-and-wait/
  listen in one-click buttons (WoL stays API-only — booting the main
  PC is never casual).

The tone bursts carry no secrets and need none: each wire is trusted by
physical possession, and each end only runs its local allow-list.

## Main-PC side

`tools/pc-link/listener.py` (needs `sounddevice` only): polls one input,
decodes on loud windows, runs allow-listed `{launch, args}` from local
`local.json`, then enforces cooldown + quiet before re-arming so one burst
never fires twice. `--calibrate` is a live level/decode meter (no actions),
`--list-devices` names inputs. Hardening for unattended runs:

- Reconnect: a vanished input (USB re-enumeration) retries every 5s
  instead of dying; Ctrl+C still exits immediately.
- `log_file`: when set, stdout/stderr append to that file (Startup-run
  instances have no console to print to).
- Startup validation warns for allow-list keys that are not real
  commands (they would never fire) and clamps bad `obs_watch` numbers.
- With cable B connected, an `"ack"` block
  (`{"command": "ack", "device": "<link output>"}`) plays one reverse
  burst per accepted command — off unless it names a device.

`watch_obs` actions: after launching, a background thread polls the local
OBS websocket with a stdlib HTTP Upgrade handshake (no password needed —
readiness is proven at the HTTP layer, pre-auth) and reports `obs_ready`
or `obs_fail` down the ack path (or logs it when no ack device is set).
Top-level `obs_watch` sets host/port/timeout/poll (defaults 127.0.0.1 /
4455 / 45s / 2s).

`tools/pc-link/send.py` is the one-shot reverse sender (hand tests, acks
without the listener): `--list-devices` names outputs, `--command ack`
sends one burst, `--test-tone` plays the 1 kHz calibration tone. Both
scripts are frozen-build safe (a compiled exe reads `local.json` next to
itself and imports the bundled protocol module).

Setup checklist (jack separation, gain, ground loops, WoL target config)
lives in `tools/pc-link/README.md`.

## Scripted flow (demo boot order)

Kasa plug on → wait ~25s (NIC/link settle) → `wol` → wait for Windows +
listener (~60-90s) → `send-and-wait obs` (one call: launches OBS, hears
`obs_ready`) → hub's own OBS poll double-confirms → apply the mode.
Fixed waits plus one honest confirmation, filmable, zero PC-side config
beyond the README checklist.

## Failure behavior

- No backend / unknown device / unconfigured MAC: 422 with the fix.
- Corrupt tone, noise, silence: decode yields nothing; both ends stay idle.
- Unknown-but-valid codes: decoded, logged, ignored unless recognized.
- Jammed command (loud line noise over the burst): lost, not misfired —
  the operator retries. Prefer dedicated link jacks over sharing show audio.
- Ack silence (no cable B, wrong gain, wrong jack): the forward command
  still ran — the hub just can't hear the confirmation. `send-and-wait`
  reports `confirmed: false` with everything it did hear; confirm through
  normal channels (OBS websocket) instead.
- `obs_fail`: OBS never opened its websocket in the watch window (slow
  boot, wrong port, websocket server disabled) — check the main PC, retry.
