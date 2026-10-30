# SPDX-License-Identifier: AGPL-3.0-or-later
"""YS-IRTM USB-serial IR driver: NEC transceiver over UART (default 9600 8N1).

Receive: remote press -> module emits 3 bytes.
Transmit: write A1 F1 <d1> <d2> <d3> -> module blasts and replies F1.
"""

import functools
import threading
import time

from .base import Driver, DriverResult

SEND_PREFIX = bytes((0xA1, 0xF1))
CONFIRM_BYTE = 0xF1

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


class IrError(Exception):
    """Serial port or module failure."""


def default_port():
    """First USB serial port on this host, or None if none is detectable.

    Cross-platform: pyserial names COM ports on Windows, /dev/ttyUSB* on
    Linux, /dev/tty.usbserial* on macOS. Built-in ports (COM1, ttyS*)
    never match the USB filter. Several USB adapters: first sorted wins,
    override with IR_PORT.
    """
    try:
        from serial.tools import list_ports
    except ImportError:
        return None
    usb = sorted(
        info.device
        for info in list_ports.comports()
        if "usb" in f"{info.hwid or ''} {info.description or ''}".lower()
    )
    return usb[0] if usb else None


def open_port(port, baudrate=9600, timeout=2.0):
    """Open the serial port. Raises IrError with a human-readable cause."""
    if port is None:
        raise IrError("no USB serial port detected; set IR_PORT (e.g. COM3 or /dev/ttyUSB0)")
    try:
        import serial
    except ImportError as exc:
        raise IrError("pyserial is not installed") from exc
    try:
        return serial.Serial(
            port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=timeout,
        )
    except Exception as exc:
        raise IrError(f"cannot open {port}: {exc}") from exc


def parse_codes(raw):
    """Chunk received bytes into 3-byte NEC codes; drop a trailing partial."""
    data = bytes(raw or b"")
    return [list(data[i : i + 3]) for i in range(0, len(data) - len(data) % 3, 3)]


class IrUsbSerialDriver(Driver):
    """One YS-IRTM module on a USB-serial port."""

    def __init__(self, driver_id, port=None, baudrate=9600, timeout=2.0):
        super().__init__(driver_id)
        self.port = port or default_port()
        self.baudrate = baudrate
        self.timeout = timeout

    def capabilities(self):
        return {
            "family": "ir",
            "transport": "usb-serial",
            "module": "YS-IRTM",
            "protocol": "NEC",
            "commands": ["send", "listen", "state"],
        }

    @_locked
    def get_state(self):
        try:
            handle = open_port(self.port, self.baudrate, timeout=1.0)
        except IrError as exc:
            return DriverResult(ok=False, error=str(exc))
        try:
            handle.close()
        except Exception:
            pass
        return DriverResult(ok=True, state={"port": self.port, "open": True})

    @staticmethod
    def _coerce_code(value):
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            raise IrError("code must be 3 bytes, e.g. [0, 255, 69]")
        code = list(value)
        if any(not isinstance(b, int) or not 0 <= b <= 255 for b in code):
            raise IrError("code bytes must be ints 0-255")
        return code

    @_locked
    def apply(self, command):
        if not isinstance(command, dict) or "send" not in command:
            return DriverResult(ok=False, error='command needs {"send": [d1, d2, d3]}')
        try:
            code = self._coerce_code(command["send"])
            handle = open_port(self.port, self.baudrate, self.timeout)
            try:
                handle.reset_input_buffer()
                handle.write(SEND_PREFIX + bytes(code))
                handle.flush()
                reply = handle.read(1)
            finally:
                try:
                    handle.close()
                except Exception:
                    pass
            if reply != bytes((CONFIRM_BYTE,)):
                return DriverResult(ok=False, error="module did not confirm (no F1 reply)")
            return DriverResult(ok=True, state={"sent": code, "confirmed": True})
        except IrError as exc:
            return DriverResult(ok=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - drivers report, never raise
            return DriverResult(ok=False, error=str(exc))

    def safe_stop(self):
        """IR has no resting output: report state, emit nothing."""
        return self.get_state()

    def probe(self):
        result = self.get_state()
        state = dict(result.state)
        state["probed"] = True
        return DriverResult(ok=result.ok, state=state, error=result.error)

    @_locked
    def listen(self, seconds=5.0):
        """Listen for remote presses. Returns {codes, raw_bytes, seconds}."""
        try:
            handle = open_port(self.port, self.baudrate, timeout=0.2)
        except IrError as exc:
            return {"codes": [], "raw_bytes": 0, "seconds": seconds, "error": str(exc)}
        try:
            deadline = time.monotonic() + max(seconds, 0.1)
            chunks = []
            while time.monotonic() < deadline:
                waiting = handle.in_waiting
                if waiting:
                    chunks.append(handle.read(waiting))
                else:
                    time.sleep(0.05)
        finally:
            try:
                handle.close()
            except Exception:
                pass
        raw = b"".join(chunks)
        return {"codes": parse_codes(raw), "raw_bytes": len(raw), "seconds": seconds}
