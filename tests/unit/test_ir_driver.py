# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the YS-IRTM USB-serial driver with a faked serial port."""

import sys
from types import SimpleNamespace

import pytest

from app.drivers.ir_usb_serial import IrUsbSerialDriver, default_port, parse_codes


class FakeHandle:
    def __init__(self, to_read=b""):
        self.written = bytearray()
        self._to_read = bytearray(to_read)
        self.closed = False

    @property
    def in_waiting(self):
        return len(self._to_read)

    def read(self, n=1):
        chunk = bytes(self._to_read[:n])
        del self._to_read[:n]
        return chunk

    def write(self, data):
        self.written += data
        if bytes(data).startswith(b"\xa1\xf1") and FakeSerial.auto_confirm:
            self._to_read += b"\xf1"
        return len(data)

    def flush(self):
        pass

    def reset_input_buffer(self):
        self._to_read.clear()

    def close(self):
        self.closed = True


class FakeSerial:
    EIGHTBITS = 8
    PARITY_NONE = "N"
    STOPBITS_ONE = 1
    handles = []
    to_read = b""
    fail_open = False
    auto_confirm = True

    @classmethod
    def Serial(cls, *args, **kwargs):  # noqa: N802 - mirrors pyserial casing
        if cls.fail_open:
            raise OSError("nope")
        handle = FakeHandle(cls.to_read)
        cls.handles.append(handle)
        return handle


@pytest.fixture(autouse=True)
def fake_serial(monkeypatch):
    FakeSerial.handles = []
    FakeSerial.to_read = b""
    FakeSerial.fail_open = False
    FakeSerial.auto_confirm = True
    monkeypatch.setitem(sys.modules, "serial", FakeSerial)
    yield


@pytest.mark.unit
def test_parse_codes_chunks_triples():
    assert parse_codes(b"\x00\xff\x45\x01\x02\x03\x09") == [[0, 255, 69], [1, 2, 3]]
    assert parse_codes(b"") == []
    assert parse_codes(None) == []


@pytest.mark.unit
def test_state_opens_port():
    result = IrUsbSerialDriver("ir", port="COM9").get_state()
    assert result.ok
    assert result.state == {"port": "COM9", "open": True}


@pytest.mark.unit
def test_state_missing_port_errors():
    FakeSerial.fail_open = True
    result = IrUsbSerialDriver("ir", port="/dev/missing").get_state()
    assert not result.ok
    assert "/dev/missing" in result.error


@pytest.mark.unit
def test_apply_sends_prefixed_code_and_confirms():
    result = IrUsbSerialDriver("ir", port="COM9").apply({"send": [0, 255, 69]})
    assert result.ok
    assert result.state == {"sent": [0, 255, 69], "confirmed": True}
    assert bytes(FakeSerial.handles[0].written) == b"\xa1\xf1\x00\xff\x45"


@pytest.mark.unit
def test_apply_no_confirm_errors():
    FakeSerial.auto_confirm = False
    result = IrUsbSerialDriver("ir", port="COM9").apply({"send": [0, 255, 69]})
    assert not result.ok
    assert "confirm" in result.error


@pytest.mark.unit
def test_apply_rejects_bad_shapes():
    driver = IrUsbSerialDriver("ir", port="COM9")
    assert not driver.apply({"send": [1, 2]}).ok
    assert not driver.apply({"send": [1, 2, 999]}).ok
    assert not driver.apply({"blink": True}).ok
    assert not driver.apply(["send"]).ok


@pytest.mark.unit
def test_listen_collects_window():
    FakeSerial.to_read = b"\x00\xff\x45\x01\x02"
    heard = IrUsbSerialDriver("ir", port="COM9").listen(seconds=0.2)
    assert heard["codes"] == [[0, 255, 69]]
    assert heard["raw_bytes"] == 5


@pytest.mark.unit
def test_safe_stop_emits_nothing():
    result = IrUsbSerialDriver("ir", port="COM9").safe_stop()
    assert result.ok
    assert FakeSerial.handles[0].written == bytearray()


def _stub_comports(monkeypatch, entries):
    tools = SimpleNamespace(list_ports=SimpleNamespace(comports=lambda: entries))
    monkeypatch.setitem(sys.modules, "serial.tools", tools)


@pytest.mark.unit
@pytest.mark.parametrize(
    "entries,expected",
    [
        (
            [
                SimpleNamespace(device="COM1", hwid=r"ACPI\PNP0501", description="Communications Port"),
                SimpleNamespace(device="COM3", hwid="USB VID:PID=1A86:7523", description="USB Serial Port"),
            ],
            "COM3",
        ),
        (
            [
                SimpleNamespace(device="/dev/ttyS0", hwid="PNP0501", description="ttyS0"),
                SimpleNamespace(device="/dev/ttyUSB0", hwid="USB VID:PID=1A86:7523 SER=553", description="USB2.0-Serial"),
            ],
            "/dev/ttyUSB0",
        ),
    ],
)
def test_default_port_prefers_usb_serial(monkeypatch, entries, expected):
    _stub_comports(monkeypatch, entries)
    assert default_port() == expected


@pytest.mark.unit
def test_default_port_none_when_no_usb_serial(monkeypatch):
    _stub_comports(
        monkeypatch,
        [SimpleNamespace(device="COM1", hwid=r"ACPI\PNP0501", description="Communications Port")],
    )
    assert default_port() is None


@pytest.mark.unit
def test_default_port_none_without_pyserial(monkeypatch):
    monkeypatch.setitem(sys.modules, "serial", None)
    monkeypatch.delitem(sys.modules, "serial.tools", raising=False)
    assert default_port() is None


@pytest.mark.unit
def test_default_constructor_defers_detection():
    assert IrUsbSerialDriver("ir").port is None


@pytest.mark.unit
def test_state_undetected_port_errors_helpfully():
    result = IrUsbSerialDriver("ir").get_state()
    assert not result.ok
    assert "IR_PORT" in result.error
