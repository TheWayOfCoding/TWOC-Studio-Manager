# SPDX-License-Identifier: AGPL-3.0-or-later
"""Effective value ranges: the single resolver every surface reads.

Per-model truth lives in profiles/*.json ``spec`` blocks (``intensityRange``,
``cctRange``, ``hueRange``, ``saturationRange``); per-device ``capabilities``
gate which keys exist at all. This module merges the two into one effective
range per property, falling back to documented generic outer bounds only when
a profile omits a range. The defaults gate, the ranges endpoint, and every
page (Control sliders, Default-sets builder, data map) read through here, so
a profile edit propagates everywhere with no hardcoded twin to drift.

Changing a range after the fact is safe: stored sets keep their bytes (the
loader only checks id references, never value ranges) and only new saves
face the gate. UIs show out-of-range saved values as warnings and preserve
them until edited — never a silent clamp on read.
"""

# Generic outer bounds: used ONLY when a profile omits that range. Physical
# percents/degrees are universal; CCT's generic span covers every profile
# shipped (2500K warm .. 8500K cool) and loses to any profile cctRange.
GENERIC_RANGES = {
    "intensity": (0, 100),
    "cct": (2500, 8500),
    "hue": (0, 360),
    "saturation": (0, 100),
}

GENERIC_STEPS = {
    "intensity": 1,
    "cct": 100,
    "hue": 5,
    "saturation": 1,
}

SPEC_RANGE_NAMES = {
    "intensity": "intensityRange",
    "cct": "cctRange",
    "hue": "hueRange",
    "saturation": "saturationRange",
}

CAP_NEEDS = {
    "intensity": "intensity",
    "cct": "cct",
    "hue": "rgb",
    "saturation": "rgb",
}

LIGHT_NUMERIC_KEYS = ("intensity", "cct", "hue", "saturation")


class RangeError(ValueError):
    """A value falls outside its effective range or capability."""


def _valid_bounds(bounds):
    """A usable [min, max] pair (plain numbers, min <= max)."""
    if not isinstance(bounds, list) or len(bounds) != 2:
        return False
    low, high = bounds
    if isinstance(low, bool) or isinstance(high, bool):
        return False
    if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
        return False
    return low <= high


def _is_num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_dual(effective_device):
    """Both a BLE address and a DMX map (selectable interface fixture)."""
    address = effective_device.get("driverAddress") or {}
    has_ble = bool(address.get("channel") and address.get("group"))
    has_dmx = bool((address.get("dmx") or {}).get("dimmer"))
    return has_ble and has_dmx


def effective_ranges(effective_device):
    """Resolve every settable property for one effective device.

    Takes the registry's effective device (instance merged with profile spec,
    capabilities, effects). Returns {"kind", "values"} where kind is one of
    "light" | "strip" | "screen" | "generic" and values maps each property to
    its control shape: numerics carry supported/min/max/step/source, enums
    carry enum [...], text carries kind/max_len.
    """
    device_type = effective_device.get("type")
    driver = effective_device.get("driver")
    if device_type in ("light", "lightstrip") and driver == "ir":
        from ..modules.ir import STRIP_FIXED_COLORS

        return {
            "kind": "strip",
            "values": {
                "power": {"enum": ["on", "off"]},
                "color": {"kind": "text", "choices": sorted(STRIP_FIXED_COLORS)},
            },
        }
    if device_type in ("light", "lightstrip"):
        caps = effective_device.get("capabilities") or {}
        spec = effective_device.get("spec") or {}
        profile_id = effective_device.get("profile")
        values = {"power": {"enum": ["on", "off"]}}
        if driver in ("gvm", "mock"):
            # Panel pages are a GVM concept (mock panels keep them too).
            values["page"] = {"enum": ["cct", "hsi"]}
        if _is_dual(effective_device):
            values["transport"] = {"enum": ["ble", "dmx"]}
        for key in LIGHT_NUMERIC_KEYS:
            need = CAP_NEEDS[key]
            # Empty capabilities (no profile) mean unknown: allow and let the
            # driver report, mirroring the modes gate's pass-through.
            supported = not (caps and not caps.get(need))
            bounds = spec.get(SPEC_RANGE_NAMES[key])
            if supported and _valid_bounds(bounds):
                low, high = bounds
                source = f"profile:{profile_id}" if profile_id else "device"
            else:
                low, high = GENERIC_RANGES[key]
                source = "generic"
            values[key] = {
                "supported": supported,
                "min": low,
                "max": high,
                "step": GENERIC_STEPS[key],
                "source": source,
            }
        return {"kind": "light", "values": values}
    if device_type == "screen":
        from ..drivers.screen import ANIMATIONS, THEMES

        return {
            "kind": "screen",
            "values": {
                "theme": {"kind": "text", "max_len": 500, "choices": list(THEMES)},
                "text": {"kind": "text", "max_len": 500},
                "animation": {
                    "kind": "text", "max_len": 500,
                    "choices": list(ANIMATIONS), "strict": True,
                },
            },
        }
    return {"kind": "generic", "values": {}}


def check_light_values(device_id, values, effective_device):
    """Validate a light's default-set values against its effective ranges.

    Raises RangeError naming the device, key, and legal span. Capability
    misses (hue on an RGB-less fixture, CCT on a fixed-temp one) raise with
    the fixture reason instead of a span.
    """
    resolved = effective_ranges(effective_device)
    if resolved["kind"] != "light":
        raise RangeError(f"{device_id}: not a range-checked light")
    known = set(resolved["values"])
    for key, value in values.items():
        if key not in known:
            raise RangeError(f"{device_id}: unknown light value {key!r}")
        if key == "power":
            if value not in ("on", "off"):
                raise RangeError(f'{device_id}: power must be "on"|"off"')
            continue
        if key == "page":
            if value not in ("cct", "hsi"):
                raise RangeError(f'{device_id}: page must be "cct"|"hsi"')
            continue
        if key == "transport":
            if value not in ("ble", "dmx"):
                raise RangeError(f'{device_id}: transport must be "ble"|"dmx"')
            if not _is_dual(effective_device):
                raise RangeError(f"{device_id}: not a dual-protocol fixture (no interface to pick)")
            continue
        info = resolved["values"][key]
        if not info["supported"]:
            if key in ("hue", "saturation"):
                raise RangeError(f"{device_id}: fixture has no color channel (hue/saturation rejected)")
            if key == "cct":
                raise RangeError(f"{device_id}: fixture has no CCT channel")
            raise RangeError(f"{device_id}: fixture has no intensity channel")
        if not _is_num(value) or not info["min"] <= value <= info["max"]:
            hint = "" if info["source"] == "generic" else f" ({info['source']})"
            raise RangeError(f"{device_id}: {key} must be {info['min']}..{info['max']}{hint}")
