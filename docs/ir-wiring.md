# IR Wiring: YS-IRTM module + FTDI USB-TTL adapter

Status: researched from module photos + community protocol docs. Pending live
probe on the Pi (see "Bring-up" below). Resolves D-WIRING (wiring half).

## Hardware identified

- **IR module:** YS-IRTM V6.08 (blue board). UART-controlled NEC infrared
  transceiver: onboard MCU + 38kHz 940nm TX LED (clear) and RX receiver (dark).
  4-pin header: `GND RXD TXD VCC`. **5V logic device.**
- **USB adapter:** WWZMDiB FTDI breakout (red board, FT232RL, mini-USB, marked
  YP-05). 6-pin header with `GND VCC TXD RXD` plus flow-control pins we ignore
  (`CTS/RTS/DTR/SLEEP/...`). Has a **5V/3.3V selector jumper**.

Product pages (Amazon, blocked to scrapers — identification from photos):
- IR module: https://www.amazon.com/dp/B0F2SCPVBK
- FTDI adapter: https://www.amazon.com/dp/B0BJKCSZZW

## Wiring (Dupont jumpers, no soldering)

4 wires, UART crossover + power. FTDI **jumper set to 5V** (the YS-IRTM is a
5V-logic part; verify the jumper bridges the 5V position before powering).

| FTDI adapter | → | YS-IRTM | Notes |
|---|---|---|---|
| VCC (5V) | → | VCC | Powers the module |
| GND | → | GND | Common ground |
| TXD | → | RXD | Crossover |
| RXD | → | TXD | Crossover |

Then: mini-USB cable from the FTDI adapter to the Pi (directly, or via the
powered hub later). A long USB cable here is the whole point — it puts the IR
emitter at the strip's receiver while the Pi stays put.

On the Pi the adapter appears as `/dev/ttyUSB0` (verify with `dmesg | tail`
after plugging in). User `pi` must be in the `dialout` group
(`groups pi`; add with `sudo usermod -aG dialout pi`, then re-login).

## Serial protocol (YS-IRTM)

- 9600 baud 8N1 default. Change with `A1 F3 <rate> 00 00`
  (01=4800, 02=9600, 03=19200, 04=57600). Stay on 9600.
- **Receive:** aim the Bytech remote at the module's dark RX window and press a
  button → module emits **3 bytes** (NEC code, e.g. `00 FF 45`). Its LED
  flashes on every reception.
- **Transmit:** send **5 bytes** `A1 F1 <d1> <d2> <d3>` → module IR-blasts the
  code and replies with the single command byte `F1` as confirmation. No reply
  means the payload was not understood.
- So capture = record the 3 bytes per button; replay = prefix with `A1 F1`.

Assumption to verify live: the Bytech remote speaks NEC (most LED-strip remotes
do). If button presses yield no bytes, that assumption is suspect #1.

## Bring-up (hands-on session)

1. Wire per the table above (power off while connecting).
2. Plug USB into the Pi; confirm `/dev/ttyUSB0` exists.
3. Open the port at 9600 8N1; press remote buttons; confirm 3-byte codes arrive.
4. For each button: press → label (function/color) → save to
   `config/studios.local/home-studio/ir-codes/bytech-strip.json`.
5. Replay each saved code via `A1 F1 …`; confirm the strip responds and `F1`
   arrives back. That capture/replay loop becomes `docs/ir-capture.md`.

## Sources (inspected)

- https://raw.githubusercontent.com/mcauser/micropython-ys-irtm/master/README.md
  (pinout, 5V logic, TX protocol, baud commands)
- https://forum.arduino.cc/t/infrared-interface/346816/7
  (wiring crossover, 9600 default, A1 F1 send format, A1 F3 baud table)
