# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the power safety gate."""

import pytest

from app.core.safety import SafetyViolation, check_power


def _device(device_id="d1", policy="auto"):
    return {"id": device_id, "powerOffPolicy": policy}


@pytest.mark.unit
def test_auto_off_allowed():
    check_power(_device(policy="auto"), False)


@pytest.mark.unit
def test_missing_policy_defaults_auto():
    check_power({"id": "d1"}, False)


@pytest.mark.unit
def test_manual_only_off_refused():
    with pytest.raises(SafetyViolation, match="manual-only"):
        check_power(_device(policy="manual-only"), False)


@pytest.mark.unit
def test_never_off_refused():
    with pytest.raises(SafetyViolation, match="never"):
        check_power(_device(policy="never"), False)


@pytest.mark.unit
def test_power_on_always_allowed():
    check_power(_device(policy="never"), True)
    check_power(_device(policy="manual-only"), True)
