# SPDX-License-Identifier: AGPL-3.0-or-later
"""Director pipeline: manifest -> direct -> plan-validate -> critique -> apply.

Validation reuses the modes planner, so AI output faces the identical gate
as modes (unknown ids, ranges, safety). Nothing moves unless the plan is
clean; the critique pass then gets one look at the applied result.
"""

from ..core.safety import SafetyViolation
from ..modules.modes import ModesError
from ..registry import RegistryError


def build_manifest(registry):
    """Per-target capability manifest generated from profiles + instances.

    Also carries `room`: fixed room lighting the AI cannot switch (no
    smart plugs) but must reason about -- it can suggest turning those
    off by hand, never claim to have done it.
    """
    lights = {}
    screens = []
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
            screens.append(device_id)
            continue
        if device.get("type") not in ("light", "lightstrip") or not device.get("controllable", True):
            continue
        try:
            merged = registry.effective_device(device_id)
        except RegistryError:
            continue
        lights[device_id] = {
            "capabilities": merged.get("capabilities") or {},
            "spec": merged.get("spec") or {},
            "usual": device.get("usual") or {},
            "aiCap": device.get("aiCap") or {},
            "zone": device.get("zone"),
            "position": device.get("position"),
            "role": device.get("role"),
            "distanceToSubjectM": device.get("distanceToSubjectM"),
            "modifiers": device.get("modifiers") or [],
            "estWatts": device.get("estWatts"),
            "details": device.get("details") or {},
        }
    return {"lights": lights, "screens": sorted(screens), "room": room}


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


def apply_look(provider, modes_module, prompt, targets, allowed=None):
    """Run the full pipeline. Raises ModesError/SafetyViolation on bad output.

    allowed (an id list, or None for everything) scopes both the manifest
    the provider sees and the directives that reach the planner; anything
    the provider aims outside the scope is reported, never applied.
    """
    if targets not in ("lights", "screens", "both"):
        raise ModesError(f'targets must be "lights"|"screens"|"both": {targets!r}')
    raw_manifest = build_manifest(modes_module.registry)
    manifest = {
        "lights": _scoped(raw_manifest["lights"], allowed)[0],
        "screens": [s for s in raw_manifest["screens"] if allowed is None or s in set(allowed)],
        "room": raw_manifest.get("room", []),
    }
    directives = provider.direct(prompt, targets, manifest)
    if not isinstance(directives, dict):
        raise ModesError("provider must return a directives object")
    lights, dropped_lights = _scoped(directives.get("lights"), allowed)
    screens, dropped_screens = _scoped(directives.get("screens"), allowed)
    scoped = {"lights": lights, "screens": screens}
    content = {"lights": lights, "screen": screens}
    try:
        plan = modes_module.plan_custom(f"ai-{provider.name()}", content)
    except (ModesError, SafetyViolation, RegistryError) as exc:
        raise ModesError(f"AI directives rejected: {exc}") from exc
    notes = critique(scoped, plan)
    result = modes_module.apply_custom(f"ai-{provider.name()}", content, sleep=lambda s: None)
    return {
        "provider": provider.name(),
        "targets": targets,
        "directives": scoped,
        "critique": notes,
        "apply": result,
        "scope": {"allowed": sorted(allowed) if allowed is not None else None, "skipped": dropped_lights + dropped_screens},
        "preset_candidate": {"label": (prompt or "AI look")[:60], **content},
    }
