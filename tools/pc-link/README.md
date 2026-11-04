# PC-link listener (main PC)

Tone commands off a mic input, allow-listed local actions. Nothing listens
on the network — the wires are the whole channel.

## Wire it

Cable A (required, hub TX -> main RX):

1. 3.5mm cable: hub line-out (or a USB audio dongle on the hub) → main PC
   front-panel mic-in. Confirm the front jack is wired to the motherboard
   HD Audio header and set as an input (not retasked) in the audio console.
2. On the mic input, turn OFF everything "smart": enhancements, noise
   suppression, acoustic echo cancellation, and microphone AGC/boost
   automation. Tones need a flat fixed-gain path — set one level and leave
   it. (`mmsys.cpl` → Recording → the input → Properties.)

Cable B (optional, main TX -> hub RX, reverse acks):

3. Second 3.5mm cable: main-PC front headphone-out → hub mic-in
   (this rig uses front-panel jacks on the main PC for both wires).
   Same rules: flat fixed level, no enhancements on either end, and
   keep both link jacks off the show-audio path (bed audio sharing a
   wire jams bursts).

Hum on either line? That's a ground loop between the two PCs: a 3.5mm
ground-loop isolator (or a USB dongle on either end) kills it.

## Realtek: separate the jacks (read before plugging in)

The streaming mic on the rear jack stays untouched. The catch: Realtek
defaults to tying same-type jacks together, where plugging in the front
mic can mute the rear one. Flip BOTH separators first (Device advanced
settings, in HD Audio Manager or the Store Realtek Audio Console):

- Recording → **Separate all input jacks as independent input devices**
  (front mic-in becomes its own capture endpoint; OBS keeps the rear).
- Playback → **Make front and rear output devices playback two different
  audio streams simultaneously** (cable B's front out stays independent
  of the rear speakers).

Then front and rear are independent endpoints with independent gains:
OBS keeps the rear, the listener takes the front, both record at once.
Do the flip + first plug-in OFF-stream (endpoint changes can glitch
live audio for a moment), verify the wireless mic is still live in OBS,
and it stays stable from then on. Driver updates can reset this setting —
re-check it whenever audio updates land.

Two gotchas that have bitten this rig:

- Windows 11 startup: if the motherboard audio panel was disabled under
  Settings → Apps → Startup (or the Realtek service is stopped), the
  front jacks silently do nothing — no error, just no signal. Re-enable,
  reboot, and confirm the panel app is running before debugging cables.
- Full Realtek package: the console needs the vendor's complete driver
  package (service + HSA components), not just the bare INF. Run the
  board vendor's root Setup.exe as admin, reboot, then re-check the OBS
  mic source (endpoint re-enumeration can silence it until reselected).

Fallback that sidesteps it all: a cheap USB audio dongle as the link
input instead of the front jack.

## Wake-on-LAN target setup (main PC)

No ports, no services, no listeners on this machine. Requirements:

- Wired NIC (WoL over Wi-Fi is a different, flakier feature).
- BIOS: wake on LAN / wake on PCI-E enabled; ErP / Deep Sleep OFF
  (ErP cuts NIC standby power in S5 and WoL silently stops working).
- Windows NIC driver → Power Management: "Allow this computer to turn off
  this device" ON, "Allow this device to wake the computer" ON, and
  "Only allow a magic packet to wake" ON (else any LAN chatter boots it).
- Fast Startup OFF (Settings → Power → additional power settings), or
  always full-shutdown: hybrid shutdown breaks WoL from S5 on most boards.
- Test with the PC in full shutdown (not sleep): hub `POST /api/link/wol`
  should boot it within seconds. If it only wakes from sleep, ErP or Fast
  Startup is the culprit.

Demo boot order: Kasa plug on → wait ~25s (NIC/link settle) → WoL →
wait for Windows + listener (~60-90s) → link `obs` tone → wait for the OBS
websocket → apply the mode. Fixed waits, filmable, zero PC-side config
beyond the list above.

## Per-user install (do this for each Windows account that runs the show)

Everything the show needs on this PC lives inside ONE user profile —
the listener, its config, and portable OBS. Nothing is shared between
accounts and nothing needs admin after Python is in. Substitute the
account name for `USER` below (today that is the `creative` account on
the 9900x workstation, which autologins for the presentation).

1. Log in as USER once, so Windows builds the profile. Until that
   first login there is nowhere to install to.
2. Confirm Python 3.11+ is reachable from that account (`py --version`
   or `python --version`). A system-wide install is fine; a per-user
   install keeps it fully inside the profile.
3. Create the home for it: `C:\Users\USER\pc-link\` gets `listener.py`,
   `send.py`, `requirements-link.txt` from this folder, PLUS
   `linkproto.py` copied from `backend/app/linkproto.py` in the repo,
   and a `local.json` made from `local.json.example` (never copy a
   live `local.json` from another account — its paths point at the
   wrong profile). Without `linkproto.py` alongside them the scripts
   die at import, before any log line — if the log file never appears
   after a login, this missing file is the first suspect.
4. Install the deps as USER: `pip install -r requirements-link.txt`
   (add `--user` if Python is system-wide and you want the packages
   inside the profile too).
5. Copy portable OBS into the profile as well, e.g.
   `C:\Users\USER\Portable-OBS\`, and point the `obs` action's
   `launch`/`cwd` at `C:/Users/USER/Portable-OBS/bin/64bit/obs64.exe`.
   Keep `--portable` in args so it never touches the account's
   installed-OBS settings.
6. Set `"log_file"` to somewhere inside the profile, e.g.
   `C:/Users/USER/pc-link/listener.log` — Startup-run instances have
   no console, so the log is the only window in.
7. Calibrate as USER before trusting it: `python listener.py
   --list-devices` (device names are system-level but endpoint
   enablement can differ per account), then `--calibrate` while the
   hub plays its test tone. Mid-scale rms on tone, near-zero in
   silence, same as the bench checklist below.
8. Prove it end to end from the hub bench: Send+Wait `ping` (expect
   `confirmed`), then Boot+OBS. Only then add the Startup entry.
9. Startup entry (USER's own Startup folder, no admin): Win+R →
   `shell:startup` → shortcut with target
   `C:\Users\USER\pc-link\listenerw.bat` where the bat file is:
   `@echo off` + `cd /d C:\Users\USER\pc-link` + `pythonw listener.py`.
   `pythonw` keeps it windowless; the log file carries all output.
   Reboot (or log out/in) and confirm a fresh timestamp in the log.
10. If the account autologins for the presentation, the hub's boot
    flow needs no changes — Windows login + listener startup is the
    ~60-90s it already waits through.

Moving to a new account later is the same list again: fresh profile
copy, fresh `local.json` paths, calibrate, prove, then Startup. The
old account's copy keeps working untouched.

Per-seat launch commands: the show seat's `local.json` allows `"obs"`,
the dev/test-bench seat allows `"obs_bench"` instead — each seat runs
only its own command, so one wire addresses two sessions on the same
PC without double launches. `ping` stays shared (either seat's ack
proves the wire is live). Keep each seat's allow-list to its own
launch command. No step here needs admin: with Python already on the
machine, a standard (limited) Windows account installs deps with
`pip install --user`, keeps everything inside its own profile, and
runs the Startup entry unprivileged.

## Run it

```powershell
pip install -r requirements-link.txt
copy local.json.example local.json
# edit local.json: input device name, action allow-list (local paths stay here)
python listener.py --list-devices
python listener.py --calibrate   # live level + decode readout, no actions
python listener.py               # for real (Startup shortcut when trusted)
```

Level-setting: hub side `POST /api/link/test` (or the bench Test tone
button) plays a 1 kHz tone; the listener `--calibrate` readout should
show a steady mid-scale rms while it plays and near-zero in silence. If
decodes never land, the input gain is almost always the cause (too hot
clips the tones flat, too cold drops them under the gate). Set cable B
the same way from the other end: `python send.py --test-tone` on the
main PC while the hub bench Listen window runs.

Loop test (both cables): hub bench Send+Wait `ping` → one call sends,
listens 8s, and reports `confirmed` with everything it heard. Forward
silence = cable A / front-mic gain; `confirmed: false` with a heard ping
on the listener = cable B / hub-input gain.

## Unattended running

- The input stream re-opens itself if the device vanishes (USB dongle
  re-enumeration) — it prints and retries every 5s instead of dying.
- Set `"log_file": "C:/PATH/TO/pc-link.log"` in `local.json` when running
  from Startup/a shortcut: stdout/stderr append there (no console).
- At startup the listener warns about allow-list keys that are not real
  commands (they would never fire) and clamps bad `obs_watch` numbers.

## OBS launch confirm (cable B)

Actions with `"watch_obs": true` spawn a watcher after launching: it
polls the local OBS websocket (plain HTTP Upgrade handshake, no password
needed) and reports `obs_ready` / `obs_fail` down the ack path. Tune it
with top-level `obs_watch` (defaults shown):

```json
"obs_watch": {"host": "127.0.0.1", "port": 4455, "timeout_s": 45, "poll_s": 2}
```

End to end: hub bench Send+Wait `obs` (60s) → listener launches OBS,
auto-acks `ack`, watcher reports `obs_ready` → hub shows `confirmed`
with `others: ["ack"]`. `obs_fail` means OBS never opened its websocket
in the window (slow boot, wrong port, server disabled) — check the PC.

## Reverse path (cable B)

One-shot sends by hand (no listener needed):

```powershell
python send.py --list-devices
python send.py --command ack
python send.py --command obs_ready --device "Speakers (Rear)"
```

Auto-ack after every accepted command (recommended once cable B is
trusted): add to `local.json`:

```json
"ack": {"command": "ack", "device": "Speakers (Rear)"}
```

The device is a substring match like the input (see `send.py
--list-devices`). Ack failures print and never crash the listener; ack
bursts play on the main PC's output while it listens on its input, so
with the jacks separated there is no self-trigger loop.

## Actions

`shutdown` (DTMF code 05) powers this PC down: enable it per machine
with `"actions": {"shutdown": {"shutdown": true, "delay_s": 20}}`. The
delay is an abort window (`shutdown /a` cancels). The hub's Hub Manager
power-down can send it before shutting itself down. Without that exact
opt-in the command is logged and ignored.

`local.json` maps command names to `{launch, args, cwd}`. Commands the
file doesn't name are decoded, logged, and ignored. Unknown codes are
ignored silently. After each accepted command the listener enforces a
cooldown plus a quiet period, so one burst never fires twice. `cwd`
(optional) sets the child's working directory — portable OBS requires
its own `bin/64bit`, or it dies on missing locale files. Keep launch
args to `--portable`: the hub boot job cuts collection + scene after
launch, so local pinning is redundant (harmless if you keep it for
hand-launched runs).

## Packaging

Both scripts are frozen-build safe: a compiled exe reads `local.json`
from its own folder and imports the bundled protocol module, so the
same tree builds a single-file app with no Python on the target.
