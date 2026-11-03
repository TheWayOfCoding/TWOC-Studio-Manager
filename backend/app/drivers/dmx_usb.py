# SPDX-License-Identifier: AGPL-3.0-or-later
"""USB-DMX driver: one OpenDMX-style universe over a USB serial port.

Framing is the bench-proven sequence (break + mark-after-break + 250000 8N2
start-code frame, repeated so fixtures latch). Fixtures hold last look, so
applies send a short burst rather than streaming forever.
"""

import functools
import math
import os
import threading
import time

from .base import Driver, DriverResult

BAUDRATE = 250000
UNIVERSE_SIZE = 512
FRAMES_PER_APPLY = 8
# Generous in-spec timing: Windows USB scheduling jitters sub-ms control, so
# wide margins beat fast frames (break: 88us min, no max; MAB: 8us min).
BREAK_S = 0.005
MAB_S = 0.002
FRAME_GAP_S = 0.025

_port_locks = {}
_port_locks_guard = threading.Lock()


def _port_lock(port):
    with _port_locks_guard:
        return _port_locks.setdefault(port, threading.Lock())


def _locked(fn):
    """Serialize all port access: overlapping opens steal each other's bytes."""

    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with _port_lock(self.port):
            return fn(self, *args, **kwargs)

    return wrapper


class DmxError(Exception):
    """Serial port or interface failure."""


def default_port():
    """First USB serial port that is not the IR module's, or None.

    Cross-platform via pyserial names; excluding IR_PORT keeps the two
    FTDI adapters apart when both are plugged in.
    """
    try:
        from serial.tools import list_ports
    except ImportError:
        return None
    ir_port = os.environ.get("IR_PORT")
    usb = sorted(
        info.device
        for info in list_ports.comports()
        if "usb" in f"{info.hwid or ''} {info.description or ''}".lower() and info.device != ir_port
    )
    return usb[0] if usb else None


_shared_drivers = {}
_shared_drivers_guard = threading.Lock()


def get_shared_driver(port=None, driver_cls=None):
    """One cached driver per (class, port): the buffer remembers last levels.

    Fades walk from the buffered levels, so every surface driving one
    universe (bench, modes) shares this instance instead of each replaying
    from zero. The class joins the key so patched test doubles never collide.
    """
    cls = driver_cls or DmxUsbDriver
    resolved = port or os.environ.get("DMX_PORT") or default_port()
    key = (cls, resolved)
    with _shared_drivers_guard:
        if key not in _shared_drivers:
            _shared_drivers[key] = cls("dmx-universe", resolved)
        return _shared_drivers[key]


def open_port(port, timeout=2.0):
    """Open the port at DMX line settings. Raises DmxError with a human cause."""
    if port is None:
        raise DmxError("no USB DMX port detected; set DMX_PORT (e.g. COM4 or /dev/ttyUSB0)")
    try:
        import serial
    except ImportError as exc:
        raise DmxError("pyserial is not installed") from exc
    try:
        handle = serial.Serial(
            port,
            baudrate=BAUDRATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_TWO,
            timeout=timeout,
            write_timeout=timeout,
        )
    except Exception as exc:
        raise DmxError(f"cannot open {port}: {exc}") from exc
    try:
        handle.rts = True  # TX-enable for RS485-dongle hardware; ignored otherwise
    except Exception:  # noqa: BLE001 - best effort only
        pass
    return handle


def _frame_for(channels):
    """Build a start-code + 512-byte frame from a {channel: value} mapping."""
    frame = bytearray(UNIVERSE_SIZE + 1)
    for channel, value in (channels or {}).items():
        if not isinstance(channel, int) or not 1 <= channel <= UNIVERSE_SIZE:
            raise DmxError(f"channel out of range 1..{UNIVERSE_SIZE}: {channel!r}")
        if not isinstance(value, int) or not 0 <= value <= 255:
            raise DmxError(f"value out of range 0..255: {value!r}")
        frame[channel] = value
    return frame


def _display_percent(value):
    """Fixture % readout: floor, matching observed Weeylite behavior (25->9)."""
    return min(100, int(value * 100 / 255))


def _dmx_for_percent(pct):
    """Smallest DMX value displaying at least pct (10% -> 26, user-calibrated)."""
    return min(255, int(math.ceil(max(pct, 0) * 255 / 100)))


# Gentle range: bench values at or below these need no acknowledgment; above
# them callers pass allow_bright. Fixture-calibrated: 26 is nominal 10%.
GENTLE_DMX_MAX = 26
GENTLE_PERCENT_MAX = 10


def dmx_for_percent(pct):
    """Smallest DMX value displaying at least pct (10% -> 26, fixture-calibrated)."""
    return _dmx_for_percent(pct)


def dmx_for_cct(kelvin, low, high):
    """Linear kelvin -> DMX over a fixture CCT range (Ninja 400: 2500..8500)."""
    if high <= low:
        raise DmxError(f"bad CCT range: {low}..{high}")
    return min(255, max(0, int(round((kelvin - low) * 255 / (high - low)))))


def display_percent(value):
    """Fixture % readout: floor, matching observed Weeylite behavior (25->9)."""
    return _display_percent(value)


def cct_for_dmx(value, low, high):
    """Inverse of dmx_for_cct: DMX value -> kelvin over a fixture CCT range."""
    if high <= low:
        raise DmxError(f"bad CCT range: {low}..{high}")
    return int(round(low + max(0, min(255, value)) * (high - low) / 255))


def _send_frames(handle, frame, count):
    for _ in range(count):
        handle.break_condition = True
        time.sleep(BREAK_S)
        handle.break_condition = False
        time.sleep(MAB_S)
        written = handle.write(bytes(frame))
        if written != len(frame):
            raise DmxError(f"short write: {written}/{len(frame)} bytes (USB trouble)")
        handle.flush()
        time.sleep(FRAME_GAP_S)


class DmxUsbDriver(Driver):
    """One DMX universe on a USB serial port. Keeps a channel buffer."""

    def __init__(self, driver_id, port=None, universe=0, frames_per_apply=FRAMES_PER_APPLY):
        super().__init__(driver_id)
        self.port = port or os.environ.get("DMX_PORT") or default_port()
        self.universe = universe
        self.frames_per_apply = frames_per_apply
        self.channels = {}
        self.blackout = False
        self.emitted = False  # any frame sent since hub start (snapshot honesty)

    def capabilities(self):
        return {
            "family": "dmx",
            "transport": "usb-serial",
            "protocol": "DMX512-A",
            "universe": self.universe,
            "commands": ["channels", "channel", "blackout", "state"],
        }

    def get_state(self):
        return DriverResult(
            ok=True,
            state={
                "port": self.port,
                "universe": self.universe,
                "blackout": self.blackout,
                "emitted": self.emitted,
                "channels": {str(ch): val for ch, val in sorted(self.channels.items()) if val},
            },
        )

    @_locked
    def apply(self, command):
        if not isinstance(command, dict):
            return DriverResult(ok=False, error="command must be an object")
        try:
            if "blackout" in command:
                self.blackout = bool(command["blackout"])
                self._emit(self._live_channels())
            elif "channels" in command:
                if not isinstance(command["channels"], dict):
                    return DriverResult(ok=False, error="channels must be an object")
                merged = dict(self.channels)
                for raw_channel, value in command["channels"].items():
                    merged[int(raw_channel)] = value
                _frame_for(merged)  # validate before mutating
                self.channels = merged
                self._emit(self._live_channels())
            elif "channel" in command:
                merged = dict(self.channels)
                merged[int(command["channel"])] = command.get("value")
                _frame_for(merged)
                self.channels = merged
                self._emit(self._live_channels())
            else:
                return DriverResult(ok=False, error="command needs channels, channel, or blackout")
        except (DmxError, ValueError, TypeError) as exc:
            return DriverResult(ok=False, error=str(exc))
        return self.get_state()

    @_locked
    def fade(self, target, seconds, fps=20, units="dmx", dwell_s=None):
        """Ramp buffer channels to target values.

        Two timings, never both: seconds totals the ramp (interpolated,
        all channels landing together), or dwell_s holds every step for
        that long (dmx units walk every value, percent units walk every
        shown %; short channels hold their final value while long ones
        finish). Dwell steps re-emit their frame for the whole hold: the
        USB link drops single-shot frames and fixtures latch only what
        arrives. Value sequences are precomputed, then played against
        absolute deadlines (fixed-timestep pacing: no drift, no CPU-speed
        coupling). Clears an active blackout first. seconds <= 0 without
        dwell applies immediately. Returns frames sent.
        """
        if dwell_s is not None:
            if isinstance(dwell_s, bool) or not isinstance(dwell_s, (int, float)) or dwell_s < 0:
                raise DmxError(f"bad dwell_s: {dwell_s!r}")
            if seconds > 0:
                raise DmxError("pass seconds or dwell_s, not both")
        if not isinstance(target, dict):
            raise DmxError("fade target must be an object")
        if units == "percent":
            goal = {}
            for channel, pct in target.items():
                if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not 0 <= pct <= 100:
                    raise DmxError(f"percent out of range 0..100: {pct!r}")
                goal[int(channel)] = pct
        elif units == "dmx":
            goal = {int(ch): val for ch, val in target.items()}
        else:
            raise DmxError(f'units must be "dmx"|"percent": {units!r}')
        final = (
            {ch: _dmx_for_percent(pct) for ch, pct in goal.items()}
            if units == "percent"
            else dict(goal)
        )
        _frame_for({**self.channels, **final})  # validate before moving
        self.blackout = False
        if dwell_s is not None:
            walks = {}
            for channel, end in goal.items():
                start = self.channels.get(channel, 0)
                if units == "percent":
                    first, last = _display_percent(start), int(round(end))
                    step_dir = 1 if last >= first else -1
                    levels = list(range(first, last + step_dir, step_dir)) or [first]
                    walks[channel] = [_dmx_for_percent(level) for level in levels]
                else:
                    step_dir = 1 if end >= start else -1
                    walks[channel] = list(range(start, end + step_dir, step_dir)) or [start]
            frames = max((len(walk) for walk in walks.values()), default=1)
            seqs = {
                channel: walk + [walk[-1]] * (frames - len(walk)) for channel, walk in walks.items()
            }
        else:
            total = max(seconds, 0)
            frames = max(2, int(total * fps) + 1) if total > 0 else 1
            seqs = {}
            for channel, end in goal.items():
                start = self.channels.get(channel, 0)
                if units == "percent":
                    first, last = _display_percent(start), int(round(end))
                    step_dir = 1 if last >= first else -1
                    levels = list(range(first, last + step_dir, step_dir)) or [first]
                    count = len(levels)
                    seqs[channel] = [
                        _dmx_for_percent(levels[min(count - 1, i * count // frames)])
                        for i in range(frames)
                    ]
                elif frames > 1:
                    seqs[channel] = [
                        int(round(start + (end - start) * i / (frames - 1))) for i in range(frames)
                    ]
                else:
                    seqs[channel] = [end]
        handle = open_port(self.port)
        started = time.time()
        try:
            sent = 0
            for step in range(frames):
                frame = dict(self.channels)
                for channel in goal:
                    frame[channel] = seqs[channel][step]
                wire = _frame_for(frame)
                if dwell_s is not None:
                    deadline = started + dwell_s * (step + 1)
                    while True:
                        _send_frames(handle, wire, 1)
                        sent += 1
                        if time.time() >= deadline:
                            break
                else:
                    _send_frames(handle, wire, 1)
                    sent += 1
                    if frames > 1:
                        deadline = started + total * (step + 1) / (frames - 1)
                        time.sleep(max(0, deadline - time.time()))
        finally:
            try:
                handle.close()
            except Exception:  # noqa: BLE001 - close is best effort
                pass
        self.channels.update(final)
        return sent

    @_locked
    def hold(self, channels, seconds):
        """Stream one mapping for a fixed time (sweeps). Returns frames sent."""
        frame = _frame_for(channels)
        handle = open_port(self.port)
        try:
            sent = 0
            end = time.time() + max(seconds, 0)
            while time.time() < end:
                _send_frames(handle, frame, 1)
                sent += 1
            return sent
        finally:
            try:
                handle.close()
            except Exception:  # noqa: BLE001 - close is best effort
                pass

    def safe_stop(self):
        """Blackout the universe. Best-effort, never raises."""
        try:
            self.blackout = True
            self._emit({})
        except Exception:  # noqa: BLE001 - safe_stop must never raise
            pass
        return DriverResult(ok=True, state={"port": self.port, "blackout": True})

    def probe(self):
        """Open check only: never emits frames, never darkens fixtures."""
        try:
            handle = open_port(self.port, timeout=1.0)
        except DmxError as exc:
            return DriverResult(ok=False, error=str(exc))
        try:
            handle.close()
        except Exception:  # noqa: BLE001 - close is best effort
            pass
        return DriverResult(ok=True, state={"port": self.port, "open": True, "universe": self.universe})

    def _live_channels(self):
        return {} if self.blackout else self.channels

    def _emit(self, channels):
        frame = _frame_for(channels)
        handle = open_port(self.port)
        try:
            _send_frames(handle, frame, self.frames_per_apply)
        finally:
            try:
                handle.close()
            except Exception:  # noqa: BLE001 - close is best effort
                pass
        self.emitted = True
