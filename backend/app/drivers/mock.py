# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scriptable fake driver for tests, the Test Bench, and hardware-free demos."""

from .base import Driver, DriverResult


class MockDriver(Driver):
    """In-memory driver that records every call for later assertions."""

    def __init__(self, driver_id, initial_state=None, fail_on=()):
        super().__init__(driver_id)
        self.state = dict(initial_state if initial_state is not None else {"power": "off"})
        self.calls = []
        self.fail_on = set(fail_on)

    def capabilities(self):
        return {"mock": True, "note": "accepts any JSON command dict"}

    def get_state(self):
        self.calls.append(("get_state", {}))
        return DriverResult(ok=True, state=dict(self.state))

    def apply(self, command):
        if not isinstance(command, dict):
            self.calls.append(("apply", command))
            return DriverResult(ok=False, error="command must be an object")
        self.calls.append(("apply", dict(command)))
        failed = self.fail_on.intersection(command)
        if failed:
            return DriverResult(ok=False, error=f"mock rejected keys: {sorted(failed)}")
        self.state.update(command)
        return DriverResult(ok=True, state=dict(self.state))

    def safe_stop(self):
        self.calls.append(("safe_stop", {}))
        self.state = {"power": "off"}
        return DriverResult(ok=True, state=dict(self.state))
