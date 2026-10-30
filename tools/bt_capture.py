# SPDX-License-Identifier: AGPL-3.0-or-later
"""Capture a live Android btsnoop stream to a file.

Prerequisites on the phone: Developer options -> Enable Bluetooth HCI snoop log.
Forwarding: adb forward tcp:8872 tcp:8872 (the on-device btsnoop_net listener).

Usage: python tools/bt_capture.py <seconds> <outfile>
"""

import socket
import sys
import time


def main():
    seconds = float(sys.argv[1])
    out_path = sys.argv[2]
    deadline = time.monotonic() + seconds
    sock = None
    while time.monotonic() < deadline:
        try:
            sock = socket.create_connection(("127.0.0.1", 8872), timeout=10)
            break
        except OSError:
            time.sleep(1.0)
    if sock is None:
        print("connect failed: 127.0.0.1:8872", file=sys.stderr)
        sys.exit(1)
    with sock:
        sock.sendall(b" ")
        sock.settimeout(2.0)
        with open(out_path, "wb") as handle:
            while time.monotonic() < deadline:
                try:
                    chunk = sock.recv(65536)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                handle.write(chunk)
                handle.flush()
    print(f"saved {out_path}")


if __name__ == "__main__":
    main()
