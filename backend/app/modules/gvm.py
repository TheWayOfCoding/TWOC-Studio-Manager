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


# Driver key to the panel page its frame must land on. Power and brightness
# are absent: the brightness dial works in every panel mode, so those frames
# ship without any page announce and the panel stays where it is.
_PAGE_FOR_KEY = {"cct": "cct", "hue": "hsi", "saturation": "hsi", "scene": "scene"}


def plan_gvm_writes(ordered_pairs):
    """Insert page announces so color frames land on the right panel page.

    Purpose: make multi-key looks order-independent. The vendor app announces
        the tab on every entry (CCT/HSI/scene); frames sent without that
        context land on whatever page the panel last showed, which reads as
        order-dependent wrong colors.
    Args:
        ordered_pairs (list[tuple[str, Any]]): driver-key commands in
            canonical wire order (page, power, brightness, cct, hue,
            saturation, scene).
    Returns:
        list[tuple[str, Any]]: the same commands with one page announce
            inserted before each color group (cct, hue/saturation, scene).
            An explicit page ships verbatim and disables inference.
    Raises:
        Nothing; unknown keys pass through untouched for the driver to report.
    """
    if any(key == "page" for key, _ in ordered_pairs):
        return list(ordered_pairs)
    planned = []
    announced_pages = set()
    for key, value in ordered_pairs:
        page = _PAGE_FOR_KEY.get(key)
        if page is not None and page not in announced_pages:
            planned.append(("page", page))
            announced_pages.add(page)
        planned.append((key, value))
    return planned


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

    def apply_many(self, commands, allow_intense=False):
        """Validate every command, then write the whole look on one connection.

        Purpose: give the Bench the same one-connection beat the modes engine
            already uses, instead of one BLE connect/disconnect per key.
        Args:
            commands (list[dict]): single-key driver commands in wire order.
            allow_intense (bool): opt-in for photo-risk intense scenes.
        Returns:
            list[DriverResult]: one outcome per command until the first
            failure, in order (a failed write stops the look).
        Raises:
            GvmModuleError: an intense scene without opt-in; nothing ships.
        """
        for command in commands:
            if "scene" in command and scene_risk(command["scene"]) == "intense" and not allow_intense:
                raise GvmModuleError(
                    f"scene {command['scene']} is photo-risk intense until verified; "
                    "pass allow_intense=True from an explicit human trial"
                )
        batch = getattr(self.drivers["ble"], "apply_many", None)
        if batch is not None:
            return list(batch(commands))
        outcomes = []
        for command in commands:
            outcome = self.drivers["ble"].apply(command)
            outcomes.append(outcome)
            if not outcome.ok:
                break
        return outcomes
