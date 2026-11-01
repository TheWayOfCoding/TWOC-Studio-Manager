# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-fixture DMX adapter: one light's view over a shared universe driver.

The modes engine talks generic light keys (intensity percent, CCT kelvin);
this adapter translates them onto the fixture's channels and merges them
into the shared universe buffer (never blanking through zero). Fades step
power first in whole percents, then color in DMX increments — the same
sequence as the bench light endpoint, so each quantity keeps exact holds.
"""

from .base import Driver, DriverResult
from .dmx_usb import cct_for_dmx, display_percent, dmx_for_cct, dmx_for_percent


class DmxLightDriver(Driver):
    """One fixture on a shared DmxUsbDriver. Holds dimmer/cct channels + range."""

    def __init__(self, driver_id, universe, dimmer, cct=None, cct_range=None):
        super().__init__(driver_id)
        self.universe = universe
        self.dimmer = dimmer
        self.cct_channel = cct
        self.cct_range = list(cct_range) if cct_range is not None else None

    def capabilities(self):
        return {
            "family": "dmx-light",
            "dimmer": self.dimmer,
            "cct": self.cct_channel,
            "cct_range": self.cct_range,
            "commands": ["intensity", "cct"],
        }

    def get_state(self):
        buffered = self.universe.get_state().state.get("channels") or {}
        state = {"intensity": display_percent(buffered.get(str(self.dimmer), 0))}
        if self.cct_channel is not None and self.cct_range is not None:
            state["cct"] = cct_for_dmx(
                buffered.get(str(self.cct_channel), 0), self.cct_range[0], self.cct_range[1]
            )
        return DriverResult(ok=True, state=state)

    def apply(self, command):
        if not isinstance(command, dict):
            return DriverResult(ok=False, error="command must be an object")
        channels = {}
        for key, value in command.items():
            if key == "intensity":
                channels[self.dimmer] = dmx_for_percent(value)
            elif key == "cct":
                if self.cct_channel is None or self.cct_range is None:
                    return DriverResult(ok=False, error=f"{self.driver_id} has no CCT channel")
                channels[self.cct_channel] = dmx_for_cct(value, self.cct_range[0], self.cct_range[1])
            elif key == "power":
                return DriverResult(
                    ok=False, error=f"{self.driver_id}: dmx has no power key (use intensity 0)"
                )
            else:
                return DriverResult(ok=False, error=f"{self.driver_id}: dmx does not support {key}")
        return self.universe.apply({"channels": channels})

    def fade(self, command, dwell_s):
        """Dwell-fade this fixture: power percents first, then color DMX steps."""
        if not isinstance(command, dict):
            return DriverResult(ok=False, error="command must be an object")
        sequence = []
        try:
            if "intensity" in command:
                self.universe.fade({self.dimmer: command["intensity"]}, 0, units="percent", dwell_s=dwell_s)
                sequence.append("power")
            if "cct" in command:
                if self.cct_channel is None or self.cct_range is None:
                    return DriverResult(ok=False, error=f"{self.driver_id} has no CCT channel")
                target = dmx_for_cct(command["cct"], self.cct_range[0], self.cct_range[1])
                self.universe.fade({self.cct_channel: target}, 0, units="dmx", dwell_s=dwell_s)
                sequence.append("cct")
        except Exception as exc:
            return DriverResult(ok=False, error=str(exc))
        state = {"dwell_s": dwell_s, "sequence": sequence}
        state.update(self.get_state().state)
        return DriverResult(ok=True, state=state)

    def safe_stop(self):
        """Darken this fixture only. Best-effort, never raises."""
        try:
            self.universe.apply({"channels": {self.dimmer: 0}})
        except Exception:  # noqa: BLE001 - safe_stop must never raise
            pass
        return DriverResult(ok=True, state={"intensity": 0})
