# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for IR capture/replay with a stub USB driver."""

import pytest

from app.drivers.base import DriverResult
from app.modules.ir import IrCodeStore, IrModule, IrModuleError


class StubUsb:
    def __init__(self):
        self.heard = {"codes": [], "raw_bytes": 0, "seconds": 0}
        self.sent = []

    def listen(self, seconds=5.0):
        return self.heard

    def apply(self, command):
        self.sent.append(command)
        return DriverResult(ok=True, state={"sent": command["send"], "confirmed": True})


@pytest.fixture()
def module(tmp_path):
    stub = StubUsb()
    return IrModule("ir", {"usb": stub}, tmp_path / "codes.json"), stub


@pytest.mark.unit
def test_capture_saves_single_press(module, tmp_path):
    mod, stub = module
    stub.heard = {"codes": [[0, 255, 69]], "raw_bytes": 3, "seconds": 1}
    assert mod.capture("power", seconds=1) == {"label": "power", "code": [0, 255, 69]}
    assert mod.codes() == {"power": {"code": [0, 255, 69], "works": None, "notes": ""}}
    assert (tmp_path / "codes.json").exists()


@pytest.mark.unit
def test_capture_hears_nothing(module):
    mod, _ = module
    with pytest.raises(IrModuleError, match="heard nothing"):
        mod.capture("power", seconds=1)


@pytest.mark.unit
def test_capture_rejects_multiple_codes(module):
    mod, stub = module
    stub.heard = {"codes": [[1, 2, 3], [4, 5, 6]], "raw_bytes": 6, "seconds": 1}
    with pytest.raises(IrModuleError, match="press only once"):
        mod.capture("power", seconds=1)


@pytest.mark.unit
def test_repeated_press_of_same_button_ok(module):
    mod, stub = module
    stub.heard = {"codes": [[0, 255, 69], [0, 255, 69]], "raw_bytes": 6, "seconds": 1}
    assert mod.capture("power", seconds=1)["code"] == [0, 255, 69]


@pytest.mark.unit
def test_send_label_roundtrip(module):
    mod, stub = module
    mod.store.save_label("power", [0, 255, 69])
    result = mod.send_label("power")
    assert result.ok
    assert stub.sent == [{"send": [0, 255, 69]}]


@pytest.mark.unit
def test_send_unknown_label(module):
    mod, _ = module
    with pytest.raises(IrModuleError, match="unknown IR label"):
        mod.send_label("nope")


@pytest.mark.unit
def test_mark_records_effectiveness(module):
    mod, _ = module
    mod.store.save_label("power", [0, 255, 69])
    assert mod.mark("power", True, "strip toggles") == {
        "code": [0, 255, 69],
        "works": True,
        "notes": "strip toggles",
    }
    assert mod.codes()["power"]["works"] is True
    with pytest.raises(IrModuleError, match="unknown IR label"):
        mod.mark("nope", False)


@pytest.mark.unit
def test_mark_preserves_extra_fields(module):
    """mark/save keep fields they don't own (e.g. photoRisk)."""
    mod, _ = module
    mod.store.save_label("strobe", [0, 239, 11])
    data = mod.codes()
    data["strobe"]["photoRisk"] = "intense"
    mod.store._write(data)
    assert mod.mark("strobe", True, "fast cycle")["photoRisk"] == "intense"
    assert mod.store.save_label("strobe", [0, 239, 11])["strobe"]["photoRisk"] == "intense"


@pytest.mark.unit
def test_legacy_list_values_still_read(tmp_path):
    store = IrCodeStore(tmp_path / "codes.json")
    (tmp_path / "codes.json").write_text('{"power": [0, 255, 69]}', encoding="utf-8")
    assert store.get("power") == [0, 255, 69]


@pytest.mark.unit
def test_send_repeats_with_gaps(module):
    mod, stub = module
    mod.store.save_label("bright-up", [0, 239, 0])
    sleeps = []
    result = mod.send_label("bright-up", times=3, gap_s=0.1, sleep=sleeps.append)
    assert result.ok
    assert stub.sent == [{"send": [0, 239, 0]}] * 3
    assert sleeps == [0.1, 0.1]


@pytest.mark.unit
def test_store_missing_file_reads_empty(tmp_path):
    assert IrCodeStore(tmp_path / "nope.json").load() == {}


@pytest.mark.unit
def test_module_needs_usb_driver(tmp_path):
    with pytest.raises(IrModuleError, match="'usb' driver"):
        IrModule("ir", {}, tmp_path / "codes.json")
