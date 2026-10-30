# SPDX-License-Identifier: AGPL-3.0-or-later
"""Decode ATT writes from an Android btsnoop capture.

Parses HCI UART frames, reassembles single-packet L2CAP, and lists ATT
Write Commands (0x52) / Write Requests (0x12) with microsecond timestamps.
Optionally groups writes under tap-log labels (``label epoch.ns`` per line).

Usage: python tools/bt_decode.py <capture.btsnoop> [taplog] [--window SEC]
"""

import struct
import sys

ATT_OPS = {0x12: "WRITE_REQ", 0x52: "WRITE_CMD"}


def parse_btsnoop(path):
    """Yield (ts_us, sent_bool, payload_bytes) for each record."""
    with open(path, "rb") as handle:
        header = handle.read(16)
    if len(header) < 16 or not header.startswith(b"btsnoop\0"):
        raise ValueError("not a btsnoop file")
    records = []
    with open(path, "rb") as handle:
        handle.seek(16)
        while True:
            prefix = handle.read(24)
            if len(prefix) < 24:
                break
            orig_len, incl_len, flags, _drops, ts_us = struct.unpack(">IIIIQ", prefix)
            payload = handle.read(incl_len)
            if len(payload) < incl_len:
                break
            records.append((ts_us, not (flags & 1), payload))
    return records


def att_writes(records):
    """Extract ATT writes from HCI records. Returns list of dicts."""
    writes = []
    stats = {"records": 0, "acl_sent": 0, "acl_recv": 0, "att": 0, "skipped": 0}
    for ts_us, sent, pkt in records:
        stats["records"] += 1
        try:
            if not pkt or pkt[0] != 0x02:  # ACL only
                continue
            if sent:
                stats["acl_sent"] += 1
            else:
                stats["acl_recv"] += 1
            handle_flags, _hci_len = struct.unpack_from("<HH", pkt, 1)
            pb_flag = (handle_flags >> 12) & 0x03
            if pb_flag == 0x01:  # continuation fragment
                continue
            l2_len, cid = struct.unpack_from("<HH", pkt, 5)
            if cid != 0x0004:  # ATT only
                continue
            l2_payload = pkt[9 : 9 + l2_len]
            if len(l2_payload) < 3:
                continue
            opcode = l2_payload[0]
            if opcode not in ATT_OPS:
                continue
            att_handle = struct.unpack_from("<H", l2_payload, 1)[0]
            value = l2_payload[3:]
            writes.append(
                {
                    "ts": ts_us / 1e6,
                    "dir": "sent" if sent else "recv",
                    "op": ATT_OPS[opcode],
                    "handle": att_handle,
                    "value": value.hex(),
                }
            )
            stats["att"] += 1
        except (struct.error, IndexError):
            stats["skipped"] += 1
    return writes, stats


def load_taps(path):
    taps = []
    with open(path) as handle:
        for line in handle:
            parts = line.split()
            if len(parts) == 2:
                taps.append((parts[0], float(parts[1])))
    return taps


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    window = 2.5
    for i, a in enumerate(sys.argv):
        if a == "--window" and i + 1 < len(sys.argv):
            window = float(sys.argv[i + 1])
    if not args:
        print("usage: bt_decode.py <capture.btsnoop> [taplog] [--window SEC]")
        sys.exit(2)
    records = parse_btsnoop(args[0])
    writes, stats = att_writes(records)
    print(f"records={stats['records']} acl_sent={stats['acl_sent']} "
          f"acl_recv={stats['acl_recv']} att_writes={stats['att']} "
          f"skipped={stats['skipped']}")
    handles = sorted({w["handle"] for w in writes})
    print("handles:", " ".join(f"0x{h:04x}" for h in handles))
    taps = load_taps(args[1]) if len(args) > 1 else []
    for tap_label, tap_ts in taps:
        print(f"--- {tap_label} @{tap_ts:.3f} ---")
        shown = 0
        for w in writes:
            if abs(w["ts"] - tap_ts) <= window:
                print(f"  {w['ts'] - tap_ts:+.3f}s {w['dir']} {w['op']} "
                      f"h=0x{w['handle']:04x} {w['value']}")
                shown += 1
        if not shown:
            print("  (no ATT writes in window)")
    if not taps:
        for w in writes:
            print(f"{w['ts']:.3f} {w['dir']} {w['op']} "
                  f"h=0x{w['handle']:04x} {w['value']}")


if __name__ == "__main__":
    main()
