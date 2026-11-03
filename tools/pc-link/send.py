# SPDX-License-Identifier: AGPL-3.0-or-later
"""PC-link sender: one-shot tone bursts out of the main PC (reverse path).

Mirror of the hub's POST /api/link/send for the second cable (main TX ->
hub RX). Used to test the reverse wire and to send acks by hand; the
listener's optional ack config automates the common case.

Usage:
  python send.py --list-devices
  python send.py --command ack
  python send.py --command obs_ready --device "Speakers (Rear)"
  python send.py --test-tone

Frozen builds (PyInstaller exe) import the bundled linkproto.
"""

import argparse
import math
import sys
from pathlib import Path

if not getattr(sys, "frozen", False):
    ROOT = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(ROOT / "backend"))

try:
    from app.linkproto import COMMANDS, encode_samples, frame_for  # noqa: E402
except ImportError:  # frozen exe bundles linkproto as a top-level module
    from linkproto import COMMANDS, encode_samples, frame_for  # noqa: E402

RATE = 44100


def resolve_output(name):
    import sounddevice as sd

    if name is None:
        return None
    if isinstance(name, int):
        return name
    lowered = str(name).lower()
    for index, info in enumerate(sd.query_devices()):
        if (info.get("max_output_channels") or 0) > 0 and lowered in str(info.get("name") or "").lower():
            return index
    raise SystemExit(f"unknown output (see --list-devices): {name!r}")


def main():
    parser = argparse.ArgumentParser(description="PC-link reverse-path sender")
    parser.add_argument("--command", default="ack", help=f"one of {sorted(COMMANDS)}")
    parser.add_argument("--device", default=None, help="output substring or index (default device)")
    parser.add_argument("--rate", type=int, default=RATE)
    parser.add_argument("--test-tone", action="store_true", help="play 1 kHz for 1s instead")
    parser.add_argument("--list-devices", action="store_true")
    args = parser.parse_args()

    try:
        import sounddevice as sd
    except ImportError:
        raise SystemExit("pip install -r requirements-link.txt first (sounddevice)")

    if args.list_devices:
        for index, info in enumerate(sd.query_devices()):
            if (info.get("max_output_channels") or 0) > 0:
                print(f"{index}: {info.get('name')} (out {info['max_output_channels']})")
        return

    device = resolve_output(int(args.device) if str(args.device).isdigit() else args.device)
    if args.test_tone:
        samples = [0.4 * math.sin(2 * math.pi * 1000 * i / args.rate) for i in range(args.rate)]
        label = "test tone 1kHz/1s"
    else:
        if args.command not in COMMANDS:
            raise SystemExit(f"unknown command {args.command!r} (want one of {sorted(COMMANDS)})")
        samples = encode_samples(frame_for(args.command), args.rate)
        label = f"command {args.command} ({frame_for(args.command)})"
    sd.play(list(samples), samplerate=args.rate, device=device, blocking=True)
    print(f"sent {label} on device={device}", flush=True)


if __name__ == "__main__":
    main()
