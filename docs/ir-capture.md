# IR Capture Workflow (YS-IRTM + Bench)

How remote codes get from a physical remote into JSON. Hardware setup lives in
`docs/ir-wiring.md`. Capture happens in the Test Bench IR card
(`http://studio.local:8001/bench`, or the `https://` dashboard on 8000); codes land in
`config/studios.local/<studio>/ir-codes/<device>.json` (local-only, git-ignored).

## Procedure

1. Aim the remote at the module's **dark RX window** from a few feet away.
2. Type the button's label, click **Capture**, press that **one** button once
   within the window. The module LED flashes on every reception.
3. A saved `{"label", "code"}` echo confirms it. Repeat per button.
4. Replay pass: pick each label from the Send dropdown, hit **Send**, watch the
   device. Record duds via the mark endpoint (`works: false` + notes).

Rules the Bench enforces: exactly one distinct code per capture window
(0 = "heard nothing", 2+ = "press only once"). Re-capturing a label overwrites
its code but preserves effectiveness notes.

## Button labels (Bytech 24-key)

Labels below are the **official** names (what the bench shows). Five fixed
colors were renamed from the remote printing after a fixed-exposure camera
calibration proved the printing wrong; the remote originals survive as
`remoteLabel` in the JSON. Remote printing order, top-left to bottom-right:
`brightness-up`, `brightness-down`, `off`, `on`, `red`, `green`, `blue`,
`white`, `red-orange`, `lime`, `sky`, `flash`, `orange`, `cyan`, `violet`,
`strobe`, `amber`, `azure`, `magenta`, `fade`, `yellow`, `teal`, `pink`,
`smooth`.

Fixed-color renames (official ← remote):

| Official | Remote | Code |
|---|---|---|
| `chartreuse` | `red-orange` | 8 |
| `mint` | `lime` | 9 |
| `royal blue` | `sky` | 10 |
| `lime` | `orange` | 12 |
| `neon green` | `yellow` | 20 |

All other fixed colors keep their remote names. Codes are the third byte of
`[0, 239, N]`; all 24 verified present.

Animated modes (official ← remote, eyeball-verified behavior):

| Official | Remote | Code | Behavior | Photo risk |
|---|---|---|---|---|
| `strobe` | `flash` | 11 | Fast sharp even cycle (intense even at low power) | intense |
| `bounce fade` | `strobe` | 15 | Smooth transitions, abrupt red/green cut to restart | mild |
| `breathing` | `fade` | 19 | Brightness ramps per base color | mild |
| `5-step sequence` | `smooth` | 23 | Hard changes, longer teal-red pause | intense |

Calibration truth (fixed-exposure captures, local-only): the strip is
blue-weak-red — only red reads true red, white reads cyan-blue, most warm
buttons land green-family, most cool/pink buttons land blue/teal. Button
names describe the calibrated appearance, not the remote printing.

## Modes / AI control (automation)

The strip is a `lightstrip` device, addressable from modes, presets, shows,
and the AI director through the IR translator:

- `power` on/off and `intensity` (0 = off, anything above = on) flip the
  on/off buttons. There is no absolute brightness — brightness stepping
  stays a bench-only relative control.
- `color` names an official fixed-color label (validated against the
  calibration table; unknown names skip by name, never silently).
- `hue` (+ optional `saturation`, default 100) fires the fixed button whose
  measured color reads closest. Saturation without hue skips by name.
- `cct` and scenes skip by name (no CCT channel; animated modes stay
  bench-only).

Power sends land before color in every step, so a color never fires at a
dark strip. Every send settles briefly: the module swallows a second frame
sent mid-transmit. If a label is ever renamed in the store, the calibration
table in the IR module must follow it or plans will skip the new name.

## Hold behavior (verified 2026-10-28)

Holding a remote button down produces **exactly one** 3-byte code per 20s
window — the YS-IRTM emits the press edge and swallows NEC repeat frames.
Holds therefore cannot be sensed, only reproduced: the send path supports
repeat counts (`times`, 100ms→300ms gaps after TX-time findings). On the Bytech
strip, ~100 presses span the brightness range, so ×10 ≈ a 10% step.

## Troubleshooting

- **Heard nothing:** aim, distance (3–6 ft), fresh remote batteries, then
  suspect non-NEC protocol (this module only reports NEC).
- **Multiple codes:** two presses in one window, or a neighbor's remote —
  re-capture with a single deliberate press.
- **No F1 on send:** serial contention (now lock-guarded) or the module busy
  mid-transmit (gap raised to 300ms). Never auto-retry: toggle buttons would
  double-fire.
