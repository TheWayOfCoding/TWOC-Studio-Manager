# SPDX-License-Identifier: AGPL-3.0-or-later
"""Power module: zone switching with safety enforcement.

Switches immediately in boot order by default (operator call: the room
comes up at relay speed). An opt-in delay_s staggers steps for inrush
caution, pausing between steps only.
"""

import time

from ..core.safety import SafetyViolation, check_power
from .base import Module

DEFAULT_ORDER = 100


class PowerError(Exception):
    """Zone has no plan: missing plug driver."""


class UnknownPowerGroupError(PowerError):
    """No device carries that power group (config error, not degraded hardware)."""


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
        power-off runs it descending. Raises SafetyViolation before anything
        moves.
        """
        devices = self.registry.devices_in_zone(zone_id)
        return self._plan_steps([item for item in devices if item.get("powerPlug")], on)

    def plan_group_power(self, group_id, on):
        """Same plan over a power group instead of a zone. No hardware moves.

        Raises UnknownPowerGroupError when no device carries the group.
        """
        devices = [self.registry.get_device(item) for item in self.registry.device_ids]
        members = [item for item in devices if item.get("powerGroup") == group_id]
        if not members:
            raise UnknownPowerGroupError(f"unknown power group: {group_id}")
        return self._plan_steps([item for item in members if item.get("powerPlug")], on)

    def _plan_steps(self, powered, on):
        for device in powered:
            check_power(device, on)
            self._plug_driver(device)
        seen, unique = set(), []
        for device in powered:
            if device["powerPlug"] not in seen:
                seen.add(device["powerPlug"])
                unique.append(device)
        ordered = sorted(unique, key=lambda item: item.get("bootOrder", DEFAULT_ORDER))
        if not on:
            ordered = list(reversed(ordered))
        plan = []
        for device in ordered:
            plan.append(
                {
                    "device": device["id"],
                    "plug": device["powerPlug"],
                    "on": on,
                    "order": device.get("bootOrder", DEFAULT_ORDER),
                }
            )
        return plan

    def set_zone_power(self, zone_id, on, sleep=time.sleep, delay_s=0):
        """Execute a zone plan step by step. Returns per-step results."""
        return self._execute(self.plan_zone_power(zone_id, on), sleep, delay_s)

    def set_group_power(self, group_id, on, sleep=time.sleep, delay_s=0):
        """Execute a power-group plan step by step. Returns per-step results."""
        return self._execute(self.plan_group_power(group_id, on), sleep, delay_s)

    def _execute(self, plan, sleep, delay_s=0):
        """Switch every step. delay_s > 0 pauses between steps only (never after)."""
        results = []
        for index, step in enumerate(plan):
            driver = self.drivers[step["plug"]]
            try:
                outcome = driver.apply({"on": step["on"]})
                results.append(
                    {"device": step["device"], "ok": outcome.ok, "state": outcome.state, "error": outcome.error}
                )
            except Exception as exc:  # noqa: BLE001 - one plug must not stop the zone
                results.append({"device": step["device"], "ok": False, "state": {}, "error": str(exc)})
            if delay_s and index < len(plan) - 1:
                sleep(delay_s)
        return results
