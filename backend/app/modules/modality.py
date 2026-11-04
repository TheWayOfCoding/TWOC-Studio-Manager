# SPDX-License-Identifier: AGPL-3.0-or-later
"""Modality engine: modes, presets, and presentations as one concept.

A modality is an ordered list of beats. Each beat bundles actions that
fire together (zone power with optional stagger, zone-wide or per-light
looks, kiosk screen/audio, OBS scene/collection) through the modes gate,
then holds `hold_s` seconds before the next beat. The last beat stays
active: nothing resets after it, and a kiosk action on the final beat
carries `stay: true` in intent (screen state persists by construction).
`stay` on non-final beats is rejected — timing is what separates beats.
"""

import time

HOLD_MAX_S = 3600


class ModalityError(Exception):
    """Malformed modality content (config error, fails before anything moves)."""


def beat_to_content(registry, beat, where="beat"):
    """Expand one beat's actions into modes-engine content.

    zoneLights expands to every controllable light/lightstrip in the
    zone; scenes fold into per-light `scene` keys (authored content, so
    the engine runs with allow_scenes=True). Raises ModalityError on
    bad shapes before any hardware moves.
    """
    if not isinstance(beat, dict):
        raise ModalityError(f"{where} must be an object")
    actions = beat.get("actions") or {}
    if not isinstance(actions, dict):
        raise ModalityError(f"{where}.actions must be an object")
    hold = beat.get("hold_s", 0)
    if isinstance(hold, bool) or not isinstance(hold, (int, float)) or not 0 <= hold <= HOLD_MAX_S:
        raise ModalityError(f"{where}.hold_s must be 0..{HOLD_MAX_S}: {hold!r}")
    lights = dict(actions.get("lights") or {})
    if not isinstance(lights, dict):
        raise ModalityError(f"{where}.actions.lights must be an object")
    zone_lights = actions.get("zoneLights") or {}
    if not isinstance(zone_lights, dict):
        raise ModalityError(f"{where}.actions.zoneLights must be an object")
    for zone_id, look in zone_lights.items():
        if zone_id not in registry.zone_ids:
            raise ModalityError(f"{where}.actions.zoneLights names unknown zone {zone_id!r}")
        if not isinstance(look, dict):
            raise ModalityError(f"{where}.actions.zoneLights[{zone_id}] must be an object")
        for device in registry.devices_in_zone(zone_id):
            if device.get("type") in ("light", "lightstrip") and device.get("controllable", True):
                lights.setdefault(device["id"], dict(look))
    scenes = actions.get("scenes") or {}
    if not isinstance(scenes, dict):
        raise ModalityError(f"{where}.actions.scenes must be an object")
    for device_id, scene in scenes.items():
        lights.setdefault(device_id, {})["scene"] = scene
    content = {
        "powerOn": list(actions.get("powerOn") or []),
        "powerOff": list(actions.get("powerOff") or []),
        "lights": lights,
        "screen": actions.get("screen") or {},
        "obsScene": actions.get("obsScene"),
        "obsCollection": actions.get("obsCollection"),
        "fade_s": beat.get("fade_s", 0),
    }
    if beat.get("power_delay_s") is not None:
        content["power_delay_s"] = beat["power_delay_s"]
    return content


def plan_modality(registry, modality_id, modality):
    """Validate every beat up front. Returns (beats, contents). No motion."""
    if not isinstance(modality, dict) or not isinstance(modality.get("beats"), list) or not modality["beats"]:
        raise ModalityError(f"modality {modality_id} needs a non-empty beats list")
    beats = modality["beats"]
    for index, beat in enumerate(beats):
        if beat.get("stay") and index != len(beats) - 1:
            raise ModalityError(f"modality {modality_id} beat {index}: stay is only for the last beat")
    contents = [beat_to_content(registry, beat, f"{modality_id} beat {i}") for i, beat in enumerate(beats)]
    return beats, contents


def apply_modality(modes_module, modality_id, modality, sleep=time.sleep, should_stop=None):
    """Run beats in order through the modes gate, holding between beats.

    should_stop() (optional) is polled before each beat: when it fires,
    the runner jumps to the final beat so a stopped modality still
    lands somewhere sane (stop-to-final, like presentations).
    """
    registry = modes_module.registry
    beats, contents = plan_modality(registry, modality_id, modality)
    results = []
    stopped = False
    for index, (beat, content) in enumerate(zip(beats, contents)):
        if should_stop is not None and index not in (0, len(beats) - 1) and should_stop():
            stopped = True
            continue
        delay = content.pop("power_delay_s", None)
        result = modes_module.apply_custom(
            f"{modality_id}#{index}", content, sleep=lambda s: None,
            ai_caps=False, allow_scenes=True,
            power_delay_s=delay,
        )
        results.append({"beat": index, "label": beat.get("label"), "ok": result["ok"], "result": result})
        if index < len(beats) - 1:
            hold = beat.get("hold_s", 0)
            if hold:
                sleep(hold)
    return {
        "kind": "modality",
        "modality": modality_id,
        "beats": results,
        "stopped_to_final": stopped,
        "ok": all(item["ok"] for item in results) if results else False,
    }
