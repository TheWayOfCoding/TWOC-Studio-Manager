# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the scriptable mock driver."""

import pytest

from app.drivers.mock import MockDriver


@pytest.mark.unit
def test_apply_merges_state():
    driver = MockDriver("m1")
    result = driver.apply({"power": "on", "intensity": 50})
    assert result.ok
    assert result.state == {"power": "on", "intensity": 50}
    assert driver.get_state().state["intensity"] == 50


@pytest.mark.unit
def test_calls_recorded():
    driver = MockDriver("m2")
    driver.apply({"power": "on"})
    driver.get_state()
    driver.safe_stop()
    kinds = [kind for kind, _ in driver.calls]
    assert kinds == ["apply", "get_state", "safe_stop"]
    assert driver.state == {"power": "off"}


@pytest.mark.unit
def test_non_dict_command_rejected():
    driver = MockDriver("m3")
    result = driver.apply(["power"])
    assert not result.ok
    assert "object" in result.error


@pytest.mark.unit
def test_fail_on_keys():
    driver = MockDriver("m4", fail_on={"strobe"})
    result = driver.apply({"strobe": True})
    assert not result.ok
    assert "strobe" in result.error


@pytest.mark.unit
def test_probe_reports_capabilities():
    driver = MockDriver("m5")
    result = driver.probe()
    assert result.ok
    assert result.state["probed"] is True
    assert result.state["capabilities"]["mock"] is True
