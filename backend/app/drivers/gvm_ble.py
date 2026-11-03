# SPDX-License-Identifier: AGPL-3.0-or-later
"""GVM 1500D panel driver over BLE (Bleak; BlueZ on Linux, WinRT on Windows).

Speaks the reverse-engineered protocol in docs/gvm-ble.md: frames written
without response to characteristic 2b10. Open loop: the panel never reports
state, so get_state() is a reachability probe, not a live read.

Synchronous wrapper around Bleak's asyncio API. Call only outside a running
event loop (FastAPI sync routes run in a threadpool, so this holds).
"""

import asyncio

from .base import Driver, DriverResult

# Control point: the 2b10 characteristic (NOTIFY/READ/WRITE NO RESPONSE).
# The Oct-2026 snoop showed app writes to handle 0x0011, but no stack
# (BlueZ, WinRT, Android) has ever enumerated a characteristic there;
# the same frames sent to 2b10 drive the panel. Resolve by UUID.
CONTROL_UUID = "00010203-0405-0607-0809-0a0b0c0d2b10"
MAGIC = bytes((0x4C, 0x54))
PREFIX = bytes((0x00, 0x30, 0x57, 0x00))
TRAILER = 0xFF

MODE_POWER = 0x00
MODE_BRIGHTNESS = 0x02
MODE_TEMPERATURE = 0x03
MODE_HUE = 0x04
MODE_SATURATION = 0x05
MODE_PAGE = 0x06
MODE_SCENE = 0x07

MIN_BRIGHTNESS = 0
MAX_BRIGHTNESS = 100
MIN_CCT = 3200
MAX_CCT = 5600
CCT_STEP = 100
MIN_HUE = 0
MAX_HUE = 360
HUE_STEP = 5
MIN_SATURATION = 0
MAX_SATURATION = 100
MAX_SCENE = 18  # 1300D/1500D manual scene list

PAGE_NAMES = {"cct": 0x01, "hsi": 0x02, "scene": 0x03}

# Last-sent single-key echoes per panel address (merged look). Drivers are
# constructed per call (bench + modes), so memory lives here keyed by
# address, not on instances. Powers hardware snapshots; never echoed
# (addresses stay scrubbed at the API boundary).
_PANEL_LOOKS = {}


def panel_look(address):
    """Last-sent look for one panel ({} when untouched since hub start)."""
    return dict(_PANEL_LOOKS.get(address, {}))


def _record_look(address, summary):
    _PANEL_LOOKS[address] = {**_PANEL_LOOKS.get(address, {}), **summary}


class GvmError(Exception):
    """Panel unreachable, discovery failed, or command rejected."""


def crc_xmodem(data):
    """CRC-16/XMODEM (poly 0x1021, init 0x0000). Returns an int."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def build_frame(mode, value):
    """Build one control frame. `mode`/`value` are byte ints. Returns bytes."""
    for name, num in (("mode", mode), ("value", value)):
        if not isinstance(num, int) or isinstance(num, bool) or not 0 <= num <= 255:
            raise GvmError(f"{name} must be a byte int, got {num!r}")
    body = MAGIC + bytes((9,)) + PREFIX + bytes((mode, 0x01, value))
    return body + crc_xmodem(body).to_bytes(2, "big") + bytes((TRAILER,))


def power_frame(on):
    """Power frame. `on` is truthy for on, falsy for off."""
    return build_frame(MODE_POWER, 0x01 if on else 0x00)


def brightness_frame(percent):
    """Brightness frame. `percent` is an int 0-100 (HSI capture: 0 is a real setpoint)."""
    if not isinstance(percent, int) or isinstance(percent, bool):
        raise GvmError(f"brightness must be an int 0-100, got {percent!r}")
    if not MIN_BRIGHTNESS <= percent <= MAX_BRIGHTNESS:
        raise GvmError(f"brightness must be an int 0-100, got {percent!r}")
    return build_frame(MODE_BRIGHTNESS, percent)


def hue_frame(degrees):
    """Hue frame. `degrees` is 0-360 in steps of 5, sent as degrees/5."""
    if (
        not isinstance(degrees, int)
        or isinstance(degrees, bool)
        or not MIN_HUE <= degrees <= MAX_HUE
        or degrees % HUE_STEP
    ):
        raise GvmError(f"hue must be 0-360 in steps of 5, got {degrees!r}")
    return build_frame(MODE_HUE, degrees // HUE_STEP)


def saturation_frame(percent):
    """Saturation frame. `percent` is an int 0-100, sent raw."""
    if not isinstance(percent, int) or isinstance(percent, bool):
        raise GvmError(f"saturation must be an int 0-100, got {percent!r}")
    if not MIN_SATURATION <= percent <= MAX_SATURATION:
        raise GvmError(f"saturation must be an int 0-100, got {percent!r}")
    return build_frame(MODE_SATURATION, percent)


def cct_frame(kelvin):
    """CCT frame. `kelvin` is a multiple of 100 from 3200 to 5600."""
    if (
        not isinstance(kelvin, int)
        or isinstance(kelvin, bool)
        or not MIN_CCT <= kelvin <= MAX_CCT
        or kelvin % CCT_STEP
    ):
        raise GvmError(f"cct must be 3200-5600 in steps of 100, got {kelvin!r}")
    return build_frame(MODE_TEMPERATURE, kelvin // 100)


def scene_frame(index):
    """Scene frame. `index` is the 1-based tile number, 1-18."""
    if not isinstance(index, int) or isinstance(index, bool) or not 1 <= index <= MAX_SCENE:
        raise GvmError(f"scene must be an int 1-18, got {index!r}")
    return build_frame(MODE_SCENE, index)


def page_frame(name_or_index):
    """Page-announce frame. Accepts 'cct'/'hsi'/'scene' (all capture-verified) or 1-4."""
    index = PAGE_NAMES.get(name_or_index, name_or_index)
    if not isinstance(index, int) or isinstance(index, bool) or not 1 <= index <= 4:
        raise GvmError(f"page must be cct/hsi/scene or 1-4, got {name_or_index!r}")
    return build_frame(MODE_PAGE, index)


async def _ascan(timeout):
    import bleak

    found = []
    result = await bleak.BleakScanner.discover(timeout=timeout, return_adv=True)
    pairs = result.values() if isinstance(result, dict) else ((device, None) for device in result)
    for device, adv in pairs:
        rssi = getattr(adv, "rssi", None)
        if rssi is None:
            rssi = getattr(device, "rssi", None)
        found.append({"address": device.address, "name": device.name, "rssi": rssi})
    return sorted(found, key=lambda item: (item["name"] or "~", item["address"]))


def _bleak_error():
    """ImportError message when Bleak is missing, else None (fast fail)."""
    try:
        import bleak  # noqa: F401
    except ImportError as exc:
        return f"bleak backend unavailable ({exc}); pip install bleak"
    return None


def scan_devices(timeout=8):
    """BLE scan. Returns [{address, name, rssi}]. Needs BlueZ + Bleak."""
    err = _bleak_error()
    if err:
        raise GvmError(err)
    return asyncio.run(_ascan(timeout))


async def _aconnect(address, timeout):
    import bleak

    client = bleak.BleakClient(address, timeout=timeout)
    await client.connect()
    if not client.is_connected:
        raise GvmError(f"could not connect to {address}")
    return client


def _resolve_control(client):
    for service in client.services:
        for char in service.characteristics:
            if str(char.uuid).lower() == CONTROL_UUID:
                return str(char.uuid)
    raise GvmError(f"control characteristic {CONTROL_UUID} missing from the GATT table")


async def _aprobe(address, timeout):
    client = await _aconnect(address, timeout)
    try:
        services = []
        for service in client.services:
            chars = [
                {"uuid": str(char.uuid), "handle": char.handle, "props": list(char.properties)}
                for char in service.characteristics
            ]
            services.append({"uuid": str(service.uuid), "characteristics": chars})
        try:
            control = _resolve_control(client)
        except GvmError:
            control = None
        return {"address": address, "control_uuid": control, "services": services}
    finally:
        await client.disconnect()


async def _awrite(address, frame, timeout):
    client = await _aconnect(address, timeout)
    try:
        uuid = _resolve_control(client)
        await client.write_gatt_char(uuid, frame, response=False)
        return {"address": address, "bytes": len(frame), "frame": frame.hex()}
    finally:
        await client.disconnect()


async def _awrite_many(address, frames, timeout):
    """Write every frame on one connection. Returns one outcome per frame.

    A failed write stops the batch: later frames are reported unsent rather
    than shipped into an unknown panel state.
    """
    client = await _aconnect(address, timeout)
    try:
        uuid = _resolve_control(client)
        outcomes = []
        for frame in frames:
            try:
                await client.write_gatt_char(uuid, frame, response=False)
            except Exception as exc:  # noqa: BLE001 - report per frame, stop the batch
                outcomes.append({"ok": False, "error": str(exc)})
                unsent = len(frames) - len(outcomes)
                outcomes.extend({"ok": False, "error": f"not sent: {exc}"} for _ in range(unsent))
                break
            outcomes.append(
                {
                    "ok": True,
                    "sent": {"address": address, "bytes": len(frame), "frame": frame.hex()},
                }
            )
        return outcomes
    finally:
        await client.disconnect()


class GvmBleDriver(Driver):
    """One GVM panel at a fixed BLE address.

    Commands (exactly one key): {"power": "on"|"off"}, {"brightness": 0-100},
    {"cct": 3200-5600 step 100}, {"hue": 0-360 step 5}, {"saturation": 0-100},
    {"scene": 1-18}, {"page": "cct"|"hsi"|"scene"|1-4}.
    """

    def __init__(self, driver_id, address, timeout=15):
        super().__init__(driver_id)
        self.address = address
        self.timeout = timeout

    def capabilities(self):
        return {
            "family": "gvm",
            "transport": "ble",
            "control_uuid": CONTROL_UUID,
            "commands": ["power", "brightness", "cct", "hue", "saturation", "scene", "page"],
        }

    def get_state(self):
        """Reachability probe: the panel reports nothing, so connect + report."""
        err = _bleak_error()
        if err:
            return DriverResult(ok=False, state={"reachable": False}, error=err)
        try:
            info = asyncio.run(_aprobe(self.address, self.timeout))
            return DriverResult(
                ok=True,
                state={
                    "reachable": True,
                    "address": self.address,
                    "control_uuid": info["control_uuid"],
                    "services": len(info["services"]),
                },
            )
        except Exception as exc:  # noqa: BLE001 - drivers report, never raise
            return DriverResult(ok=False, state={"reachable": False}, error=str(exc))

    def _frame_for(self, command):
        if not isinstance(command, dict):
            raise GvmError("command must be an object")
        if "power" in command or "on" in command:
            want = command.get("power")
            if want is None and isinstance(command.get("on"), bool):
                want = "on" if command["on"] else "off"
            if want not in ("on", "off"):
                raise GvmError('power needs "on"|"off"')
            return power_frame(want == "on"), {"power": want}
        if "brightness" in command:
            value = command["brightness"]
            return brightness_frame(value), {"brightness": value}
        if "cct" in command:
            value = command["cct"]
            return cct_frame(value), {"cct": value}
        if "hue" in command:
            value = command["hue"]
            return hue_frame(value), {"hue": value}
        if "saturation" in command:
            value = command["saturation"]
            return saturation_frame(value), {"saturation": value}
        if "scene" in command:
            value = command["scene"]
            return scene_frame(value), {"scene": value}
        if "page" in command:
            value = command["page"]
            return page_frame(value), {"page": value}
        raise GvmError("command needs one of: power, brightness, cct, hue, saturation, scene, page")

    def apply(self, command):
        try:
            frame, summary = self._frame_for(command)
        except GvmError as exc:
            return DriverResult(ok=False, error=str(exc))
        err = _bleak_error()
        if err:
            return DriverResult(ok=False, state=dict(summary), error=err)
        try:
            sent = asyncio.run(_awrite(self.address, frame, self.timeout))
            _record_look(self.address, summary)
            return DriverResult(ok=True, state={**summary, **sent})
        except Exception as exc:  # noqa: BLE001 - drivers report, never raise
            return DriverResult(ok=False, state=dict(summary), error=str(exc))

    def apply_many(self, commands):
        """Write several single-key commands on ONE connection (one show-step beat).

        Plan-then-apply: every command is validated before anything ships, so
        one bad key sends nothing. Returns one DriverResult per command.
        """
        planned = []
        for command in commands:
            try:
                planned.append(self._frame_for(command))
            except GvmError as exc:
                return [DriverResult(ok=False, error=str(exc)) for _ in commands]
        if not planned:
            return []
        err = _bleak_error()
        if err:
            return [DriverResult(ok=False, state=dict(summary), error=err) for _, summary in planned]
        try:
            outcomes = asyncio.run(
                _awrite_many(self.address, [frame for frame, _ in planned], self.timeout)
            )
        except Exception as exc:  # noqa: BLE001 - drivers report, never raise
            return [
                DriverResult(ok=False, state=dict(summary), error=str(exc))
                for _, summary in planned
            ]
        results = []
        for (_, summary), outcome in zip(planned, outcomes):
            if outcome.get("ok"):
                _record_look(self.address, summary)
                results.append(DriverResult(ok=True, state={**summary, **outcome["sent"]}))
            else:
                results.append(
                    DriverResult(ok=False, state=dict(summary), error=outcome.get("error"))
                )
        while len(results) < len(planned):
            results.append(DriverResult(ok=False, error="batch returned too few outcomes"))
        return results

    def safe_stop(self):
        """Power the panel off. Best-effort, never raises."""
        err = _bleak_error()
        if err:
            return DriverResult(ok=False, state={"power": "off?"}, error=err)
        try:
            sent = asyncio.run(_awrite(self.address, power_frame(False), self.timeout))
            _record_look(self.address, {"power": "off"})
            return DriverResult(ok=True, state={"power": "off", **sent})
        except Exception as exc:  # noqa: BLE001 - safe_stop must never raise
            return DriverResult(ok=False, state={"power": "off?"}, error=str(exc))

    def probe(self):
        """Connect and dump the GATT table plus the resolved control UUID."""
        err = _bleak_error()
        if err:
            return DriverResult(ok=False, error=err)
        try:
            return DriverResult(ok=True, state=asyncio.run(_aprobe(self.address, self.timeout)))
        except Exception as exc:  # noqa: BLE001 - probe must never raise
            return DriverResult(ok=False, error=str(exc))
