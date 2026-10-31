# SPDX-License-Identifier: AGPL-3.0-or-later
"""Modes engine: plan-then-apply content modes across power/lights/OBS/screens.

Plan phase validates everything (unknown ids, ranges, safety) and raises
before anything moves. Execute phase runs power -> lights -> OBS -> screen;
anything degraded is skipped and named, never silent (mode-catalog rule).
"""

import time

from ..core.safety import SafetyViolation
from .base import Module
from .power import PowerError, UnknownPowerGroupError

LOG_CAP = 100

# Generic light keys to profile spec range names. Only enforced for type=light
# devices whose profile declares the range; anything else passes through and
# lets the driver report.
LIGHT_RANGES = {
    "intensity": "intensityRange",
    "cct": "cctRange",
    "hue": "hueRange",
    "saturation": "saturationRange",
}
LIGHT_CAPS = {"intensity": "intensity", "cct": "cct", "hue": "rgb", "saturation": "rgb"}

# Per-family translation of generic light keys to driver keys. Drivers take
# one key per apply (GVM frames carry a single command), so plans expand to
# sequential single-key commands for every family.
GVM_TRANSLATE = {"power": "power", "intensity": "brightness", "cct": "cct", "page": "page"}


class ModesError(Exception):
    """Malformed mode/preset content (config error, fails the apply)."""


class ModesModule(Module):
    """Resolve modes/presets into subsystem actions. Subsystems injected.

    power: PowerModule | None. lights: {device_id: Driver} | None.
    obs: callable(scene) -> DriverResult | None.
    screens: callable({screen_id: spec}) -> {screen_id: DriverResult} | None.
    """

    def __init__(self, name, registry, power=None, lights=None, obs=None, screens=None):
        super().__init__(name, {})
        self.registry = registry
        self.power = power
        self.lights = lights
        self.obs = obs
        self.screens = screens
        self.log = []

    def describe(self):
        return {
            "module": self.name,
            "modes": self.registry.mode_ids,
            "default": self.registry.studio.get("defaultMode"),
            "subsystems": {
                "power": "ready" if self.power is not None else "missing",
                "lights": "ready" if self.lights else "missing",
                "obs": "ready" if self.obs is not None else "missing",
                "screens": "ready" if self.screens is not None else "missing",
            },
        }

    def status(self):
        """Subsystem readiness plus log depth. Never touches hardware."""
        return {
            "module": self.name,
            "subsystems": self.describe()["subsystems"],
            "log_entries": len(self.log),
            "last": self.log[-1] if self.log else None,
        }

    def plan_mode(self, mode_id):
        """Validate a mode into an executable plan. Raises before anything moves."""
        return self.plan_custom(mode_id, self.registry.get_mode(mode_id), kind="mode")

    def plan_custom(self, plan_id, content, kind="custom"):
        """Validate ad-hoc mode-like content (AI directives). Same rules as modes."""
        if not isinstance(content, dict):
            raise ModesError("custom plan content must be an object")
        return {
            "kind": kind,
            "id": plan_id,
            "intent": content.get("intent"),
            "toggles": content.get("toggles") or {},
            "power": self._plan_power(content.get("powerOn") or [], content.get("powerOff") or []),
            "lights": self._plan_lights(content.get("lights") or {}),
            "obs": {"scene": content.get("obsScene")},
            "screens": {"screens": content.get("screen") or {}},
        }

    def apply_mode(self, mode_id, sleep=None):
        """Plan, then execute power -> lights -> OBS -> screen. Returns the result."""
        plan = self.plan_mode(mode_id)
        return self._execute(plan, sleep or time.sleep)

    def apply_custom(self, plan_id, content, sleep=None):
        """Plan, then apply ad-hoc mode-like content. Returns the result."""
        plan = self.plan_custom(plan_id, content)
        return self._execute(plan, sleep or time.sleep)

    def plan_preset(self, preset_id):
        """Validate a preset (lights + screen only) into an executable plan."""
        preset = self.registry.get_preset(preset_id)
        return {
            "kind": "preset",
            "id": preset_id,
            "intent": None,
            "toggles": {},
            "power": {"plans": [], "skipped": []},
            "lights": self._plan_lights(preset.get("lights") or {}),
            "obs": {"scene": None},
            "screens": {"screens": preset.get("screen") or {}},
            "preset_power_note": "presets don't change power",
            "preset_obs_note": "presets don't change OBS",
        }

    def apply_preset(self, preset_id, sleep=None):
        """Plan, then apply a preset's lights (and screen when wired)."""
        plan = self.plan_preset(preset_id)
        return self._execute(plan, sleep or time.sleep)

    def _execute(self, plan, sleep):
        power = self._exec_power(plan["power"], sleep, plan.get("preset_power_note"))
        lights = self._exec_lights(plan["lights"])
        obs = self._exec_obs(plan["obs"], plan.get("preset_obs_note"))
        screens = self._exec_screens(plan["screens"])
        subsystems = {"power": power, "lights": lights, "obs": obs, "screens": screens}
        id_key = {"mode": "mode", "preset": "preset"}.get(plan["kind"], "custom")
        result = {
            "kind": plan["kind"],
            "studio": self.registry.studio.get("id"),
            id_key: plan["id"],
            "intent": plan["intent"],
            "toggles": plan["toggles"],
            "subsystems": subsystems,
            "ok": all(item["status"] != "degraded" for item in subsystems.values()),
        }
        self.log.append(
            {
                "kind": plan["kind"],
                "id": plan["id"],
                "ok": result["ok"],
                "statuses": {name: item["status"] for name, item in subsystems.items()},
            }
        )
        del self.log[:-LOG_CAP]
        return result

    def _plan_power(self, on_groups, off_groups):
        planned, skipped = [], []
        if self.power is None:
            for group in list(on_groups) + list(off_groups):
                skipped.append({"target": group, "reason": f"power module not wired (group {group})"})
            return {"plans": planned, "skipped": skipped}
        for group, on in [(g, True) for g in on_groups] + [(g, False) for g in off_groups]:
            try:
                planned.append({"group": group, "on": on, "steps": self.power.plan_group_power(group, on)})
            except UnknownPowerGroupError:
                raise
            except PowerError as exc:
                skipped.append({"target": group, "reason": f"group {group}: {exc}"})
        return {"plans": planned, "skipped": skipped}

    def _plan_lights(self, lights):
        if not isinstance(lights, dict):
            raise ModesError("lights must be an object of device_id to command")
        devices, skipped = [], []
        for device_id, command in lights.items():
            device = self.registry.get_device(device_id)
            if not isinstance(command, dict):
                raise ModesError(f"lights[{device_id}] must be an object")
            if device.get("type") == "light" and "scene" in command:
                raise SafetyViolation(
                    f"refusing scene {command['scene']} on {device_id} via automation (bench-only)"
                )
            if not device.get("controllable", True):
                skipped.append({"target": device_id, "reason": f"{device_id} is not controllable"})
                continue
            family = device.get("driver")
            if family is None:
                skipped.append({"target": device_id, "reason": f"{device_id} names no driver family"})
                continue
            if device.get("type") == "light":
                self._check_light_ranges(device_id, command)
            kept, key_skips = self._check_light_caps(device_id, command)
            skipped.extend(key_skips)
            if family == "mock":
                translated = [(key, kept[key]) for key in kept]
            elif family == "gvm":
                translated = []
                for key in kept:
                    if key not in GVM_TRANSLATE:
                        skipped.append(
                            {"target": f"{device_id}.{key}", "reason": f"gvm does not support {key}"}
                        )
                        continue
                    translated.append((GVM_TRANSLATE[key], kept[key]))
            else:
                skipped.append(
                    {"target": device_id, "reason": f"no translator for driver family '{family}' ({device_id})"}
                )
                continue
            if (self.lights or {}).get(device_id) is None:
                skipped.append({"target": device_id, "reason": f"no light driver wired for {device_id}"})
                continue
            devices.append({"device": device_id, "commands": translated})
        return {"devices": devices, "skipped": skipped}

    def _check_light_ranges(self, device_id, command):
        merged = self.registry.effective_device(device_id)
        spec = merged.get("spec") or {}
        for key, value in command.items():
            if key == "power":
                normalized = "on" if value is True else "off" if value is False else value
                if normalized not in ("on", "off"):
                    raise ModesError(f"lights[{device_id}].power needs on|off")
                command[key] = normalized
                continue
            range_name = LIGHT_RANGES.get(key)
            if range_name is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ModesError(f"lights[{device_id}].{key} must be a number")
            bounds = spec.get(range_name)
            if bounds is not None and not (bounds[0] <= value <= bounds[1]):
                raise SafetyViolation(f"{device_id}.{key}={value} outside {range_name} {bounds}")

    def _check_light_caps(self, device_id, command):
        if self.registry.get_device(device_id).get("type") != "light":
            return dict(command), []
        caps = self.registry.effective_device(device_id).get("capabilities") or {}
        kept, skipped = {}, []
        for key, value in command.items():
            need = LIGHT_CAPS.get(key)
            if need and caps and not caps.get(need):
                skipped.append(
                    {"target": f"{device_id}.{key}", "reason": f"{device_id} cannot do {key} ({need} unsupported)"}
                )
                continue
            kept[key] = value
        return kept, skipped

    def _exec_power(self, power_plan, sleep, preset_note=None):
        skipped = list(power_plan["skipped"])
        if preset_note is not None:
            return {"status": "skipped", "applied": [], "skipped": [{"target": "power", "reason": preset_note}]}
        if not power_plan["plans"] and not skipped:
            return {
                "status": "skipped",
                "applied": [],
                "skipped": [{"target": "power", "reason": "mode declares no power changes"}],
            }
        if self.power is None:
            return {"status": "skipped", "applied": [], "skipped": skipped}
        applied = []
        for item in power_plan["plans"]:
            plugs = {step["device"]: step["plug"] for step in item["steps"]}
            for res in self.power.set_group_power(item["group"], item["on"], sleep):
                applied.append(
                    {
                        "device": res["device"],
                        "plug": plugs.get(res["device"]),
                        "on": item["on"],
                        "ok": res["ok"],
                        "state": res["state"],
                        "error": res["error"],
                    }
                )
        status = "ready" if not skipped and all(item["ok"] for item in applied) else "degraded"
        return {"status": status, "applied": applied, "skipped": skipped}

    def _exec_lights(self, lights_plan):
        skipped = list(lights_plan["skipped"])
        if not lights_plan["devices"] and not skipped:
            return {
                "status": "skipped",
                "applied": [],
                "skipped": [{"target": "lights", "reason": "mode declares no lights"}],
            }
        if not self.lights:
            return {"status": "skipped", "applied": [], "skipped": skipped}
        applied = []
        for item in lights_plan["devices"]:
            driver = self.lights[item["device"]]
            for key, value in item["commands"]:
                try:
                    outcome = driver.apply({key: value})
                    applied.append(
                        {
                            "device": item["device"],
                            "key": key,
                            "value": value,
                            "ok": outcome.ok,
                            "state": outcome.state,
                            "error": outcome.error,
                        }
                    )
                except Exception as exc:  # noqa: BLE001 - one key must not stop the mode
                    applied.append(
                        {"device": item["device"], "key": key, "value": value, "ok": False, "state": {}, "error": str(exc)}
                    )
        status = "ready" if not skipped and all(item["ok"] for item in applied) else "degraded"
        return {"status": status, "applied": applied, "skipped": skipped}

    def _exec_obs(self, obs_plan, preset_note=None):
        if preset_note is not None:
            return {"status": "skipped", "applied": [], "skipped": [{"target": "obs", "reason": preset_note}]}
        scene = obs_plan["scene"]
        if scene is None:
            return {
                "status": "skipped",
                "applied": [],
                "skipped": [{"target": "obs", "reason": "mode declares no OBS scene"}],
            }
        if self.obs is None:
            return {
                "status": "skipped",
                "applied": [],
                "skipped": [{"target": scene, "reason": f"obs applier not wired (scene {scene})"}],
            }
        try:
            outcome = self.obs(scene)
            applied = [{"target": scene, "ok": outcome.ok, "state": outcome.state, "error": outcome.error}]
        except Exception as exc:  # noqa: BLE001 - obs failure must not stop the mode
            applied = [{"target": scene, "ok": False, "state": {}, "error": str(exc)}]
        status = "ready" if applied[0]["ok"] else "degraded"
        return {"status": status, "applied": applied, "skipped": []}

    def _exec_screens(self, screens_plan):
        mapping = screens_plan["screens"]
        if not mapping:
            return {
                "status": "skipped",
                "applied": [],
                "skipped": [{"target": "screens", "reason": "mode declares no screens"}],
            }
        if self.screens is None:
            return {
                "status": "skipped",
                "applied": [],
                "skipped": [
                    {"target": screen, "reason": f"screens applier not wired (screen {screen})"} for screen in mapping
                ],
            }
        try:
            outcomes = self.screens(dict(mapping))
        except Exception as exc:  # noqa: BLE001 - screen failure must not stop the mode
            return {
                "status": "degraded",
                "applied": [{"target": screen, "ok": False, "state": {}, "error": str(exc)} for screen in mapping],
                "skipped": [],
            }
        applied = [
            {
                "target": screen,
                "ok": outcomes.get(screen).ok if outcomes.get(screen) else False,
                "state": outcomes.get(screen).state if outcomes.get(screen) else {},
                "error": outcomes.get(screen).error if outcomes.get(screen) else f"no result for {screen}",
            }
            for screen in mapping
        ]
        status = "ready" if all(item["ok"] for item in applied) else "degraded"
        return {"status": status, "applied": applied, "skipped": []}
