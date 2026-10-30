# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hardware: IR serial port presence and listen window. Emits nothing."""

import os

import pytest

from app.drivers.ir_usb_serial import IrUsbSerialDriver, default_port

pytestmark = pytest.mark.hardware


def _port():
    return os.environ.get("IR_PORT") or default_port()


def test_ir_port_opens():
    if os.environ.get("STUDIO_HW") != "1":
        pytest.skip("hardware tests run on the Hub host with STUDIO_HW=1")
    result = IrUsbSerialDriver("ir-hw", _port()).get_state()
    assert result.ok, result.error


def test_ir_listen_window_returns_shape():
    if os.environ.get("STUDIO_HW") != "1":
        pytest.skip("hardware tests run on the Hub host with STUDIO_HW=1")
    heard = IrUsbSerialDriver("ir-hw", _port()).listen(seconds=3.0)
    assert "error" not in heard, heard.get("error")
    assert isinstance(heard["codes"], list)
    print(f"heard {len(heard['codes'])} codes in 3s")
