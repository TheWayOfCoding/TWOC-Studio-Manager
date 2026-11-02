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
    """Per-target capability manifest generated from profiles + instances."""
    lights = {}
    screens = []
    for device_id in registry.device_ids:
        device = registry.get_device(device_id)
        if device.get("driver") == "screen":
            screens.append(device_id)
            continue
        if device.get("type") != "light" or not device.get("controllable", True):
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
        }
    return {"lights": lights, "screens": sorted(screens)}


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


def apply_look(provider, modes_module, prompt, targets):
    """Run the full pipeline. Raises ModesError/SafetyViolation on bad output."""
    if targets not in ("lights", "screens", "both"):
        raise ModesError(f'targets must be "lights"|"screens"|"both": {targets!r}')
    manifest = build_manifest(modes_module.registry)
    directives = provider.direct(prompt, targets, manifest)
    if not isinstance(directives, dict):
        raise ModesError("provider must return a directives object")
    content = {"lights": directives.get("lights") or {}, "screen": directives.get("screens") or {}}
    try:
        plan = modes_module.plan_custom(f"ai-{provider.name()}", content)
    except (ModesError, SafetyViolation, RegistryError) as exc:
        raise ModesError(f"AI directives rejected: {exc}") from exc
    notes = critique(directives, plan)
    result = modes_module.apply_custom(f"ai-{provider.name()}", content, sleep=lambda s: None)
    return {
        "provider": provider.name(),
        "targets": targets,
        "directives": directives,
        "critique": notes,
        "apply": result,
        "preset_candidate": {"label": (prompt or "AI look")[:60], **content},
    }
