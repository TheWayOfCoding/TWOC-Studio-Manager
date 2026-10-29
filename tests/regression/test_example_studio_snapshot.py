# SPDX-License-Identifier: AGPL-3.0-or-later
"""Golden regression: the example studio always loads to the same shape."""

from pathlib import Path

import pytest

from app.config.loader import load_all

CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config"


@pytest.mark.regression
def test_example_studio_snapshot():
    data = load_all(CONFIG_ROOT)
    assert sorted(data["studios"]) == ["example-studio"]
    assert sorted(data["profiles"]) == ["demo-flood", "demo-rgb"]
    loaded = data["studios"]["example-studio"]
    assert [d["id"] for d in loaded["devices"]] == ["demo-key", "demo-rgb", "demo-cam", "demo-screen"]
    assert [z["id"] for z in loaded["zones"]] == ["bench", "desk"]
    assert [m["id"] for m in loaded["modes"]] == ["demo_work", "demo_stream"]
    assert sorted(loaded["presets"]) == ["default"]
    assert loaded["studio"]["defaultMode"] == "demo_work"
