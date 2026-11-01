# SPDX-License-Identifier: AGPL-3.0-or-later
"""GVM module: 1500D panels over BLE, with photo-risk labels on scenes."""

from .base import Module

# D-PHOTO: scenes are intense until verified gentle by eye. Verified scene
# indexes graduate to "low" here; everything else stays gated.
VERIFIED_GENTLE_SCENES = frozenset()


class GvmModuleError(Exception):
    """Missing driver, unknown panel, or gated intense scene."""


def scene_risk(index):
    """Photo-risk label for a 1-based scene index."""
    return "low" if index in VERIFIED_GENTLE_SCENES else "intense"


class GvmModule(Module):
    """CCT + scene control over one BLE driver per panel (v1: single panel)."""

    def __init__(self, name, drivers):
        super().__init__(name, drivers)
        if "ble" not in self.drivers:
            raise GvmModuleError('GVM module needs a "ble" driver')

    def describe(self):
        return {
            "module": self.name,
            "transports": sorted(self.drivers),
            "photo_risk": {
                "power": "low",
                "brightness": "low",
                "cct": "low",
                "hue": "low",
                "saturation": "low",
                "page": "low",
                "scene": "intense-until-verified",
            },
            "verified_gentle_scenes": sorted(VERIFIED_GENTLE_SCENES),
        }

    def set_power(self, on):
        """Switch the panel. `on` is truthy for on, falsy for off."""
        return self.drivers["ble"].apply({"power": "on" if on else "off"})

    def set_brightness(self, percent):
        return self.drivers["ble"].apply({"brightness": percent})

    def set_cct(self, kelvin):
        return self.drivers["ble"].apply({"cct": kelvin})

    def set_hue(self, degrees):
        return self.drivers["ble"].apply({"hue": degrees})

    def set_saturation(self, percent):
        return self.drivers["ble"].apply({"saturation": percent})

    def announce_page(self, name_or_index):
        return self.drivers["ble"].apply({"page": name_or_index})

    def set_scene(self, index, allow_intense=False):
        """Select a scene. Intense scenes need explicit opt-in (Bench confirm)."""
        if scene_risk(index) == "intense" and not allow_intense:
            raise GvmModuleError(
                f"scene {index} is photo-risk intense until verified; "
                "pass allow_intense=True from an explicit human trial"
            )
        return self.drivers["ble"].apply({"scene": index})
