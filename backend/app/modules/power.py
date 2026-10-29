# SPDX-License-Identifier: AGPL-3.0-or-later
"""Power module: zone switching with staged boot and safety enforcement."""

import time

from ..core.safety import SafetyViolation, check_power
from .base import Module

DEFAULT_ORDER = 100
BASE_DELAY_S = 2
MAX_DELAY_S = 10


class PowerError(Exception):
    """Zone has no plan: missing plug driver."""


class PowerModule(Module):
    """Switches registry zones through plug drivers. drivers: plug_id -> Driver."""

    def __init__(self, name, registry, drivers=None):
        super().__init__(name, drivers)
        self.registry = registry

    def describe(self):
        return {"module": self.name, "zones": self.registry.zone_ids, "plugs": sorted(self.drivers)}

    def _plug_driver(self, device):
        try:
            return self.drivers[device["powerPlug"]]
        except KeyError as exc:
            raise PowerError(f"no driver for plug {device['powerPlug']}") from exc

    def plan_zone_power(self, zone_id, on):
        """Compute the ordered, safety-checked action list. No hardware moves.

        Power-on runs bootOrder ascending (PC first, lights settle after);
        power-off runs it descending. Delays scale with estimated watts so the
        room never surges. Raises SafetyViolation before anything moves.
        """
        devices = self.registry.devices_in_zone(zone_id)
        powered = [item for item in devices if item.get("powerPlug")]
        for device in powered:
            check_power(device, on)
            self._plug_driver(device)
        ordered = sorted(powered, key=lambda item: item.get("bootOrder", DEFAULT_ORDER))
        if not on:
            ordered = list(reversed(ordered))
        plan = []
        for device in ordered:
            watts = device.get("estWatts", 0) or 0
            plan.append(
                {
                    "device": device["id"],
                    "plug": device["powerPlug"],
                    "on": on,
                    "order": device.get("bootOrder", DEFAULT_ORDER),
                    "delay_after_s": min(BASE_DELAY_S + watts // 100, MAX_DELAY_S),
                }
            )
        return plan

    def set_zone_power(self, zone_id, on, sleep=time.sleep):
        """Execute a zone plan step by step. Returns per-step results."""
        plan = self.plan_zone_power(zone_id, on)
        results = []
        for step in plan:
            driver = self.drivers[step["plug"]]
            try:
                outcome = driver.apply({"on": step["on"]})
                results.append(
                    {"device": step["device"], "ok": outcome.ok, "state": outcome.state, "error": outcome.error}
                )
            except Exception as exc:  # noqa: BLE001 - one plug must not stop the zone
                results.append({"device": step["device"], "ok": False, "state": {}, "error": str(exc)})
            sleep(step["delay_after_s"])
        return results
