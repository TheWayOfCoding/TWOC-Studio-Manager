# DMX Wiring: USB dongle → RJ45 fixtures (bench-verified)

Chain: PC → DSD TECH SH-RS09B USB cable (FTDI chip, OpenDMX-style
bit-bang framing — **not** the Enttec Pro protocol) → 3-pin XLR →
RJ45 CAT5 → Viltrox/Weeylite fixtures with RJ45 DMX in/out, daisy-chained
DMX OUT → DMX IN down the line.

## The adapter trap

Generic 3-pin-XLR-male → RJ45-female adapters are **not** wired to any
DMX standard. One measured unit (SiYear, printed `CHSM-250603014`) maps:

| XLR pin | Signal | → RJ45 pin on that adapter |
|---|---|---|
| 1 | GND/shield | 6 |
| 2 | Data− | 7 |
| 3 | Data+ | 8 |

ESTA / ANSI E1.27-2 wants Data+ on RJ45 **1**, Data− on **2**, and
ground on **7+8** — so that adapter as-shipped can never drive a
standard fixture. Symptoms of a miswired path: dongle LED flashes (USB
side is fine) but fixtures never show the DMX connection icon and never
respond, even isolated one at a time.

## The fix: custom CAT5

Re-terminate one end of a CAT5 cable so the fixture end presents ESTA
wiring (1 = D+, 2 = D−, 7 = GND) given whatever the adapter end feeds
it. Our working cable was built from a T-568B cable with one custom end:

> **TO CONFIRM — exact custom crimp order.** The working cable's custom
> end was built and verified live, but its full 8-wire order was never
> written down. Re-meter it (procedure below) and replace this note with
> the measured pin→wire table before rebuilding from this doc alone.

## Continuity procedure (multimeter)

Unplug everything. Meter each path end to end:

1. XLR pin 1 (GND) ↔ RJ45 pin 7 at the fixture plug.
2. XLR pin 2 (Data−) ↔ RJ45 pin 2 at the fixture plug.
3. XLR pin 3 (Data+) ↔ RJ45 pin 1 at the fixture plug.
4. No continuity between any two of those three paths, and none from any
   of them to the cable shield/shell except path 1.

If the adapter maps differently from the table above, adjust the custom
end to compensate — the fixture end must always land on 1/2/7.

## Fixture addresses and calibration (verified live)

- Ninja 400 (bi-color 2500–8500K): dimmer = DMX ch 1, CCT = ch 2.
- Ninja 20 (fixed 5600K): dimmer = DMX ch 20.
- CCT is linear: 0 = 2500K, 255 = 8500K.
- The on-screen % readout **floors**: DMX 25 reads 9, DMX 26 reads 10
  (nominal 10%). The driver converts accordingly.

## Framing and reliability notes

- Break 5 ms, mark-after-break 2 ms, ~25 ms frame gap (~19 fps).
  Windows USB scheduling jitters sub-ms timing, so wide margins beat
  fast frames; set the FTDI latency timer to 1 ms if flicker appears.
- Dwell fades re-emit each step's frame for the whole hold — single-shot
  frames get lost on this link and fixtures latch only what arrives.
- Reserve (not yet fitted): a 120 Ω terminator across the data pair at
  the last fixture. Any future flicker regression → fit one first.
