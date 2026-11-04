# SPDX-License-Identifier: AGPL-3.0-or-later
"""Weeylite/Viltrox Ninja driver over BLE advertisements (no pairing, no GATT).

Speaks the reverse-engineered protocol in docs/ninja-ble.md: every command
is a complete 27-byte look broadcast as the scan-response payload of a
fresh extended advertisement. The hub mimics the app with a WinRT
advertisement publisher (Windows-only); the same bytes go out whether the
target is a Ninja 10, 20, or 400 — address byte selects channel/group.

Transport risk, stated plainly: the app puts the command in the SCAN
RESPONSE, and WinRT exposes no scan-response API, so the hub publishes
the identical bytes as regular advertisement manufacturer data instead.
Whether fixtures accept that placement is verified live on the bench —
a silent light with ok:true means placement failed, not the frame.

Synchronous. Call only outside a running event loop.
"""

import threading
import time

from .base import Driver, DriverResult

COMPANY_ID = 0x004C
FRAME_LEN = 27

MIN_CHANNEL = 1
MAX_CHANNEL = 19
GROUPS = "ABCDEF"

MIN_BRIGHTNESS = 0
MAX_BRIGHTNESS = 100
MIN_CCT = 2500
MAX_CCT = 8500
CCT_STEP = 100
MIN_GM = -10
MAX_GM = 10
PRESET_TILES = [3000, 4000, 5200, 5500, 6000, 7000]  # app tile order, index 0-5
CUSTOM_PRESET = 0x64  # slider-set CCT carries this in the preset slot
MIN_SCENE = 0
MAX_SCENE = 9
SCENES = ["Flash", "Burst", "Flash lamp", "Blink", "Weld", "SOS",
          "Candlelight", "Flame", "CCT LOOP", "TV"]
FX_MODE_MARKER = 0x02  # preset slot carries this on scene broadcasts (observed)

GENTLE_PERCENT_MAX = 10  # bench rule (mirrors DMX): brighter needs allow_bright


class NinjaError(Exception):
    """Bad look values, unknown scene, or publisher failure."""


def address_byte(channel, group):
    """Address byte: channel x 8 + group index + 1. Verified (3,A)=0x19."""
    if (
        not isinstance(channel, int)
        or isinstance(channel, bool)
        or not MIN_CHANNEL <= channel <= MAX_CHANNEL
    ):
        raise NinjaError(f"channel must be an int {MIN_CHANNEL}-{MAX_CHANNEL}, got {channel!r}")
    if not isinstance(group, str) or len(group) != 1 or group.upper() not in GROUPS:
        raise NinjaError(f"group must be one of A-F, got {group!r}")
    return channel * 8 + GROUPS.index(group.upper()) + 1


def _check_int(name, value, lo, hi):
    if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
        raise NinjaError(f"{name} must be an int {lo}-{hi}, got {value!r}")
    return value


def _check_cct(kelvin):
    _check_int("cct", kelvin, MIN_CCT, MAX_CCT)
    if kelvin % CCT_STEP:
        raise NinjaError(f"cct must step {CCT_STEP}K, got {kelvin!r}")
    return kelvin


def build_look(channel, group, brightness, cct=5500, gm=0, preset=None, counter=0):
    """Full 27-byte steady look. `preset` None follows the app: tile index
    when cct matches a tile, CUSTOM_PRESET for slider values."""
    addr = address_byte(channel, group)
    _check_int("brightness", brightness, MIN_BRIGHTNESS, MAX_BRIGHTNESS)
    _check_cct(cct)
    _check_int("gm", gm, MIN_GM, MAX_GM)
    _check_int("counter", counter, 0, 255)
    if preset is None:
        preset = PRESET_TILES.index(cct) if cct in PRESET_TILES else CUSTOM_PRESET
    else:
        _check_int("preset", preset, 0, 5)
    frame = bytearray(FRAME_LEN)
    frame[0:6] = bytes((0x1A, 0xFF, 0x4C, 0x00, 0x02, 0x15))
    frame[6] = 0xEF
    frame[7] = addr
    frame[8] = 0x00
    frame[9] = cct // 100
    frame[10] = brightness
    frame[11] = preset
    frame[12] = 0x00
    frame[13] = gm + 10
    frame[14:18] = bytes(4)
    frame[18] = 0x02
    frame[19] = 0xFE
    frame[20] = counter
    frame[21] = brightness
    frame[22:27] = bytes((0x00, 0x0A, 0x00, 0x6E, 0xC5))
    return bytes(frame)


def manufacturer_payload(frame):
    """Manufacturer-data section content for a 27-byte frame: everything
    after len/type/company (keeps the 0215 beacon prefix — slicing it off
    makes fixtures ignore the broadcast, as a live phone capture proved)."""
    frame = bytes(frame)
    if len(frame) != FRAME_LEN:
        raise NinjaError(f"frame must be {FRAME_LEN} bytes, got {len(frame)}")
    return frame[4:]


def scene_index(name_or_index):
    """0-based scene index from int 0-9 or case-insensitive scene name."""
    if isinstance(name_or_index, int) and not isinstance(name_or_index, bool):
        if MIN_SCENE <= name_or_index <= MAX_SCENE:
            return name_or_index
    elif isinstance(name_or_index, str):
        want = name_or_index.strip().lower()
        for i, name in enumerate(SCENES):
            if name.lower() == want:
                return i
    raise NinjaError(f"scene must be 0-9 or one of {SCENES}, got {name_or_index!r}")


def build_scene(channel, group, scene, brightness, counter=0):
    """Full 27-byte scene look. Scene index rides bytes 9+12 (capture);
    byte 11 carries the FX marker, GM slot stays zeroed (as observed)."""
    addr = address_byte(channel, group)
    index = scene_index(scene)
    _check_int("brightness", brightness, MIN_BRIGHTNESS, MAX_BRIGHTNESS)
    _check_int("counter", counter, 0, 255)
    frame = bytearray(FRAME_LEN)
    frame[0:6] = bytes((0x1A, 0xFF, 0x4C, 0x00, 0x02, 0x15))
    frame[6] = 0xEF
    frame[7] = addr
    frame[8] = FX_MODE_MARKER
    frame[9] = index
    frame[10] = brightness
    frame[11] = FX_MODE_MARKER
    frame[12] = index
    frame[13] = 0x00
    frame[14:17] = bytes(3)
    frame[17] = FX_MODE_MARKER
    frame[18] = 0x02
    frame[19] = 0xFE
    frame[20] = counter
    frame[21] = brightness
    frame[22:27] = bytes((0x00, 0x0A, 0x00, 0x6E, 0xC5))
    return bytes(frame)


class Advertiser:
    """One publish cycle. Subclasses implement publish(); never raise raw."""

    def publish(self, payload, hold_s):
        """Broadcast payload for hold_s seconds. Returns a JSON dict."""
        raise NotImplementedError


class MockAdvertiser(Advertiser):
    """Recording stand-in: no radio, keeps every payload for assertions."""

    def __init__(self):
        self.published = []

    def publish(self, payload, hold_s):
        entry = {"payload": bytes(payload).hex(), "bytes": len(payload), "hold_s": hold_s}
        self.published.append(entry)
        return {"mock": True, **entry}


def winrt_available():
    """Import check for the WinRT publisher backend. Returns error or None."""
    try:
        import winsdk.windows.devices.bluetooth.advertisement  # noqa: F401
        import winsdk.windows.storage.streams  # noqa: F401
    except ImportError as exc:
        return f"WinRT BLE unavailable ({exc}); pip install winsdk on Windows"
    return None


def _default_adapter():
    """The default Bluetooth adapter, or None when unavailable. Never raises."""
    try:
        import asyncio

        from winsdk.windows.devices.bluetooth import BluetoothAdapter

        async def _query():
            return await BluetoothAdapter.get_default_async()

        return asyncio.run(_query())
    except Exception:  # noqa: BLE001 - unavailable is reported, never raised
        return None


def adapter_extended_supported():
    """Default adapter's extended-advertising support: True/False, or None
    when the query is unavailable. Never raises (bench honesty aid).

    Capability note only: fixtures accept the legacy fallback as well
    (field-verified Nov 3 on two dongles), but a radio stuck in legacy
    after the app-native mode failed is worth seeing on the bench.
    """
    adapter = _default_adapter()
    if adapter is None:
        return None
    try:
        return bool(adapter.is_extended_advertising_supported)
    except Exception:  # noqa: BLE001
        return None


def _default_adapter_address():
    """Default adapter's Bluetooth address as an int, or None. Never raises."""
    adapter = _default_adapter()
    if adapter is None:
        return None
    try:
        return int(adapter.bluetooth_address)
    except Exception:  # noqa: BLE001
        return None


def _restart_bluetooth_service():
    """Restart the Windows Bluetooth service once. True on clean exit.

    Field remedy (Nov 3): the stack can wedge TX-silent — publishers
    report Started while nothing reaches the air. A service restart is
    the only observed cure. Best-effort: False on any failure (including
    non-Windows), never raises, so a failed heal never blocks a send.
    """
    try:
        import subprocess

        done = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Restart-Service bthserv -Force"],
            capture_output=True,
            timeout=30,
            check=False,
        )
        return done.returncode == 0
    except Exception:  # noqa: BLE001
        return False


class WinRtAdvertiser(Advertiser):
    """Real publisher via WinRT advertisement (Windows + BLE radio).

    Puts the command bytes in the advertisement manufacturer data section
    (company 0x004C); WinRT offers no scan-response API, so this is the
    closest mimicry of the app's scan-response placement.
    """

    def __init__(self, extended=True, anonymous=True):
        self.extended = extended
        self.anonymous = anonymous

    def _new_publisher(self, adv, extended, anonymous):
        from winsdk.windows.devices.bluetooth.advertisement import (
            BluetoothLEAdvertisementPublisher,
        )

        try:
            pub = BluetoothLEAdvertisementPublisher(adv)
        except TypeError:
            pub = BluetoothLEAdvertisementPublisher()
            pub.advertisement = adv
        pub.use_extended_advertisement = extended
        pub.is_anonymous = anonymous
        return pub

    def _attempt(self, adv, payload, hold_s, extended, anonymous):
        pub = self._new_publisher(adv, extended, anonymous)
        try:
            pub.start()
        except Exception as exc:  # noqa: BLE001 - translated below
            raise NinjaError(f"advertiser failed to start: {exc}") from exc
        status_start = str(pub.status)
        try:
            if "abort" in status_start.lower():
                raise NinjaError(f"advertiser aborted ({status_start}); radio missing or blocked?")
            time.sleep(max(hold_s, 0))
            status_end = str(pub.status)
            if "abort" in status_end.lower():
                raise NinjaError(
                    f"advertiser aborted ({status_end}); radio missing or blocked?"
                )
        finally:
            try:
                pub.stop()
            except Exception:  # noqa: BLE001, S110 - stop is best-effort
                pass
        mode = "extended" if extended else "legacy"
        return {
            "bytes": len(payload),
            "status": status_end,
            "status_start": status_start,
            "hold_s": hold_s,
            "mode": mode,
        }

    def _build_adv(self, payload):
        from winsdk.windows.devices.bluetooth.advertisement import (
            BluetoothLEAdvertisement,
            BluetoothLEManufacturerData,
        )
        from winsdk.windows.storage.streams import DataWriter

        writer = DataWriter()
        writer.write_bytes(manufacturer_payload(payload))
        section = BluetoothLEManufacturerData()
        section.company_id = COMPANY_ID
        section.data = writer.detach_buffer()
        adv = BluetoothLEAdvertisement()
        adv.manufacturer_data.append(section)
        return adv

    # Adapter self-heal state (class-level: every driver instance shares
    # one radio, so the baseline must be shared too).
    _heal_lock = threading.Lock()
    _last_adapter_address = None
    _adapter_absent = False
    _heal_pending = False
    _monitor_started = False
    _MONITOR_INTERVAL_S = 10.0
    # The wedge can also form mid-run with no adapter event at all (field
    # finding: sends Started, fixture dark, service restart cured it).
    # The only signal then is an idle gap, so the first publish after one
    # heals before sending; rapid-fire sends during a show never do.
    _last_publish_at = None
    _idle_heal_pending = False
    _IDLE_HEAL_AFTER_S = 120.0
    _legacy_only = False
    _started_heal_done = False
    _START_HEAL_WAIT_S = 30.0
    _START_HEAL_POLL_S = 0.5

    @classmethod
    def _observe_adapter(cls, address):
        """Fold one adapter observation into the heal state.

        Re-enumeration (dongle swap, or unplug/replug of the same dongle)
        can leave the stack TX-silent, so both a changed address and a
        disappear/reappear cycle mark a heal pending. First sighting only
        records the baseline (hub startup heals the boot case separately).
        """
        with cls._heal_lock:
            if address is None:
                if cls._last_adapter_address is not None:
                    cls._adapter_absent = True
                return
            if cls._last_adapter_address is None:
                cls._last_adapter_address = address
                cls._adapter_absent = False
                return
            if cls._adapter_absent or address != cls._last_adapter_address:
                cls._heal_pending = True
                cls._legacy_only = False  # new radio: re-probe extended once
            cls._adapter_absent = False
            cls._last_adapter_address = address

    @classmethod
    def _consume_adapter_heal(cls):
        """Run a pending service restart once. True when it actually ran.

        A failed restart stays pending, so the next publish retries.
        """
        with cls._heal_lock:
            pending = cls._heal_pending
        if not pending:
            return False
        if not _restart_bluetooth_service():
            return False
        with cls._heal_lock:
            cls._heal_pending = False
        return True

    @classmethod
    def _wait_for_adapter(cls, address):
        """Poll until the default adapter answers, up to the boot wait.

        Cold-boot race: hub-serve can start before the USB dongle has
        enumerated, so the first observation can be None even though the
        radio arrives seconds later. Returns the address seen, or None
        when the wait expires (the publish then fails honestly instead
        of hanging forever).
        """
        if address is not None or cls._START_HEAL_WAIT_S <= 0:
            return address
        deadline = time.monotonic() + cls._START_HEAL_WAIT_S
        while time.monotonic() < deadline:
            time.sleep(min(cls._START_HEAL_POLL_S, max(deadline - time.monotonic(), 0)))
            address = _default_adapter_address()
            if address is not None:
                return address
        return None

    @classmethod
    def _consume_start_heal(cls, address):
        """Restart the BT service before this process's first publish.

        The hub-serve pre-serve restart fires before the dongle may
        exist, and until now the first publish after a hub start never
        healed at all (field failure: full power-off, first bench send
        minutes later, light dead). So the first publish waits for the
        adapter, restarts the service, and only then sends. Exactly one
        start heal runs per process; a failed restart stays pending
        and retries on the next publish.
        """
        with cls._heal_lock:
            if cls._started_heal_done:
                return False
        address = cls._wait_for_adapter(address)
        if address is None:
            return False
        cls._observe_adapter(address)
        if not _restart_bluetooth_service():
            return False
        cls._wait_for_adapter(_default_adapter_address())
        with cls._heal_lock:
            cls._started_heal_done = True
        return True

    @classmethod
    def _consume_idle_heal(cls):
        """Restart the BT service when the last publish is long past.

        True when a restart actually ran. A failed restart leaves the
        idle mark untouched, so the next publish retries (same contract
        as the adapter heal). No publish yet (hub just started, pre-serve
        already restarted the service) never heals.
        """
        now = time.monotonic()
        with cls._heal_lock:
            last = cls._last_publish_at
            due = cls._idle_heal_pending or (
                last is not None and now - last >= cls._IDLE_HEAL_AFTER_S
            )
            if due:
                cls._idle_heal_pending = True
        if not due:
            return False
        if not _restart_bluetooth_service():
            return False
        with cls._heal_lock:
            cls._idle_heal_pending = False
            cls._last_publish_at = now
        return True

    @classmethod
    def _note_publish(cls):
        with cls._heal_lock:
            cls._last_publish_at = time.monotonic()

    @classmethod
    def start_adapter_monitor(cls):
        """Start the daemon thread watching for adapter re-enumeration.

        Publishes also observe the adapter directly; the monitor exists
        so a reseat is noticed even while the lights sit idle (the heal
        itself still runs just-in-time on the next publish).
        """
        with cls._heal_lock:
            if cls._monitor_started:
                return
            cls._monitor_started = True
        thread = threading.Thread(
            target=cls._adapter_monitor_loop, name="ninja-adapter-monitor", daemon=True
        )
        thread.start()

    @classmethod
    def _adapter_monitor_loop(cls):
        while True:
            try:
                cls._observe_adapter(_default_adapter_address())
            except Exception:  # noqa: BLE001 - monitor must never die loudly
                pass
            time.sleep(cls._MONITOR_INTERVAL_S)

    def publish(self, payload, hold_s):
        err = winrt_available()
        if err:
            raise NinjaError(err)
        payload = bytes(payload)
        address = _default_adapter_address()
        if address is not None:
            WinRtAdvertiser._observe_adapter(address)
        restarted = WinRtAdvertiser._consume_adapter_heal()
        heal_reason = "adapter" if restarted else None
        if not restarted and WinRtAdvertiser._consume_start_heal(address):
            restarted = True
            heal_reason = "start"
        if not restarted and WinRtAdvertiser._consume_idle_heal():
            restarted = True
            heal_reason = "idle"
        adv = self._build_adv(payload)
        if WinRtAdvertiser._legacy_only:
            # This radio already rejected extended once: go straight to
            # legacy. Repeating the failing start on every publish is
            # pure publisher churn (a wedge trigger) for zero benefit.
            result = self._attempt(adv, payload, hold_s, False, False)
        else:
            try:
                result = self._attempt(adv, payload, hold_s, self.extended, self.anonymous)
            except NinjaError as first:
                if not self.extended and not self.anonymous:
                    raise
                try:
                    result = self._attempt(adv, payload, hold_s, False, False)
                except NinjaError as second:
                    raise NinjaError(f"{first} (legacy retry also failed: {second})") from second
                result["fallback"] = str(first)
                WinRtAdvertiser._legacy_only = True
        WinRtAdvertiser._note_publish()
        if restarted:
            result["bt_service_restarted"] = True
            result["bt_heal_reason"] = heal_reason
        return result


class NinjaBleDriver(Driver):
    """One Ninja address (channel + group) over BLE advertisements.

    Every transmission is a COMPLETE look: partial commands merge into the
    last-sent look for this address (counter increments per broadcast).
    Commands: {"brightness": 0-100}, {"cct": 2500-8500 step 100},
    {"gm": -10-10}, {"preset": 0-5}, {"scene": 0-9|name},
    {"power": "on"|"off"} (off zeroes brightness; on restores),
    {"raw": hex} (27 verbatim bytes, bring-up only).
    """

    def __init__(self, driver_id, channel, group, backend="auto", hold_s=1.0, repeats=2,
                 default_cct=5500):
        super().__init__(driver_id)
        self.address = address_byte(channel, group)  # validates now, fails fast
        self.channel = channel
        self.group = group.upper()
        self.default_cct = _check_cct(default_cct)
        if isinstance(backend, Advertiser):
            self.advertiser = backend
            self.backend_name = type(backend).__name__
        elif backend == "mock":
            self.advertiser = MockAdvertiser()
            self.backend_name = "mock"
        elif backend == "auto":
            self.advertiser = WinRtAdvertiser()
            self.backend_name = "winrt"
        else:
            raise NinjaError(f"backend must be auto, mock, or an Advertiser, got {backend!r}")
        self.hold_s = hold_s
        self.repeats = repeats
        self.counter = 0
        self.look = {"brightness": 0, "cct": self.default_cct, "gm": 0, "preset": None,
                     "scene": None}
        self._sent = False  # nothing transmitted yet; partial looks are unsafe
        self._resume_brightness = 5  # power-on default until a real level is sent

    def capabilities(self):
        return {
            "family": "ninja",
            "transport": "ble-advertise",
            "backend": self.backend_name,
            "address": {"channel": self.channel, "group": self.group},
            "commands": ["power", "brightness", "cct", "gm", "preset", "scene", "raw"],
        }

    def get_state(self):
        """Hub-side memory for this address (fixtures never report back).
        Never transmits. `transmitted` false + counter 0 means nothing has
        gone out since hub start — the look shown is defaults, not fact."""
        return DriverResult(
            ok=True,
            state={
                "channel": self.channel,
                "group": self.group,
                "look": dict(self.look),
                "transmitted": self._sent,
                "counter": self.counter,
                "backend": self.backend_name,
                "winrt_error": winrt_available(),
            },
        )

    def seed_default_cct(self, kelvin):
        """Adopt a fixture-correct CCT default while pristine (pre-first-send
        only; never overrides a transmitted look). Fixed-daylight profiles
        seed their colorTempK so State reads honestly."""
        if not self._sent:
            self.look["cct"] = _check_cct(kelvin)

    def probe(self):
        """Backend availability. Never transmits."""
        if isinstance(self.advertiser, MockAdvertiser):
            return DriverResult(ok=True, state={"backend": "mock", "transmits": False})
        err = winrt_available()
        if err:
            return DriverResult(ok=False, error=err)
        return DriverResult(
            ok=True,
            state={
                "backend": "winrt",
                "imports": True,
                "extended_supported": adapter_extended_supported(),
            },
        )

    def _send(self, payload):
        """Publish repeats times (app parity: state changes go out twice)."""
        if not isinstance(self.repeats, int) or not 1 <= self.repeats <= 5:
            raise NinjaError(f"repeats must be an int 1-5, got {self.repeats!r}")
        sends = []
        for _ in range(self.repeats):
            stamped = bytes(payload[:20]) + bytes((self.counter,)) + bytes(payload[21:])
            try:
                outcome = self.advertiser.publish(stamped, self.hold_s)
            except NinjaError:
                raise
            except Exception as exc:  # noqa: BLE001 - drivers report, never raise
                raise NinjaError(f"publish failed: {exc}") from exc
            sends.append({"counter": self.counter, "payload": stamped.hex(), **outcome})
            self.counter = (self.counter + 1) % 256
        return sends

    def _plan(self, command):
        """Validate + merge into a (payload, summary) pair. Sends nothing."""
        if not isinstance(command, dict):
            raise NinjaError("command must be an object")
        if "raw" in command:
            raw = command["raw"]
            if not isinstance(raw, str):
                raise NinjaError("raw must be hex text")
            try:
                payload = bytes.fromhex(raw)
            except ValueError:
                raise NinjaError("raw must be hex text") from None
            if len(payload) != FRAME_LEN:
                raise NinjaError(f"raw must be {FRAME_LEN} bytes, got {len(payload)}")
            return payload, {"raw": payload.hex()}
        look = dict(self.look)
        if "power" in command or "on" in command:
            want = command.get("power")
            if want is None and isinstance(command.get("on"), bool):
                want = "on" if command["on"] else "off"
            if want not in ("on", "off"):
                raise NinjaError('power needs "on"|"off"')
            if want == "off":
                if look["brightness"] > 0:
                    self._resume_brightness = look["brightness"]
                look["brightness"] = 0
            else:
                look["brightness"] = self._resume_brightness
                if look["brightness"] <= 0:
                    look["brightness"] = self._resume_brightness = 5
        for key in ("brightness", "cct", "gm", "preset", "scene"):
            if key in command:
                look[key] = command[key]
                if key == "brightness" and look[key] > 0:
                    self._resume_brightness = look[key]
        if not any(k in command for k in ("power", "on", "brightness", "cct", "gm", "preset", "scene")):
            raise NinjaError("command needs one of: power, brightness, cct, gm, preset, scene, raw")
        if (
            not self._sent
            and "brightness" not in command
            and "power" not in command
            and "on" not in command
        ):
            raise NinjaError(
                "brightness unknown since hub start; include brightness once "
                "(a partial look would send the 0 default and darken the light)"
            )
        if look["scene"] is not None:
            payload = build_scene(self.channel, self.group, look["scene"], look["brightness"], 0)
        else:
            payload = build_look(
                self.channel, self.group, look["brightness"], look["cct"],
                look["gm"], look["preset"], 0,
            )
        return payload, {"look": look}

    def apply(self, command):
        try:
            payload, summary = self._plan(command)
        except NinjaError as exc:
            return DriverResult(ok=False, error=str(exc))
        try:
            sends = self._send(payload)
        except NinjaError as exc:
            return DriverResult(ok=False, state=dict(summary), error=str(exc))
        self._sent = True
        if "look" in summary:
            self.look = dict(summary["look"])
        return DriverResult(ok=True, state={**summary, "sends": sends})

    def apply_many(self, commands):
        """Merge several single-key commands into ONE broadcast look.

        The modes engine calls this when a step names more than one key, so
        a {brightness, cct} step lands as one beat instead of two smeared
        frames. Returns one DriverResult per command (the shared outcome).
        """
        merged = {}
        for command in commands or []:
            if isinstance(command, dict):
                merged.update(command)
        if not merged:
            return [DriverResult(ok=False, error="nothing to send") for _ in (commands or [])]
        outcome = self.apply(merged)
        return [outcome for _ in commands]

    def safe_stop(self):
        """Darken this address (brightness 0). Best-effort, never raises."""
        try:
            payload = build_look(self.channel, self.group, 0, self.default_cct, 0, None, 0)
            sends = self._send(payload)
            self.look["brightness"] = 0
            self._sent = True
            return DriverResult(ok=True, state={"brightness": 0, "sends": sends})
        except Exception as exc:  # noqa: BLE001 - safe_stop must never raise
            return DriverResult(ok=False, state={"brightness": "0?"}, error=str(exc))
