# Weeylite/Viltrox Ninja Bluetooth protocol (reverse-engineered)

Source: HCI snoop of the Weeylite Pro Android app
(`com.ruitianzhixin.weeylite2`), captured via live `btsnoop_net` stream
(port 8872 over `adb forward`) with scripted taps
(`captures/ninja10/`, git-ignored). Lights: Ninja 10 (CH3-A),
Ninja 400 (CH3-B), Ninja 20 (CH3-C).

Unlike GVM (paired GATT writes), this protocol is **connectionless
broadcast**: the app carries no connection, pairs with nothing, and
re-broadcasts one extended advertisement on every UI change. Lights
listening on the matching channel/group address apply it.

## Transport

- BLE extended advertising, rebuilt from scratch per change. Each tap
  produces this host→controller command run (opcodes LE):
  `2039` disable set → `203C` remove set → `2018` (empty, constant
  scaffolding) → `2036` set ADV data (constant 25-byte params, carries
  no tap-varying data) → `2035` set random address (**rotates every
  broadcast**) → `2037` set scan response (**the 27-byte command**) →
  `2038` set params (constant) → `2039` enable set.
- Event-driven only: no periodic re-advertise. State taps (CCT slider /
  preset, brightness, GM, power, mode entry) emit the payload **twice**
  (~0.1-0.2 s apart, counter differs); select taps (group, channel,
  scene) emit **once**.
- Lights advertise back about every 2 s (LE extended ADV report,
  service-data UUID `FEF3`, 28-byte payload). The payload is constant
  across brightness/CCT/scene changes (identity, not state); its
  channel/group bytes are not isolated yet.

## Frame (27-byte scan response, index from 0)

iBeacon-mimicking wrapper (`1a ff 4c00 0215`) with Weeylite fields:

```
idx: 00 01 02 03 04 05 06 07 08 09 10 11 12 13 14-17 18 19 20 21 22-26
val: 1a ff 4c 00 02 15 ef AD 00 CC BB PP 00 GM 00..00 02 fe CT BB 00 0a 00 6e c5
```

- `AD` (7): address = **channel × 8 + group index + 1** (A=0..F=5).
  Verified: (3,A)=`0x19`, (3,B)=`0x1a`, (3,C)=`0x1b`, (5,A)=`0x29`.
- `CC` (9): CCT Kelvin/100. Verified `0x1a`=2600 (slider min),
  `0x1e`=3000, `0x28`=4000, `0x2d`=4500, `0x34`=5200, `0x37`=5500,
  `0x3c`=6000, `0x46`=7000. Same encoding as GVM.
- `BB` (10) and (21): brightness percent, mirrored in both spots.
  Verified 2/5/7/10/23/25. Max verified 25 (brighter untested).
- `PP` (11): CCT preset index 0-5 (3000..7000 in tile order) or `0x64`
  when set via slider. **Lags one broadcast** on slider moves (first
  of the pair carries the stale index). Cosmetic to the light as far
  as verified (see slider-authority note below).
- `GM` (13): green/magenta + 10 (offset binary). Verified
  -10→`0x00`, -5→`0x05`, 0→`0x0a`, +10→`0x14`.
- `CT` (20): per-broadcast counter, +1 every group, observed
  `0x08..0x82` continuous (wrap unseen). Enforcement unknown.
- Rest constant: `ef` (6), `00` (8,12,14-17), `02 fe` (18-19),
  `00 0a 00 6e c5` (22-26).

Example — group A steady look, 5500K, 7%, GM 0 (`CT` varies):

```
1a ff 4c 00 02 15 ef 19 00 37 07 03 00 0a 00 00 00 00 02 fe CT 07 00 0a 00 6e c5
```

## FX scenes

Scene taps send the same template with `CC` (9) **and** (12) both set
to the scene index, plus FX markers `0x02` in bytes 8, 11, and 17
(steady looks carry `0x00`/preset/`0x00` there instead); all other
fields carry the current look:

```
0=Flash 1=Burst 2=Flash lamp 3=Blink 4=Weld 5=SOS 6=Candlelight
7=Flame 8=CCT LOOP 9=TV
```

Mode detect: CCT/100 below 25 is unrepresentable, so `CC < 25` means
a scene is active. Entering the CCT screen re-broadcasts the steady
look (scene exit). Scene strobes are intense even at low power; keep
scene taps to ~1.5 s on a working key light. The FX speed slider tap
at its current value emitted nothing (event-driven).

## Power

The group Switch broadcasts the current template with **both
brightness bytes zeroed** (`BB`=0); switching back on restores the
previous brightness. Verified the group A light went dark/lit on a
real fixture. No separate power byte observed.

## Fixture following (user eyeball)

- Ninja 10 (A, fixed daylight): brightness ✓, power ✓, scenes ✓
  (stuck-in-TV proved it); CCT bytes ignored as expected.
- Ninja 400 (B, bi-color): brightness ✓, CCT ✓ (3000→7000 flip seen),
  per-group power inferred (same bytes as A; B switch taps missed).
- Ninja 20 (C, fixed daylight): brightness ✓; CCT sent, ignored.
- Cross-talk: none observed — each broadcast moved only its addressed
  group (A/B/C independence witnessed live).

## Hub control implications

- The hub must **advertise**, not connect: no GATT, no pairing. On
  Windows this means a BLE advertisement publisher (WinRT), not a
  BLE client library. Payload = the 27-byte frame above.
- Hub control SHIPPED (Nov 2): frame builders + Mock/WinRT advertisers
  with cached look+counter per address, scene gate, bench card (direct
  ch/grp + dry-run + raw hex). Every send is a full look.
- TRANSPORT PROVEN: fixtures accept the command bytes placed in ADV
  manufacturer data — no scan-rsp needed (WinRT has no scan-rsp API;
  user eyeball confirmed a real fixture moved). Frames are air-verified
  byte-exact against the app template.
- Dongles that reject extended/anonymous advertising fall back to legacy
  (extended+anonymous off); the mode is reported per send. The hub
  mimics the app with rotating sender addresses + incrementing `CT` and
  double-sends state changes. Whether lights enforce the counter or
  require rotation is still unknown.
- Open-loop honesty: fixtures never report, so hub state carries a
  transmitted flag (memory, not telemetry), and a pristine cache rejects
  a partial first-send (brightness/power required, else a dark-flash
  trap). State reads never transmit.
- Modes/AI wiring live (steady looks only): power/intensity/cct translate
  through the modes engine with the bench sharing one cached driver per
  address (multi-key steps merge to one broadcast). Fixed-temp panels
  skip cct by name. Scenes stay bench-only except confirmed shows;
  gm/preset stay bench-only (no generic keys).
- Slider-authority (preset byte cosmetic?) still needs one eyeball:
  run8 set 4500K/2600K via slider (`PP`=`0x64`); confirm the 400
  actually showed neutral-then-warm.

## App behavior notes (automation)

- Taps broadcast ~instantly; sequence with 2-3 s holds, 1.5 s for
  scenes. Screenshots/dumps between taps are for the record only.
- Top-right `+` opens a "new scene" dialog (Cancel at its left
  button); it swallows nearby taps while open — never tap it for
  navigation.
- The channel drawer auto-closes on select; a BACK key after that
  backgrounds the whole app. Relaunch by explicit intent, and guard
  every script with a foreground check (no blind taps).
- The CCT screen has no brightness readout — verify brightness via
  packets (`BB`) or the fixture's own screen.
- Group buttons carry no accessibility selected-state; trust packet
  addresses, not dumps, for group targeting.
- Untapped so far: HSL / RGBWY / X,Y / Color Chip / Color Picker /
  My Scene screens, FX speed byte, music tab, groups D-F,
  channels 1-2/4/6-19, counter wrap, `2036` constant blob decode.
