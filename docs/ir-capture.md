# IR Capture Workflow (YS-IRTM + Bench)

How remote codes get from a physical remote into JSON. Hardware setup lives in
`docs/ir-wiring.md`. Capture happens in the Test Bench IR card
(`http://studio.local:8000/bench`); codes land in
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

Row order, top-left to bottom-right: `brightness-up`, `brightness-down`, `off`,
`on`, `red`, `green`, `blue`, `white`, `red-orange`, `lime`, `sky`, `flash`,
`orange`, `cyan`, `violet`, `strobe`, `amber`, `azure`, `magenta`, `fade`,
`yellow`, `teal`, `pink`, `smooth`.

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
