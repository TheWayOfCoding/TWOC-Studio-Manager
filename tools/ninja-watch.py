# SPDX-License-Identifier: AGPL-3.0-or-later
"""Independent RF witness: is the hub actually transmitting Ninja frames?

Run on a SECOND PC with Bluetooth on (never the hub — WinRT binds the
default radio only, so the hub cannot hear itself). Actively watches BLE
advertisements with extended advertisements enabled (the WinRT default
is legacy-only, which blinds the watcher to exactly this traffic) and
logs every Ninja-shaped sighting:

- hub/phone command frames: company 0x004C manufacturer data starting
  02 15 EF (the 27-byte frame minus len/type/company), decoded live
- fixture identity adverts: service FEF3, roughly one per 2 s per light

Usage:
  .venv\\Scripts\\python.exe tools\\ninja-watch.py [--seconds 120] [--all]

Test script: start the watcher, bench-send from the hub, then send once
from the phone app as a positive control. Silence from the hub plus a
loud phone frame proves hub TX-silent; both loud proves the fixture.
"""

import argparse
import sys
import time

GROUPS = "ABCDEF"


def decode_frame(data):
    """Decode manufacturer data (frame bytes 4..26) or return None."""
    if len(data) != 23 or data[0] != 0x02 or data[1] != 0x15 or data[2] != 0xEF:
        return None
    addr = data[3]
    channel, rem = divmod(addr - 1, 8)
    group = GROUPS[rem] if 0 <= rem < len(GROUPS) else "?"
    return {
        "channel": channel,
        "group": group,
        "addr": addr,
        "cct_raw": data[5],
        "brightness": data[6],
        "counter": data[16],
    }


def buffer_bytes(buf):
    from winsdk.windows.storage.streams import DataReader

    reader = DataReader.from_buffer(buf)
    out = bytearray(reader.unconsumed_buffer_length)
    reader.read_bytes(out)
    return bytes(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--all", action="store_true", help="log every advertiser seen")
    args = parser.parse_args()

    try:
        from winsdk.windows.devices.bluetooth.advertisement import (
            BluetoothLEAdvertisementWatcher,
            BluetoothLEScanningMode,
        )
    except ImportError:
        print("winsdk missing: run with the repo .venv python on Windows", flush=True)
        return 1

    counts = {"frames": 0, "fixtures": 0, "other": 0}
    seen_senders = set()

    def on_received(sender, event):
        try:
            adv = event.advertisement
            address = event.bluetooth_address
            rssi = getattr(event, "raw_signal_strength_in_dbm", "?")
            for section in adv.manufacturer_data:
                if section.company_id != 0x004C:
                    continue
                data = buffer_bytes(section.data)
                decoded = decode_frame(data)
                stamp = time.strftime("%H:%M:%S")
                if decoded:
                    counts["frames"] += 1
                    seen_senders.add(address)
                    print(
                        f"{stamp} FRAME sender={address:012X} rssi={rssi} "
                        f"CH{decoded['channel']}-{decoded['group']} "
                        f"bright={decoded['brightness']}% cct_raw={decoded['cct_raw']} "
                        f"counter={decoded['counter']} raw={data.hex()}",
                        flush=True,
                    )
                elif args.all:
                    counts["other"] += 1
                    print(f"{stamp} OTHER sender={address:012X} rssi={rssi} data={data.hex()}", flush=True)
                return
            uuids = [str(u) for u in adv.service_uuids]
            if any("0000fef3" in u for u in uuids):
                counts["fixtures"] += 1
                if counts["fixtures"] % 10 == 1:
                    print(
                        f"{time.strftime('%H:%M:%S')} FIXTURE sender={address:012X} rssi={rssi} "
                        f"(identity advert #{counts['fixtures']})",
                        flush=True,
                    )
        except Exception as exc:  # noqa: BLE001 - witness must never die on one packet
            print(f"watch error: {exc}", flush=True)

    watcher = BluetoothLEAdvertisementWatcher()
    watcher.allow_extended_advertisements = True
    watcher.scanning_mode = BluetoothLEScanningMode.ACTIVE
    watcher.add_received(on_received)
    watcher.start()
    print(
        f"watching {args.seconds}s (extended + active). "
        "Now bench-send from the hub, then once from the phone app.",
        flush=True,
    )
    try:
        time.sleep(args.seconds)
    except KeyboardInterrupt:
        pass
    watcher.stop()
    print(
        f"done: frames={counts['frames']} from {len(seen_senders)} sender(s), "
        f"fixture_adverts={counts['fixtures']}, other_apple={counts['other']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
