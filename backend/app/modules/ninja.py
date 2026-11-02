# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ninja module: Weeylite/Viltrox lights over BLE advertisements."""

from .base import Module

# D-PHOTO: scenes are intense until verified gentle by eye (FX strobes hit
# hard even at low power — witnessed on a live key light). Verified scene
# indexes graduate to "low" here; everything else stays gated.
VERIFIED_GENTLE_SCENES = frozenset()


class NinjaModuleError(Exception):
    """Missing driver or gated intense scene."""


def scene_risk(index):
    """Photo-risk label for a 0-based scene index."""
    return "low" if index in VERIFIED_GENTLE_SCENES else "intense"


class NinjaModule(Module):
    """Steady looks + scenes over one BLE advertiser per address."""

    def __init__(self, name, drivers):
        super().__init__(name, drivers)
        if "ble" not in self.drivers:
            raise NinjaModuleError('Ninja module needs a "ble" driver')

    def describe(self):
        return {
            "module": self.name,
            "transports": sorted(self.drivers),
            "photo_risk": {
                "power": "low",
                "brightness": "low",
                "cct": "low",
                "gm": "low",
                "preset": "low",
                "scene": "intense-until-verified",
            },
            "verified_gentle_scenes": sorted(VERIFIED_GENTLE_SCENES),
        }

    def set_power(self, on):
        """Switch the address. `on` is truthy for on, falsy for off."""
        return self.drivers["ble"].apply({"power": "on" if on else "off"})

    def set_brightness(self, percent):
        return self.drivers["ble"].apply({"brightness": percent})

    def set_cct(self, kelvin):
        return self.drivers["ble"].apply({"cct": kelvin})

    def set_gm(self, value):
        return self.drivers["ble"].apply({"gm": value})

    def set_look(self, brightness=None, cct=None, gm=None, preset=None):
        """One merged steady look; None fields keep the last-sent values."""
        command = {}
        if brightness is not None:
            command["brightness"] = brightness
        if cct is not None:
            command["cct"] = cct
        if gm is not None:
            command["gm"] = gm
        if preset is not None:
            command["preset"] = preset
        if not command:
            raise NinjaModuleError("set_look needs at least one of: brightness, cct, gm, preset")
        return self.drivers["ble"].apply(command)

    def set_scene(self, index, brightness=None, allow_intense=False):
        """Select a scene (0-9 or name). Intense scenes need explicit opt-in."""
        from ..drivers.ninja_ble import scene_index

        resolved = scene_index(index)
        if scene_risk(resolved) == "intense" and not allow_intense:
            raise NinjaModuleError(
                f"scene {resolved} is photo-risk intense until verified; "
                "pass allow_intense=True from an explicit human trial"
            )
        command = {"scene": resolved}
        if brightness is not None:
            command["brightness"] = brightness
        return self.drivers["ble"].apply(command)
