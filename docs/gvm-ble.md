# GVM 1500D Bluetooth protocol (reverse-engineered)

Source: HCI snoop of the GVM LED Android app (`com.gvm.cloudphone`),
captured 2026-10-29 via scripted taps (`captures/run3/`, git-ignored).
Panel: BT_LED (example address `AA:BB:CC:DD:EE:FF`; use your panel's), APP mode, CH 3.

## Transport

- BLE GATT: frames written to characteristic
  `00010203-0405-0607-0809-0a0b0c0d2b10` (NOTIFY/READ/WRITE NO RESPONSE,
  value handle `0x10`), Write Command, no response expected.
- The Oct-2026 snoop showed app writes to handle `0x0011`, but no stack
  (BlueZ, WinRT, Android) enumerates anything there; the same frames sent
  to `2b10` drive the panel (verified live via nRF and the Hub).
  Resolve by UUID, not handle.
- At connect the app writes `0100` to handle `0x0013`.

## Frame

```
4C 54 LEN PAYLOAD CRC16 FF
```

- Magic `4C 54`, trailer `FF`.
- `LEN` = length of `PAYLOAD + CRC16` (control frames: `09`).
- `CRC16` = **CRC-16/XMODEM** (poly `0x1021`, init `0x0000`) computed over
  everything from `4C` through the last payload byte, stored **big-endian**.
  Verified against 22 captured frames.

## Control payload (7 bytes)

```
00 30 57 00 MODE 01 VAL
```

- Leading `00 30 57 00` is constant in this session (group/channel?
  TBD — replay verbatim).
- `MODE`: `00` = power, `02` = CCT brightness, `03` = CCT temperature,
  `06` = page announce, `07` = scene.
- `VAL`:
  - Power: `0x00` = off, `0x01` = on (app greys/blues the button).
  - Brightness: percent, `0x01`–`0x64` (verified 1/5/16).
  - Temperature: Kelvin/100, `0x20`–`0x38` (verified 35/43/51 = 3500/4300/5100K).
  - Page announce: `0x01` on entering CCT tab, `0x03` on entering Scene tab
    (hypothesis: 1-based tab index; panel likely just mirrors it).
  - Scene: 1-based tile index (`0x03` = Candle).

Example — brightness 16%:

```
4C 54 09 00 30 57 00 02 01 10 4E AF FF
```

## App behavior notes

- Dials emit intermediate writes ~200 ms apart during a drag, plus the
  final value. Taps emit a single write.
- One 14-byte (`0E`) setup frame at connect is still undecoded; not needed
  for open-loop control.

## Capture method (for re-runs)

- `adb forward tcp:8872 tcp:8872`, then `tools/bt_capture.py SECS OUT`.
- The capture **must run unsandboxed** (sandboxed shells cannot reach the
  adb-forwarded localhost port); always `--remove` + re-add the forward
  first (stale listings lie); gate the tap script on the output file
  reaching >= 16 bytes.
- `tools/bt_decode.py CAP [TAPLOG]` lists ATT writes; btsnoop timestamps
  are BOOTTIME, not epoch — correlate by intervals, not absolute time.

## Named panels (Bench)

The Bench drives configured panels by name — no scan needed. Give each
panel a `driverAddress` in your `studios.local` devices file:

```json
{"driver": "gvm", "driverAddress": {"ble": "AA:BB:CC:DD:EE:FF"}}
```

`GET /api/testbench/gvm/panels` lists `{id, name, studio}` (never
addresses); `probe`/`state`/`set` accept `panel` (the `studio/device`
id) instead of `address`, and panel-based responses echo the name, not
the address. A raw `address` still works for direct API use (e.g. a new
panel found via `/scan`), but the Bench UI never sends or shows one.
