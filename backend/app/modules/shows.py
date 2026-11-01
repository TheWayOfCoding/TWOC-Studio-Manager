# SPDX-License-Identifier: AGPL-3.0-or-later
"""Presentation sequences: hard-coded JSON shows walked by one runner.

A show is an ordered list of steps; each step applies mode-like content
(lights + screen, confirmed scenes folded into the same apply) through the
modes gate, the runner narrates the applied result to the studio screens
(sync by construction: captions derive from what actually applied), then
holds. Shows bypass the AI intensity cap: they are authored, not AI asks.
Holds live in the runner only — the screen interface stays stateless.

Shows live in studios.local/<studio>/shows.json (local-only, like plug-map):
{"<id>": {"label": str, "steps": [{lights?, screen?, scenes?, hold_s?,
caption?, label?, fade_s?}]}}. Steps support lights/screen/scenes only
(power and OBS stay out of shows for now).
"""

import json
import time
from pathlib import Path

from ..core.safety import SafetyViolation
from ..drivers.base import DriverResult
from ..registry import RegistryError
from .modes import ModesError

HOLD_MAX_S = 120
HOLD_DEFAULT_S = 2
SCENE_MAX = 18

STEP_KEYS = frozenset({"lights", "screen", "scenes", "hold_s", "caption", "label", "fade_s"})

# Partial scene index -> name map (the full map is still a parked refinement).
SCENE_NAMES = {3: "Candle"}


class ShowsError(Exception):
    """Unknown show, unreadable file, or malformed step content."""


def _shows_path(config_root, studio_id):
    return Path(config_root) / "studios.local" / studio_id / "shows.json"


def _read_shows(config_root, studio_id):
    try:
        stored = json.loads(_shows_path(config_root, studio_id).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ShowsError(f"no shows file for studio {studio_id}") from exc
    except ValueError as exc:
        raise ShowsError(f"shows.json for studio {studio_id} is not valid JSON: {exc}") from exc
    if not isinstance(stored, dict):
        raise ShowsError(f"shows.json for studio {studio_id} must hold an object")
    return stored


def _check_step(show_id, index, step):
    where = f"{show_id} step {index}"
    if not isinstance(step, dict):
        raise ShowsError(f"{where} must be an object")
    unknown = set(step) - STEP_KEYS
    if unknown:
        raise ShowsError(f"{where} has unknown keys: {sorted(unknown)}")
    lights = step.get("lights") or {}
    screen = step.get("screen") or {}
    scenes = step.get("scenes") or {}
    if not isinstance(lights, dict):
        raise ShowsError(f"{where} lights must be an object")
    if not isinstance(screen, dict) or any(not isinstance(spec, dict) for spec in screen.values()):
        raise ShowsError(f"{where} screen must map screen ids to spec objects")
    if not isinstance(scenes, dict):
        raise ShowsError(f"{where} scenes must be an object of device id to scene index")
    for device_id, scene_index in scenes.items():
        if (
            not isinstance(scene_index, int)
            or isinstance(scene_index, bool)
            or not 1 <= scene_index <= SCENE_MAX
        ):
            raise ShowsError(f"{where} scenes[{device_id}] must be 1..{SCENE_MAX}")
    hold = step.get("hold_s", HOLD_DEFAULT_S)
    if isinstance(hold, bool) or not isinstance(hold, (int, float)) or not 0 <= hold <= HOLD_MAX_S:
        raise ShowsError(f"{where} hold_s must be 0..{HOLD_MAX_S}")
    caption = step.get("caption")
    if caption is not None and not isinstance(caption, str):
        raise ShowsError(f"{where} caption must be a string")
    label = step.get("label")
    if label is not None and not isinstance(label, str):
        raise ShowsError(f"{where} label must be a string")
    if not lights and not screen and not scenes:
        raise ShowsError(f"{where} does nothing (needs lights, screen, and/or scenes)")
    normalized = {"lights": lights, "screen": screen, "scenes": scenes, "hold_s": hold}
    if caption is not None:
        normalized["caption"] = caption
    if label is not None:
        normalized["label"] = label
    if step.get("fade_s") is not None:
        normalized["fade_s"] = step["fade_s"]
    return normalized


def _check_show(show_id, show):
    if not isinstance(show, dict):
        raise ShowsError(f"{show_id} must be an object")
    steps = show.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ShowsError(f"{show_id} needs a non-empty steps list")
    return {
        "label": show.get("label") or show_id,
        "steps": [_check_step(show_id, index, step) for index, step in enumerate(steps)],
    }


def list_shows(config_root, studio_id):
    """List a studio's shows as [{id, label, steps}]. Missing file reads as empty."""
    try:
        stored = _read_shows(config_root, studio_id)
    except ShowsError:
        return []
    listed = []
    for show_id in sorted(stored):
        try:
            listed.append(
                {"id": show_id, "label": stored[show_id].get("label") or show_id,
                 "steps": len(stored[show_id].get("steps") or [])}
            )
        except AttributeError:
            continue
    return listed


def load_show(config_root, studio_id, show_id):
    """Load and validate one show. Raises ShowsError on anything unusable."""
    stored = _read_shows(config_root, studio_id)
    if show_id not in stored:
        raise ShowsError(f"unknown show: {show_id}")
    return _check_show(show_id, stored[show_id])


def _studio_screens(registry):
    """Screen device ids (narration targets when a step names none)."""
    return [
        device_id
        for device_id in registry.device_ids
        if registry.get_device(device_id).get("driver") == "screen"
    ]


def _device_name(registry, device_id):
    try:
        return registry.get_device(device_id).get("name", device_id)
    except RegistryError:
        return device_id


def _failed_keys(lights_result):
    """Map device id -> first error among its applied entries."""
    failures = {}
    for item in lights_result.get("applied") or []:
        if not item.get("ok") and item.get("device") not in failures:
            failures[item["device"]] = item.get("error") or "failed"
    return failures


def _cct_tint(kelvin):
    if kelvin <= 3500:
        return "#ff9a3c"
    if kelvin <= 4500:
        return "#ffd9a0"
    return "#cfe6ff"


def narrate(registry, lights, scenes, lights_result, scene_outcomes):
    """Caption + wash derived from applied content (sync by construction).

    `lights` is the step's generic lights content, `scenes` its scene map,
    `lights_result` the modes lights subsystem result, `scene_outcomes` a
    {device: DriverResult} map. Failures narrate honestly with a red wash.
    Returns {"text", "wash" | None, "theme", "ok"}.
    """
    failures = _failed_keys(lights_result)
    for device_id, outcome in (scene_outcomes or {}).items():
        if not outcome.ok and device_id not in failures:
            failures[device_id] = outcome.error or "failed"
    parts, wash_from = [], None
    for device_id in sorted(set(list(lights) + list(scenes or {}))):
        name = _device_name(registry, device_id)
        bits = []
        command = lights.get(device_id) or {}
        for key in command:
            if key == "fade_s":
                continue
            bits.append(f"{key} {command[key]}")
        if wash_from is None:
            wash_from = (device_id, command)
        if device_id in (scenes or {}):
            index = scenes[device_id]
            bits.append(f"scene {index} ({SCENE_NAMES[index]})" if index in SCENE_NAMES else f"scene {index}")
        text = f"{name} · " + " · ".join(bits) if bits else name
        if device_id in failures:
            text += f" FAILED: {str(failures[device_id])[:80]}"
        parts.append(text)
    ok = not failures
    if not ok:
        wash = {"color": "#ff2a2a", "opacity": 0.45}
    elif scenes:
        wash = {"color": "#ff9a2a", "opacity": 0.5}
    elif wash_from is not None:
        wash = _wash_for(wash_from[1])
    else:
        wash = None
    return {
        "text": "; ".join(parts),
        "wash": wash,
        "theme": "candle" if scenes else "show",
        "ok": ok,
    }


def _wash_for(command):
    """Wash color from one generic light command (hue > cct > intensity)."""
    intensity = command.get("intensity", 100)
    try:
        level = float(intensity) / 100.0
    except (TypeError, ValueError):
        level = 1.0
    if isinstance(command.get("hue"), (int, float)) and not isinstance(command.get("hue"), bool):
        saturation = command.get("saturation", 100)
        return {
            "color": f"hsl({command['hue']},{saturation}%,55%)",
            "opacity": round(0.25 + 0.45 * level, 3),
        }
    if isinstance(command.get("cct"), (int, float)) and not isinstance(command.get("cct"), bool):
        return {"color": _cct_tint(command["cct"]), "opacity": round(0.15 + 0.4 * level, 3)}
    if "intensity" in command:
        return {"color": "#ffffff", "opacity": round(0.1 + 0.5 * level, 3)}
    return None


def _merge_scenes(lights, scenes):
    """Fold confirmed scene selects into lights content (one modes apply).

    Scene keys land last per device, so panels finish on the scene select;
    GVM plans re-sort to canonical page-first order from there.
    """
    if not scenes:
        return dict(lights)
    merged = {device_id: dict(command) for device_id, command in lights.items()}
    for device_id, index in scenes.items():
        merged.setdefault(device_id, {})["scene"] = index
    return merged


def _plan_all(modes_module, show_id, show, confirm_scenes):
    """Plan every step before anything moves. Raises ShowsError."""
    if any(step["scenes"] for step in show["steps"]) and not confirm_scenes:
        raise ShowsError(f"{show_id} includes scene steps: confirm scenes to run it")
    for index, step in enumerate(show["steps"]):
        content = {"lights": _merge_scenes(step["lights"], step["scenes"]), "screen": {}}
        if step.get("fade_s") is not None:
            content["fade_s"] = step["fade_s"]
        try:
            modes_module.plan_custom(
                f"{show_id}[{index}]", content, ai_caps=False, allow_scenes=True
            )
        except (ModesError, SafetyViolation, RegistryError) as exc:
            raise ShowsError(f"{show_id} step {index} rejected: {exc}") from exc
        for device_id in step["scenes"]:
            try:
                modes_module.registry.get_device(device_id)
            except RegistryError as exc:
                raise ShowsError(f"{show_id} step {index} scenes[{device_id}]: {exc}") from exc
        for screen_id in step["screen"]:
            try:
                device = modes_module.registry.get_device(screen_id)
            except RegistryError as exc:
                raise ShowsError(f"{show_id} step {index} screen[{screen_id}]: {exc}") from exc
            if device.get("driver") != "screen":
                raise ShowsError(f"{show_id} step {index} screen[{screen_id}] is not a screen device")


def _scene_outcomes(step, lights_result):
    """Per-device scene outcomes from the merged modes apply.

    Entries the planner skipped never appear in applied, so a missing scene
    entry is an honest failure, not a silent pass.
    """
    applied = {}
    for entry in lights_result.get("applied") or []:
        if entry.get("key") == "scene" and entry["device"] not in applied:
            applied[entry["device"]] = entry
    outcomes = {}
    for device_id in step["scenes"]:
        entry = applied.get(device_id)
        if entry is None:
            outcomes[device_id] = DriverResult(
                ok=False, error=f"scene for {device_id} was skipped"
            )
        else:
            outcomes[device_id] = DriverResult(
                ok=entry["ok"],
                state=dict(entry.get("state") or {}),
                error=entry.get("error"),
            )
    return outcomes


def run_show(modes_module, screens, show, sleep=time.sleep, stop=None, confirm_scenes=False,
             on_step=None):
    """Walk every step: apply, narrate the result to screens, hold.

    `screens` maps {screen_id: spec} like ScreensModule.apply_mapping (None
    skips narration). `stop` is a threading.Event: when set, the run jumps
    to the final step (applied, no hold) and reports stopped. Returns
    {"label", "state", "ok", "steps": [{label, caption, wash, ok}]}.
    """
    label = show.get("label") or "show"
    show = {"label": label,
            "steps": [_check_step(label, index, step) for index, step in enumerate(show["steps"])]}
    _plan_all(modes_module, label, show, confirm_scenes)
    registry = modes_module.registry
    steps, ok_all = [], True
    index, state = 0, "done"
    while index < len(show["steps"]):
        step = show["steps"][index]
        content = {"lights": _merge_scenes(step["lights"], step["scenes"]), "screen": {}}
        fade = step.get("fade_s")
        result = modes_module.apply_custom(
            f"show[{index}]", content, sleep=lambda s: None, fade_s=fade,
            ai_caps=False, allow_scenes=True,
        )
        scene_outcomes = _scene_outcomes(step, result["subsystems"]["lights"])
        narration = narrate(registry, step["lights"], step["scenes"],
                            result["subsystems"]["lights"], scene_outcomes)
        caption = step.get("caption") or narration["text"] or _caption_from_screens(step)
        caption = caption or _default_title(show, index, step)
        _push_narration(screens, registry, step, narration, caption)
        step_ok = narration["ok"] and result["ok"]
        ok_all = ok_all and step_ok
        record = {"label": step.get("label") or f"step {index}", "caption": caption,
                  "wash": narration["wash"], "ok": step_ok}
        steps.append(record)
        if on_step is not None:
            on_step(index, record)
        if stop is not None and stop.is_set():
            if index < len(show["steps"]) - 1:
                index = len(show["steps"]) - 1
                state = "stopped"
                continue
            break
        _hold_sleep(step["hold_s"], sleep, stop)
        if stop is not None and stop.is_set() and index < len(show["steps"]) - 1:
            index = len(show["steps"]) - 1
            state = "stopped"
            continue
        index += 1
    return {"label": show.get("label") or "show", "state": state, "ok": ok_all, "steps": steps}


def _hold_sleep(total, sleep, stop, grain=0.2):
    """Sleep a hold in slices so stop lands within ~grain seconds."""
    remaining = total
    while remaining > 0:
        if stop is not None and stop.is_set():
            return
        chunk = min(grain, remaining)
        sleep(chunk)
        remaining -= chunk


def _caption_from_screens(step):
    """Single-screen override text doubles as the recorded caption."""
    if len(step["screen"]) == 1:
        return next(iter(step["screen"].values())).get("text") or ""
    return ""


def _default_title(show, index, step):
    if step.get("label"):
        return f"{show.get('label') or 'show'} · {step['label']}"
    return f"{show.get('label') or 'show'} · step {index}"


def _push_narration(screens, registry, step, narration, caption):
    if screens is None:
        return
    targets = list(step["screen"]) or _studio_screens(registry)
    if not targets:
        return
    mapping = {}
    for screen_id in targets:
        spec = {"theme": narration["theme"], "text": caption, "wash": narration["wash"]}
        for key, value in (step["screen"].get(screen_id) or {}).items():
            if value is None and key == "wash":
                spec.pop("wash", None)
            elif value is not None:
                spec[key] = value
        if spec.get("wash") is None:
            spec.pop("wash", None)
        mapping[screen_id] = spec
    try:
        screens(mapping)
    except Exception:  # noqa: BLE001 - narration must never stop the show
        pass

