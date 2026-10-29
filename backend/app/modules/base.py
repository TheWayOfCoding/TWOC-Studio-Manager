# SPDX-License-Identifier: AGPL-3.0-or-later
"""Module contract: composers of drivers into capabilities."""

from abc import ABC, abstractmethod


class Module(ABC):
    """Owns logic, presets, and validation over drivers. No raw hardware I/O."""

    def __init__(self, name, drivers=None):
        self.name = name
        self.drivers = dict(drivers or {})

    @abstractmethod
    def describe(self):
        """Return the module description for API and Bench surfaces."""

    def status(self):
        """Aggregate per-driver states without raising on single failures."""
        states = {}
        for driver_id, driver in self.drivers.items():
            try:
                result = driver.get_state()
                states[driver_id] = {"ok": result.ok, "state": result.state, "error": result.error}
            except Exception as exc:  # noqa: BLE001 - status must never raise
                states[driver_id] = {"ok": False, "state": {}, "error": str(exc)}
        return {"module": self.name, "drivers": states}
