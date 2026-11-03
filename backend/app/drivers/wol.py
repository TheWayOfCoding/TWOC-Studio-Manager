# SPDX-License-Identifier: AGPL-3.0-or-later
"""Wake-on-LAN: magic packets from the hub (no ports on the target).

The target NIC listens while the PC sleeps or is soft-off; nothing runs
there and nothing inbound opens. Needs BIOS + OS "wake on magic packet"
enabled on a wired NIC (Windows Fast Startup can break it — disable it or
use full shutdown on the target).
"""

import socket

BROADCAST = "255.255.255.255"
PORT = 9
REPEATS = 3


class WolError(Exception):
    """Bad MAC text or broadcast failure."""


def parse_mac(text):
    """Six raw bytes from AA:BB:CC:DD:EE:FF (colons/dashes/bare all fine)."""
    cleaned = "".join(ch for ch in str(text or "") if ch.isalnum()).lower()
    if len(cleaned) != 12 or any(ch not in "0123456789abcdef" for ch in cleaned):
        raise WolError("wol_mac must be 6 hex bytes, e.g. AA:BB:CC:DD:EE:FF")
    return bytes.fromhex(cleaned)


def magic_packet(mac_bytes):
    """102-byte magic payload: 6x FF sync + 16x the target address."""
    if len(mac_bytes) != 6:
        raise WolError("magic packet needs exactly 6 address bytes")
    return b"\xff" * 6 + mac_bytes * 16


def wake(mac_text, broadcast=BROADCAST, port=PORT, repeats=REPEATS):
    """Broadcast the magic packet. Returns packets sent. Never echoes the MAC."""
    payload = magic_packet(parse_mac(mac_text))
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            for _ in range(max(int(repeats), 1)):
                sock.sendto(payload, (broadcast, port))
        finally:
            sock.close()
    except WolError:
        raise
    except Exception as exc:
        raise WolError(f"broadcast failed: {exc}") from exc
    return max(int(repeats), 1)
