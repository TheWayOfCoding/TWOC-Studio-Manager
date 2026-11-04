# SPDX-License-Identifier: AGPL-3.0-or-later
"""Power module: zone switching with safety enforcement.

Switches immediately in boot order by default (operator call: the room
comes up at relay speed). An opt-in delay_s staggers steps for inrush
caution, pausing between steps only.
"""

import time

from ..core.safety import check_power
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

    def plan_group_power(self, group_id, on, include_manual=False):
        """Same plan over a power group instead of a zone. No hardware moves.

        Returns (steps, protected): policy-protected members are skipped by
        name on power-off (never always; manual-only unless include_manual),
        mirroring plan_all_power, so one protected device never aborts the
        rest of the group. Raises UnknownPowerGroupError when no device
        carries the group.
        """
        devices = [self.registry.get_device(item) for item in self.registry.device_ids]
        members = [item for item in devices if item.get("powerGroup") == group_id]
        if not members:
            raise UnknownPowerGroupError(f"unknown power group: {group_id}")
        members = [item for item in members if item.get("powerPlug")]
        if not on:
            kept, protected = [], []
            for item in members:
                policy = item.get("powerOffPolicy", "auto")
                if policy == "never":
                    protected.append(
                        {"device": item["id"], "plug": item["powerPlug"], "reason": "never off programmatically"}
                    )
                elif policy == "manual-only" and not include_manual:
                    protected.append(
                        {
                            "device": item["id"],
                            "plug": item["powerPlug"],
                            "reason": "manual-only (tick the box to include)",
                        }
                    )
                else:
                    kept.append(item)
            return self._plan_steps(kept, on, include_manual=include_manual), protected
        return self._plan_steps(members, on), []

    def plan_all_power(self, on, include_manual=False):
        """Plan over every plugged device, skipping protected policies by name.

        `never` devices never switch programmatically. `manual-only` devices
        switch only when include_manual is set (the dashboard checkbox).
        Returns (plan, skipped) so nothing is ever silent.
        """
        powered, skipped = [], []
        for device_id in self.registry.device_ids:
            device = self.registry.get_device(device_id)
            if not device.get("powerPlug"):
                continue
            policy = device.get("powerOffPolicy", "auto")
            if not on and policy == "never":
                skipped.append(
                    {"device": device["id"], "plug": device["powerPlug"], "reason": "never off programmatically"}
                )
            elif not on and policy == "manual-only" and not include_manual:
                skipped.append(
                    {
                        "device": device["id"],
                        "plug": device["powerPlug"],
                        "reason": "manual-only (tick the box to include)",
                    }
                )
            else:
                powered.append(device)
        return self._plan_steps(powered, on, include_manual=include_manual), skipped

    def set_all_power(self, on, include_manual=False, sleep=time.sleep, delay_s=0):
        """Execute a whole-studio plan. Returns (results, skipped)."""
        plan, skipped = self.plan_all_power(on, include_manual=include_manual)
        return self._execute(plan, sleep, delay_s), skipped

    def _plan_steps(self, powered, on, include_manual=False):
        for device in powered:
            policy = device.get("powerOffPolicy", "auto")
            if not (include_manual and policy == "manual-only"):
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
        """Execute a zone plan step by step. Returns per-step results.

        A zone may declare `dependsOn` (its plug strip is itself fed by
        another zone's plug — e.g. the hands-on-screens strip hangs off
        the hands-on strip). Power-on brings the parent zone up first;
        power-off leaves the parent alone (its own switch governs it).
        """
        results = []
        if on:
            parent = self.registry.get_zone(zone_id).get("dependsOn")
            if parent:
                results.extend(self._execute(self.plan_zone_power(parent, on), sleep, delay_s))
        results.extend(self._execute(self.plan_zone_power(zone_id, on), sleep, delay_s))
        return results

    def set_group_power(self, group_id, on, sleep=time.sleep, delay_s=0, include_manual=False):
        """Execute a power-group plan step by step. Returns per-step results."""
        steps, _protected = self.plan_group_power(group_id, on, include_manual=include_manual)
        return self._execute(steps, sleep, delay_s)

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
