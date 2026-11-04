# TWOC Studio Hub — Hackathon Showcase

One-click studio director for content creators: walk in, tap one button,
and the room powers up, lights set, OBS cuts to your scene, and the
backdrop screens join in. An AI lighting director takes plain-English
prompts ("chill gaming vibes") and paints the whole rig to match.

Built for the hackathon demo: a real studio, real hardware, live on camera —
with a submission video due at the end of the crunch.

## How the build went

**Week 1: power and the plan.** The hub started as plug control: six Kasa
smart plugs across four studio zones, driven through a web bench. The
plugs taught the first lessons — freshly-booted plugs fumble their first
handshake (fixed with a silent single-connect heal), and DHCP drift kept
stranding zones until control went alias-based with cached discovery.
Relays clicked at full speed after the staging pauses were ripped out.

**Cracking the GVM panels.** The LED panels speak Bluetooth LE to a phone
app, with no public protocol. So the build turned a phone into a lab
instrument: HCI snoops captured the app's Bluetooth traffic while scripted
taps walked the UI, and the frames fell out one field at a time — CCT,
brightness, scenes, then the full HSI color page (hue in 5-degree steps,
saturation in percent, verified live when a panel showed dim blue on
command). The hub now speaks native GVM BLE with no phone in the loop.

**The DMX saga.** Two Viltrox fixtures on a USB-DMX chain answered
absolutely nothing — not a flicker. Every suspect was eliminated in turn
(channels, modes, cables, ports) until a multimeter proved the no-name
XLR-to-RJ45 adapter used a proprietary pinout, landing data on the
fixtures' ground pins. A sacrificial CAT5 was re-crimped to a custom
crossover, both fixtures answered, and follow-up work (USB latency,
frame timing, primary-port discipline) took the chain from flickery to
clean. The fixtures' CCT range was then mapped end to end on real
hardware.

**Cracking the Ninja protocol over the air.** The Viltrox fixtures also
take Bluetooth from a vendor app — connectionless broadcast advertising,
no pairing at all. The phone-as-lab-instrument playbook ran again, this
time harder: the Android build only wrote header-only snoop files, so
capture moved to a live `btsnoop_net` stream forwarded over ADB while
automated taps (timed ~1.5s on a live key light, with app-foreground
guards and drawer-reopen handling) walked every screen. Out came the
27-byte frame: channel/group addressing, mirrored brightness bytes, CCT,
presets, per-broadcast counters, ten FX scenes. The hub replays
byte-exact frames through the Windows BLE advertiser — and the fixtures
accepted them, proving no scan-response placement was needed. All three
fixtures moved to Bluetooth control first, gaining soft power the DMX
path never had; the Ninja 400 later returned to its wire-verified DMX
path (DMX-only today) while the 10/20 stayed on Bluetooth.

**Calibrating the IR strip with a camera.** The shelf strip's 24-button
remote was captured in full over a USB-serial IR blaster — then a fixed-
exposure camera rig measured what each button *actually* looks like.
The strip is red-weak (red peaks at a third of blue), so "white" reads
teal and half the buttons land in the wrong color family. The result is
a measured-truth color table with human rename labels, and every
animated mode eyeballed and graded for intensity.

**The AI director goes live.** With every family under hub control, the
AI layer landed: prompt + target + scope over a live model, per-fixture
normal baselines, power caps, one critique pass, voice input. The first
hardware spike — one cozy prompt — aimed two panels, a key light, and a
backdrop screen in a single apply, all devices reporting ready.

**Talking to the PC down a wire.** The show PC needed to boot OBS on
command with zero network ports open. The answer: DTMF touch-tones —
the 1960s phone-dialing code — down a 3.5mm audio cable, with reverse
acks on a second cable. Bring-up had its own gauntlet: an audio endpoint
that opened fine but played into the void (fixed with exact WASAPI
names), then a hub listener that heard nothing because chunked reads
returned zeros on that stack (rewrote capture callback-driven). The boot
job went from 73 seconds and deaf to ~10 seconds end to end: ping, ack,
launch, websocket-ready, collection cut, scene cut.

**The kiosk wars.** The backdrop glass runs a fullscreen shell that
survived ALT-F4 and swallowed a whole desktop (a stale scheduled task
running a second kiosk), then refused its double-ESC exit (a capability
file missing remote-URL scope for the close plugin). Both fixed, both
contract-tested, both verified on the glass.

**Honesty as a feature.** Throughout, every failure mode was made
honest: offline plugs, unwired lights, and missing scenes degrade by
name; dead providers return 502s with the upstream message; snapshots
name every skipped device. Nothing in the room ever fails silent.

**Deadline day.** The last session shipped per-seat OBS start (one audio
wire addressing the show seat and the test-bench seat independently),
a zone-move editor on the Equipment page, and the last hardening pass —
then every plug went live with its real equipment attached and the suite
closed at 546 passed, 8 skipped.

## What it controls

**Power (Kasa plugs, alias-based).** Six official plugs (Studio Plug
1–6) in zone-importance order: Workstation Computer, Content Creation,
Background, Hands-On, Hands-On Screens, Ambient Mood — plus a Hub
Manager zone on its own PC (no plug) and a Room zone of fixed lights.
Plugs found by alias with cached discovery — DHCP drift can't strand a
zone — and every page primes discovery on load so switches answer
fast. First-contact handshake flakiness gets a silent heal.
Manual-only protection marks the plugs a power cut would endanger;
All Off skips them by name unless explicitly overridden.

**Lights — four families, one language.** GVM panels over BLE
(brightness/CCT/HSI/scenes, protocol reverse-engineered from app
captures); Ninja 400 over USB-DMX (dimmer + CCT, wall-clock pacing,
buffer-preserving blackout); Ninja 10/20 over BLE broadcast (byte-exact
replayed frames, soft power); IR shelf strip with a camera-measured
color table. Every family on the bench, in modes and modalities, and
under AI.

**OBS.** Full websocket control: status, scenes, scene collections,
safe-fallback switching, guarded streaming. The dashboard carries a live
OBS card with a collection picker plus a per-seat start card, and the
audio wire cold-boots portable OBS into the right collection and scene
in ~10 seconds — show seat and test-bench seat independently, one wire.

**Backdrop screens.** Kiosk pages with pushed themes and text, a
synthesized audio bed, self-reload across hub restarts, fullscreen shell
with double-ESC exit.

**Modalities + presentations.** Smart collections (kind mode, preset,
show, or custom) run from the dashboard and the Modality Manager with
run/stop/undo/snapshot — the one-click demo surface over the modes
engine. Multi-step shows carry captions with narration wash,
stop-to-final behavior, and per-step result reporting.

**AI director.** Prompt + target + scope over mock or live providers,
per-fixture baselines and power caps, critique pass, voice input,
save-as-preset, snapshot-from-hardware. Live-proven on the real rig.
Fixed Room lighting (no smart plugs) rides along as context the AI
factors in and may suggest switching by hand — never claims to move.

**Undo + action log.** Every apply is recorded; one button replays the
previous room state through the validated path.

**Ordering.** Every list (zones, devices, modes, presets, plugs,
panels, modalities) follows a custom order set on a drag/drop admin
page with one-click A–Z sorts, saved as one small per-studio JSON
file — no database.

**Hub power.** The Hub Manager zone restarts the hub program or shuts
the hub PC down completely, optionally turning everything off first
and DTMF-shutting-down the workstation down the audio wire (one of
196 codes on that wire), always behind a final confirmation.

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

One Python service, JSON-configured studios, 546-test suite with
hardware and socket-free tiers, dual HTTPS/HTTP serving, watchdog task,
self-signed TLS. Deploys over the LAN with clobber guards on local
config. Only fictional example data is committed; real studio details
stay git-ignored and local.

## Status

Live on the hub host, demo-ready. Every light family drives through
bench, modes, modalities, and AI; OBS boot-over-audio runs in ~10s per
seat; shows, undo, and snapshot all land. The zone/plug structure now
matches the room (eight zones in plug order including fixed Room
lighting), list ordering is admin-configurable, assets move between
zones on the Equipment page, and the hub can shut the whole room down
in order. All plugs are live with their real equipment attached; the
show-account listener is installed with its first-login run still to be
witnessed — not blocking the demo.
