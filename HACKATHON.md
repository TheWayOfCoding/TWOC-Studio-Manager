# TWOC Studio Hub — Hackathon Showcase

One-click studio director for content creators: walk in, tap one button,
and the room powers up, lights set, OBS cuts to your scene, and the
backdrop screens join in. An AI lighting director takes plain-English
prompts ("chill gaming vibes") and paints the whole rig to match.

Built for the hackathon demo: a real studio, real hardware, live on camera.

## The demo in 60 seconds

1. **All Off** — the room goes dark and quiet, every plug cut except the
   protected ones (the PC stays up; the bench strip holds because a camera
   rig lives on it).
2. **Hands-On mode** — one tap: plugs click on, the bench lights fade to
   their look, OBS cuts to the close-up scene, the backdrop screen themes
   itself. ~10 seconds, relay-speed.
3. **Boot + OBS** — the hub pings the main PC *over a 3.5mm audio cable*
   using touch-tone codes, the PC answers, launches portable OBS, and the
   hub cuts it to the presentation collection and scene. No network ports
   open on the show PC at all.
4. **AI look** — "warm dim interview key" → the AI director aims every
   light and screen in the room, with one critique pass and honest
   per-fixture reporting.
5. **Undo** — one button walks the whole room back to the previous look.

## What it controls

**Power (Kasa plugs, alias-based).** Six smart plugs across four zones
(PC area, hands-on bench, mood, back). Plugs are found by alias with
cached discovery, so DHCP drift can never strand a zone. First-contact
handshake flakiness gets a silent single-connect heal. Manual-only
protection marks the plugs a power cut would endanger — All Off skips
them by name unless you explicitly tick "also cut protected zones."

**Lights — four families, one language.**

- *GVM panels over BLE*: brightness, color temperature, full HSI color,
  and scenes. The HSI wire format was reverse-engineered from phone-app
  Bluetooth captures during the build (hue = degrees/5, saturation =
  percent, verified live on-panel).
- *Viltrox Ninja fixtures over BLE broadcast*: the vendor app's protocol
  was cracked from a live HCI snoop — connectionless extended
  advertising, 27-byte frames, per-address counters. The hub replays
  byte-exact frames through Windows' BLE advertiser and moves real
  fixtures over the air. Soft power on/off included.
- *USB-DMX*: real DMX fades with wall-clock pacing, per-fixture power and
  color channels, blackout that preserves the look buffer. Survived a
  saga: the XLR-to-RJ45 adapter turned out to be a proprietary pinout,
  proven with a multimeter, fixed with a custom-crimped CAT5.
- *IR shelf strip*: all 24 remote buttons captured and replayed over a
  USB-serial IR blaster, with a camera-calibrated color table (a fixed-
  exposure rig measured what each button *actually* looks like — the
  strip is red-weak, so "white" reads teal) and human rename labels.

**OBS.** Full websocket control: status, scene lists, scene-collection
lists, switching with a safe-scene fallback, guarded stream start/stop.
The dashboard carries an OBS card with a live status dot and a
collection picker.

**Backdrop screens.** Kiosk pages with pushed themes and text, a
synthesized WebAudio bed, self-reload across hub restarts, and a Tauri
shell with double-ESC exit for the glass.

**Presentations (shows).** Captioned multi-step sequences with narration
wash, stop-to-final behavior, and per-step result reporting — filmable
cue-to-cue runs.

**The audio wire (PC-link).** The party trick with a purpose: the hub
talks to the main PC down 3.5mm audio using DTMF touch-tones — the
1960s AT&T phone-dialing code, preamble + data + check digit, ~0.8s per
command. `ping`/`obs` go one way; `ack`/`obs_ready`/`obs_fail` come
back on the second cable. A boot job pings until the PC answers,
launches portable OBS, waits for its websocket, then cuts collection
and scene — end to end in about 10 seconds. Zero ports open on the PC;
the wires are the whole channel.

**AI director.** Prompt + target + scope (all/zones/lights) over a mock
provider or a live model, with per-fixture normal baselines ("usual"
looks the room returns to), power caps per fixture, one critique pass,
voice input on the dashboard, and save-as-preset. Live-proven on real
hardware: one prompt aimed two GVM panels, a Ninja key, and a backdrop
screen in a single apply.

**Honesty everywhere.** Every apply degrades by name — offline plugs,
unwired lights, and missing scenes are reported per device, never
silent, never a 500. Snapshot-from-hardware saves the live rig as a
preset. Undo + action log replays the previous state through the
validated path.

## Architecture

```
  +----------------+   HTTPS/HTTP LAN   +------------------+
  |  Dashboard /   |<------------------>|  Hub (FastAPI)   |
  |  Bench / Kiosk |                    |  modes, AI, OBS, |
  +----------------+                    |  screens, shows  |
                                        +--+---+---+---+--+
                                           |   |   |   |
                        Kasa LAN    BLE ---+   |   |   +--- 3.5mm DTMF
                        DMX USB -------+   |   |       +--- WoL magic pkt
                        IR serial --+   |   |   |
                                        v   v   v   v
                                      plugs, panels, fixtures,
                                      strip, screens, main PC
```

One Python service, JSON-configured studios, ~480-test suite with
hardware and socket-free tiers, dual HTTPS/HTTP serving, watchdog task,
self-signed TLS. Deploys over the LAN with plug-map clobber guards.

## War stories (all survived)

- **The silent DMX chain**: two fixtures, zero response, weeks of
  suspects — ended at a multimeter proving the adapter's pinout was
  proprietary, not ESTA. Custom-crimped CAT5, both fixtures answered.
- **The deaf hub**: the audio listener heard nothing for hours — the
  chunked audio reads returned instant zeros on that stack. Rewrote
  capture callback-driven (like the PC listener) and the boot job went
  from 73s-and-deaf to 10s-and-ready.
- **The kiosk that wouldn't die**: a fullscreen window surviving ALT-F4
  and swallowing the desktop — turned out to be a whole second kiosk
  process from a stale scheduled task.
- **The blue white**: the IR strip's "white" measured as teal on a
  calibrated camera — the red channel peaks at a third of blue's
  brightness. The whole color table is measured truth, not remote
  labels.
- **The MME void**: an audio endpoint that opened fine and played into
  nothing — fixed by addressing the exact full WASAPI endpoint name.

## Status

Live on the hub host, demo-ready. Every light family drives through
bench, modes, presets, and AI; OBS boot-over-audio runs in ~10s; shows,
undo, and snapshot all land. Remaining threads: zone/plug detail
rework, presentation structure, and the show-user account transition —
all queued, none blocking the demo.
