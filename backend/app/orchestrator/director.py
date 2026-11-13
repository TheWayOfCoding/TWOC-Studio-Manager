# SPDX-License-Identifier: AGPL-3.0-or-later
"""Orchestrator pipeline: manifest -> direct -> plan-validate -> critique -> apply.

Validation reuses the modes planner, so orchestrator output faces the
identical gate as modes (unknown ids, ranges, safety). Nothing moves
unless the plan is clean; the critique pass then gets one look at the
applied result.
"""

import time

from ..config.ranges import effective_ranges
from ..core.safety import SafetyViolation
from ..modules.modes import ModesError
from ..registry import RegistryError

PROMPT_TEMPLATE = (
    "You are the Studio Hub orchestrator. Reply with ONE compact JSON object only, no prose: "
    '{"lights": {"<device-id>": {...}}, "screens": {"<screen-id>": {"theme": str, "text": str, '
    '"animation": str}}}. '
    "Apply to these targets only: %(targets)s. "
    "Use only these device ids and ranges: %(manifest)s. "
    "Each light lists controls, exactly the keys it accepts with legal spans: only emit "
    "those keys, and combine them freely in one directive (intensity plus cct plus "
    "hue/saturation together is encouraged when the look needs it). "
    "Screens take theme (free text, curated list in screen_options.themes), text (short "
    "caption), and animation (must be one of screen_options.animations; omit for a still "
    "look). "
    "Each light lists usual, its normal everyday look: treat it as the baseline "
    "(moody or cozy asks go below it, neutral asks stay near it). "
    "usual may carry both a white baseline (cct) and a color baseline "
    "(hue/saturation): use the keys matching the requested look. "
    "Stay inside each light's orchCap ceiling. "
    "intensity 0 means dark and is allowed when the request wants a light off "
    '(GVM panels also accept "power": "off"; DMX lights use 0). '
    "default carries the studio's active default set: when the request asks to restore, "
    "reset, or return to normal lighting, emit the default devices values verbatim for "
    "the in-scope devices. "
    "zones lists each zone's label and directable devices for context only: power and "
    "plugs are not directable, never emit plug directives, and never suggest switching "
    "smart plugs. "
    "The manifest room list is fixed room lighting with no smart switch: "
    "factor it into the look and you may suggest switching it by hand, "
    "but never emit directives for room ids. "
    "Request: %(prompt)s"
)


def _light_controls(merged):
    """Compact settable-keys map from the shared ranges resolver.

    Only supported keys appear, so the model emits them directly without
    joining capabilities and spec itself.
    """
    resolved = effective_ranges(merged)
    values = resolved.get("values") or {}
    if resolved.get("kind") == "strip":
        return {
            "power": ["on", "off"],
            "color": list(values.get("color", {}).get("choices", [])),
        }
    controls = {}
    if "power" in values:
        controls["power"] = list(values["power"].get("enum") or ["on", "off"])
    for key in ("page", "transport"):
        if key in values:
            controls[key] = list(values[key].get("enum") or [])
    for key in ("intensity", "cct", "hue", "saturation"):
        info = values.get(key) or {}
        if info.get("supported"):
            controls[key] = {"min": info["min"], "max": info["max"], "step": info["step"]}
    return controls


def _screen_options():
    """Curated screen lists from the shared ranges resolver (single source)."""
    values = effective_ranges({"type": "screen"}).get("values") or {}
    return {
        "themes": list(values.get("theme", {}).get("choices", [])),
        "animations": list(values.get("animation", {}).get("choices", [])),
    }


def _manifest_zones(registry):
    """Zone labels + directable members for context (power stays manual-only)."""
    zones = []
    for zone_id in registry.zone_ids:
        if zone_id == "room":
            continue
        try:
            zone = registry.get_zone(zone_id)
            members = registry.devices_in_zone(zone_id)
        except RegistryError:
            continue
        directables = [
            item["id"]
            for item in members
            if item.get("controllable", True) is not False
            and (item.get("type") in ("light", "lightstrip") or item.get("driver") == "screen")
        ]
        zones.append(
            {"id": zone_id, "label": zone.get("label", zone_id), "devices": directables}
        )
    return zones


def _manifest_default(registry, lights, screens):
    """Active default set values for in-studio directables (plugs excluded).

    The model restores normal lighting by emitting these verbatim; plugs
    never appear here, so a restore can never touch power.
    """
    set_id = (registry.studio or {}).get("defaultSet")
    if not set_id:
        return {"set": None, "devices": {}}
    try:
        entry = registry.get_default_set(set_id)
    except RegistryError:
        return {"set": None, "devices": {}}
    devices = (entry or {}).get("devices") or {}
    keep = set(lights) | set(screens)
    return {
        "set": set_id,
        "devices": {
            device_id: dict(values)
            for device_id, values in devices.items()
            if device_id in keep and isinstance(values, dict)
        },
    }


def build_manifest(registry):
    """Per-target capability manifest generated from profiles + instances.

    Also carries `zones` (labels + directable members, context only),
    `screen_options` (the curated theme and animation lists), `default`
    (the active default set values a restore ask emits verbatim), and
    `room`: fixed room lighting the orchestrator cannot switch (no
    smart plugs) but must reason about -- it can suggest turning those
    off by hand, never claim to have done it.
    """
    lights = {}
    screens = {}
    room = []
    for device_id in registry.device_ids:
        device = registry.get_device(device_id)
        if device.get("zone") == "room" and device.get("type") in ("light", "lightstrip"):
            room.append({
                "id": device_id,
                "name": device.get("name", device_id),
                "position": device.get("position"),
                "role": device.get("role"),
                "estWatts": device.get("estWatts"),
                "usual": device.get("usual") or {},
                "notes": device.get("notes"),
                "details": device.get("details") or {},
                "switchable": False,
            })
            continue
        if device.get("driver") == "screen":
            screens[device_id] = {
                "name": device.get("name", device_id),
                "zone": device.get("zone"),
            }
            continue
        if device.get("type") not in ("light", "lightstrip") or not device.get("controllable", True):
            continue
        try:
            merged = registry.effective_device(device_id)
        except RegistryError:
            continue
        lights[device_id] = {
            "name": device.get("name", device_id),
            "capabilities": merged.get("capabilities") or {},
            "spec": merged.get("spec") or {},
            "controls": _light_controls(merged),
            "usual": device.get("usual") or {},
            "orchCap": device.get("orchCap") or device.get("aiCap") or {},
            "zone": device.get("zone"),
            "position": device.get("position"),
            "role": device.get("role"),
            "distanceToSubjectM": device.get("distanceToSubjectM"),
            "modifiers": device.get("modifiers") or [],
            "estWatts": device.get("estWatts"),
            "details": device.get("details") or {},
        }
    return {
        "lights": lights,
        "screens": dict(sorted(screens.items())),
        "screen_options": _screen_options(),
        "zones": _manifest_zones(registry),
        "room": room,
        "default": _manifest_default(registry, lights, screens),
    }


def critique(directives, plan):
    """One review pass over validated directives. Returns a list of notes."""
    notes = []
    lights = (directives or {}).get("lights") or {}
    if not lights and not ((directives or {}).get("screens") or {}):
        notes.append("empty look: nothing to apply")
    for device_id, command in lights.items():
        if isinstance(command, dict) and command.get("intensity", 0) == 0:
            notes.append(f"{device_id} at 0%: deliberate blackout or mistake?")
    if not notes:
        notes.append("clean: all directives within declared capabilities")
    return notes


def _scoped(mapping, allowed):
    """Filter a directives/manifest mapping to the allowed ids. Returns (kept, dropped)."""
    if allowed is None:
        return dict(mapping or {}), []
    keep = set(allowed)
    kept = {key: value for key, value in (mapping or {}).items() if key in keep}
    return kept, sorted(key for key in (mapping or {}) if key not in keep)


def orchestrate(provider, modes_module, prompt, targets, allowed=None):
    """Run the full pipeline. Raises ModesError/SafetyViolation on bad output.

    allowed (an id list, or None for everything) scopes the manifest the
    provider sees, the default values it may restore, and the directives
    that reach the planner; anything the provider aims outside the scope
    is reported, never applied.
    """
    if targets not in ("lights", "screens", "both"):
        raise ModesError(f'targets must be "lights"|"screens"|"both": {targets!r}')
    raw_manifest = build_manifest(modes_module.registry)
    manifest = {
        "lights": _scoped(raw_manifest["lights"], allowed)[0],
        "screens": _scoped(raw_manifest["screens"], allowed)[0],
        "screen_options": raw_manifest["screen_options"],
        "zones": raw_manifest["zones"],
        "room": raw_manifest.get("room", []),
        "default": {
            "set": raw_manifest["default"]["set"],
            "devices": _scoped(raw_manifest["default"]["devices"], allowed)[0],
        },
    }
    started = time.monotonic()
    directives = provider.direct(prompt, targets, manifest)
    provider_ms = int((time.monotonic() - started) * 1000)
    if not isinstance(directives, dict):
        raise ModesError("provider must return a directives object")
    lights, dropped_lights = _scoped(directives.get("lights"), allowed)
    screens, dropped_screens = _scoped(directives.get("screens"), allowed)
    scoped = {"lights": lights, "screens": screens}
    content = {"lights": lights, "screen": screens}
    try:
        plan = modes_module.plan_custom(f"orchestrator-{provider.name()}", content)
    except (ModesError, SafetyViolation, RegistryError) as exc:
        raise ModesError(f"Orchestrator directives rejected: {exc}") from exc
    notes = critique(scoped, plan)
    result = modes_module.apply_custom(f"orchestrator-{provider.name()}", content, sleep=lambda s: None)
    return {
        "provider": provider.name(),
        "targets": targets,
        "timings": {"provider_ms": provider_ms},
        "directives": scoped,
        "critique": notes,
        "apply": result,
        "scope": {"allowed": sorted(allowed) if allowed is not None else None, "skipped": dropped_lights + dropped_screens},
        "preset_candidate": {"label": (prompt or "Orchestrator look")[:60], **content},
    }
