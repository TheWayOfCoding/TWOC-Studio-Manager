# SPDX-License-Identifier: AGPL-3.0-or-later
"""Modes engine: plan-then-apply content modes across power/lights/OBS/screens.

Plan phase validates everything (unknown ids, ranges, safety) and raises
before anything moves. Execute phase runs power -> lights -> OBS -> screen;
anything degraded is skipped and named, never silent (mode-catalog rule).

Lights translate per driver family (mock, gvm, dmx, ninja, ir). Fades are opt-in
dwell seconds per step: a mode/preset default with per-light overrides and
an apply-time override that wins over both. AI (custom) plans additionally
clamp intensity to each fixture's configured aiCap, reporting every clamp,
and refuse scene selects; confirmed shows opt out of both (ai_caps=False,
allow_scenes=True). Multi-key steps ride one driver batch when the driver
offers apply_many, so a step lands as one beat instead of smeared frames.
"""

import time

from ..core.safety import SafetyViolation
from ..drivers.base import DriverResult
from ..drivers.ninja_ble import NinjaError, address_byte
from .base import Module
from .ir import STRIP_FIXED_COLORS, nearest_strip_color
from .power import PowerError, UnknownPowerGroupError

LOG_CAP = 100
DWELL_MAX_S = 60
POWER_DELAY_MAX_S = 5

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
GVM_TRANSLATE = {
    "power": "power",
    "intensity": "brightness",
    "cct": "cct",
    "hue": "hue",
    "saturation": "saturation",
    "page": "page",
    "scene": "scene",
}

# Canonical GVM frame order (matches the proven bench order): page context
# first so hue/saturation/scene frames land on the right panel page.
GVM_KEY_ORDER = ("page", "power", "brightness", "cct", "hue", "saturation", "scene")

# Ninja steady looks: generic keys to broadcast-look keys. Scenes pass
# through only for confirmed shows (modes/presets/AI refuse them above);
# gm/preset stay bench-only (no generic key names them).
NINJA_TRANSLATE = {
    "power": "power",
    "intensity": "brightness",
    "cct": "cct",
    "scene": "scene",
}

# Power/brightness first so a pristine cache (brightness unknown since hub
# start) accepts the step even on drivers without batching.
NINJA_KEY_ORDER = ("power", "brightness", "cct", "scene")


class ModesError(Exception):
    """Malformed mode/preset content (config error, fails the apply)."""


def _check_dwell(value, where):
    """Validate a fade dwell (seconds held per step). Raises ModesError."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= DWELL_MAX_S:
        raise ModesError(f"{where} must be 0..{DWELL_MAX_S} (dwell seconds per step): {value!r}")
    return value


def _check_power_delay(value):
    """Validate an apply-time power stagger (0..5s between plug steps)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= POWER_DELAY_MAX_S:
        raise ModesError(f"power_delay_s must be 0..{POWER_DELAY_MAX_S} (seconds between plug steps): {value!r}")
    return value


def _is_dmx_channel(channel):
    return isinstance(channel, int) and not isinstance(channel, bool) and 1 <= channel <= 512


class ModesModule(Module):
    """Resolve modes/presets into subsystem actions. Subsystems injected.

    power: PowerModule | None. lights: {device_id: Driver} | None.
    obs: callable(scene) -> DriverResult | None.
    screens: callable({screen_id: spec}) -> {screen_id: DriverResult} | None.
    obs_boot: callable(scene, collection) -> (job, started) | None. Modes
    flagged autoStartObs kick it instead of switching immediately (the job
    waits out the PC boot, then cuts collection and scene); without it
    they switch at once.
    """

    def __init__(self, name, registry, power=None, lights=None, obs=None, screens=None, obs_boot=None):
        super().__init__(name, {})
        self.registry = registry
        self.power = power
        self.lights = lights
        self.obs = obs
        self.screens = screens
        self.obs_boot = obs_boot
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

    def plan_mode(self, mode_id, fade_s=None, include_manual=False):
        """Validate a mode into an executable plan. Raises before anything moves."""
        return self.plan_custom(
            mode_id, self.registry.get_mode(mode_id), kind="mode", fade_s=fade_s, include_manual=include_manual
        )

    def plan_custom(
        self, plan_id, content, kind="custom", fade_s=None, ai_caps=True, allow_scenes=False,
        include_manual=False,
    ):
        """Validate ad-hoc mode-like content (AI directives). Same rules as modes."""
        if not isinstance(content, dict):
            raise ModesError("custom plan content must be an object")
        if fade_s is None:
            default_dwell = _check_dwell(content.get("fade_s", 0), "fade_s")
            fade_lock = None
        else:
            default_dwell = fade_lock = _check_dwell(fade_s, "fade_s")
        if kind != "custom":
            ai_caps = False
        return {
            "kind": kind,
            "id": plan_id,
            "intent": content.get("intent"),
            "toggles": content.get("toggles") or {},
            "auto_boot": bool(content.get("autoStartObs")),
            "auto_collection": content.get("obsCollection"),
            "power": self._plan_power(
                content.get("powerOn") or [], content.get("powerOff") or [], include_manual=include_manual
            ),
            "lights": self._plan_lights(
                content.get("lights") or {}, default_dwell, ai_caps, fade_lock,
                allow_scenes=allow_scenes,
            ),
            "obs": {"scene": content.get("obsScene")},
            "screens": {"screens": content.get("screen") or {}},
        }

    def apply_mode(self, mode_id, sleep=None, fade_s=None, power_delay_s=None, include_manual=False):
        """Plan, then execute power -> lights -> OBS -> screen. Returns the result."""
        delay = _check_power_delay(power_delay_s) if power_delay_s is not None else 0
        plan = self.plan_mode(mode_id, fade_s=fade_s, include_manual=include_manual)
        return self._execute(plan, sleep or time.sleep, delay, include_manual=include_manual)

    def apply_custom(self, plan_id, content, sleep=None, fade_s=None, power_delay_s=None,
                     ai_caps=True, allow_scenes=False, include_manual=False):
        """Plan, then apply ad-hoc mode-like content. Returns the result."""
        delay = _check_power_delay(power_delay_s) if power_delay_s is not None else 0
        plan = self.plan_custom(
            plan_id, content, fade_s=fade_s, ai_caps=ai_caps, allow_scenes=allow_scenes,
            include_manual=include_manual,
        )
        return self._execute(plan, sleep or time.sleep, delay, include_manual=include_manual)

    def plan_preset(self, preset_id, fade_s=None):
        """Validate a preset (lights + screen only) into an executable plan."""
        preset = self.registry.get_preset(preset_id)
        if fade_s is None:
            default_dwell = _check_dwell(preset.get("fade_s", 0), "fade_s")
            fade_lock = None
        else:
            default_dwell = fade_lock = _check_dwell(fade_s, "fade_s")
        return {
            "kind": "preset",
            "id": preset_id,
            "intent": None,
            "auto_boot": False,
            "auto_collection": None,
            "toggles": {},
            "power": {"plans": [], "skipped": [], "protected": []},
            "lights": self._plan_lights(preset.get("lights") or {}, default_dwell, False, fade_lock),
            "obs": {"scene": None},
            "screens": {"screens": preset.get("screen") or {}},
            "preset_power_note": "presets don't change power",
            "preset_obs_note": "presets don't change OBS",
        }

    def apply_preset(self, preset_id, sleep=None, fade_s=None, power_delay_s=None):
        """Plan, then apply a preset's lights (and screen when wired)."""
        if power_delay_s is not None:
            _check_power_delay(power_delay_s)  # validated, then ignored: presets don't change power
        plan = self.plan_preset(preset_id, fade_s=fade_s)
        return self._execute(plan, sleep or time.sleep)

    def _execute(self, plan, sleep, power_delay_s=0, include_manual=False):
        power = self._exec_power(
            plan["power"], sleep, plan.get("preset_power_note"), power_delay_s, include_manual=include_manual
        )
        lights = self._exec_lights(plan["lights"])
        obs = self._exec_obs(
            plan["obs"],
            plan.get("preset_obs_note"),
            auto_boot=plan.get("auto_boot"),
            power_status=power["status"],
            collection=plan.get("auto_collection"),
        )
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

    def _plan_power(self, on_groups, off_groups, include_manual=False):
        planned, skipped, protected = [], [], []
        if self.power is None:
            for group in list(on_groups) + list(off_groups):
                skipped.append({"target": group, "reason": f"power module not wired (group {group})"})
            return {"plans": planned, "skipped": skipped, "protected": protected}
        for group, on in [(g, True) for g in on_groups] + [(g, False) for g in off_groups]:
            try:
                steps, kept_out = self.power.plan_group_power(group, on, include_manual=include_manual)
                planned.append({"group": group, "on": on, "steps": steps})
                protected.extend(kept_out)
            except UnknownPowerGroupError:
                raise
            except PowerError as exc:
                skipped.append({"target": group, "reason": f"group {group}: {exc}"})
        return {"plans": planned, "skipped": skipped, "protected": protected}

    def _plan_lights(self, lights, default_dwell=0, ai_caps=False, fade_lock=None, allow_scenes=False):
        if not isinstance(lights, dict):
            raise ModesError("lights must be an object of device_id to command")
        devices, skipped, capped = [], [], []
        for device_id, raw in lights.items():
            device = self.registry.get_device(device_id)
            if not isinstance(raw, dict):
                raise ModesError(f"lights[{device_id}] must be an object")
            command = dict(raw)
            dwell = default_dwell
            if "fade_s" in command:
                per_light = _check_dwell(command.pop("fade_s"), f"lights[{device_id}].fade_s")
                if fade_lock is None:
                    dwell = per_light
            if fade_lock is not None:
                dwell = fade_lock
            if device.get("type") == "light" and "scene" in command and not allow_scenes:
                raise SafetyViolation(
                    f"refusing scene {command['scene']} on {device_id} via automation (bench-only)"
                )
            if ai_caps:
                self._clamp_ai_cap(device_id, device, command, capped)
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
                translated.sort(key=lambda pair: GVM_KEY_ORDER.index(pair[0]))
            elif family == "dmx":
                translated = self._translate_dmx(device_id, device, kept, skipped)
                if translated is None:
                    continue
            elif family == "ninja":
                translated = self._translate_ninja(device_id, device, kept, skipped)
                if translated is None:
                    continue
            elif family == "ir":
                translated = self._translate_ir(device_id, kept, skipped)
            else:
                skipped.append(
                    {"target": device_id, "reason": f"no translator for driver family '{family}' ({device_id})"}
                )
                continue
            if (self.lights or {}).get(device_id) is None:
                skipped.append({"target": device_id, "reason": f"no light driver wired for {device_id}"})
                continue
            devices.append({"device": device_id, "commands": translated, "dwell_s": dwell})
        return {"devices": devices, "skipped": skipped, "capped": capped}

    def _clamp_ai_cap(self, device_id, device, command, capped):
        """Clamp AI asks to aiCap, profile ranges, and panel steps. Records every clamp.

        AI output is advisory: near-misses land on the nearest legal value
        and say so, instead of failing the whole look (user-authored modes
        and presets keep the strict gate).
        """
        cap = (device.get("aiCap") or {}).get("intensity")
        if cap is not None:
            if isinstance(cap, bool) or not isinstance(cap, (int, float)) or not 0 <= cap <= 100:
                raise ModesError(f"{device_id}.aiCap.intensity must be 0..100: {cap!r}")
            value = command.get("intensity")
            if not isinstance(value, bool) and isinstance(value, (int, float)) and value > cap:
                command["intensity"] = cap
                capped.append({"target": device_id, "key": "intensity", "asked": value, "capped": cap})
        if device.get("type") != "light":
            return
        spec = self.registry.effective_device(device_id).get("spec") or {}
        for key, range_name in LIGHT_RANGES.items():
            if key not in command:
                continue
            value = command[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue  # range check reports non-numbers
            bounds = spec.get(range_name)
            if bounds is not None and not (
                isinstance(bounds, list)
                and len(bounds) == 2
                and all(isinstance(end, (int, float)) and not isinstance(end, bool) for end in bounds)
            ):
                raise ModesError(f"{device_id}.{range_name} must be [min, max]: {bounds!r}")
            new = value
            if bounds is not None:
                new = min(bounds[1], max(bounds[0], value))
            if key == "cct":
                new = round(new / 100) * 100
            elif key == "hue":
                new = round(new / 5) * 5
            else:
                new = round(new)
            if bounds is not None:
                new = min(bounds[1], max(bounds[0], new))
            if isinstance(new, float) and new.is_integer():
                new = int(new)
            if new != value or (isinstance(value, float) and isinstance(new, int)):
                command[key] = new
                capped.append({"target": device_id, "key": key, "asked": value, "capped": new})

    def _translate_dmx(self, device_id, device, kept, skipped):
        """Generic intensity/cct keys pass through; the adapter maps channels.

        Returns None (whole device skipped) when the channel map itself is
        bad; per-key problems are skipped and named individually.
        """
        address = (device.get("driverAddress") or {}).get("dmx") or {}
        dimmer, cct_ch = address.get("dimmer"), address.get("cct")
        if not _is_dmx_channel(dimmer) or (cct_ch is not None and not _is_dmx_channel(cct_ch)):
            skipped.append(
                {"target": device_id, "reason": f"{device_id} has a bad DMX channel map: {address!r}"}
            )
            return None
        spec = self.registry.effective_device(device_id).get("spec") or {}
        bounds = spec.get("cctRange")
        tunable = cct_ch is not None and isinstance(bounds, list) and len(bounds) == 2
        translated = []
        for key in kept:
            if key == "intensity":
                translated.append((key, kept[key]))
            elif key == "cct":
                if cct_ch is None:
                    skipped.append(
                        {"target": f"{device_id}.cct", "reason": f"{device_id} has no CCT channel"}
                    )
                elif not tunable:
                    skipped.append(
                        {
                            "target": f"{device_id}.cct",
                            "reason": f"{device_id} has no cctRange to map kelvin",
                        }
                    )
                else:
                    translated.append((key, kept[key]))
            elif key == "power":
                skipped.append(
                    {
                        "target": f"{device_id}.power",
                        "reason": f"{device_id}: dmx has no power key (use intensity 0)",
                    }
                )
            else:
                skipped.append(
                    {"target": f"{device_id}.{key}", "reason": f"dmx does not support {key}"}
                )
        return translated

    def _translate_ninja(self, device_id, device, kept, skipped):
        """Generic power/intensity/cct keys pass through; the driver merges
        them into one broadcast look (see apply_many).

        Returns None (whole device skipped) when the channel/group map itself
        is bad; per-key problems are skipped and named individually.
        """
        address = device.get("driverAddress") or {}
        try:
            address_byte(address.get("channel"), address.get("group"))
        except NinjaError:
            skipped.append(
                {"target": device_id, "reason": f"{device_id} has a bad Ninja address map: {address!r}"}
            )
            return None
        spec = self.registry.effective_device(device_id).get("spec") or {}
        fixed = spec.get("fixedTemp") is True
        fixed_kelvin = spec.get("colorTempK")
        translated = []
        for key in kept:
            if key not in NINJA_TRANSLATE:
                skipped.append(
                    {"target": f"{device_id}.{key}", "reason": f"ninja does not support {key}"}
                )
                continue
            if key == "cct" and fixed:
                detail = f" (fixed {fixed_kelvin}K)" if fixed_kelvin else ""
                skipped.append(
                    {"target": f"{device_id}.cct", "reason": f"{device_id} is fixed-temp{detail}"}
                )
                continue
            translated.append((NINJA_TRANSLATE[key], kept[key]))
        translated.sort(key=lambda pair: NINJA_KEY_ORDER.index(pair[0]))
        return translated

    def _translate_ir(self, device_id, kept, skipped):
        """Strip looks: power/intensity flip the on/off labels, color names a
        fixed button, hue/saturation map to the nearest measured color.

        The strip has no absolute brightness: intensity only decides on vs
        off (brightness steps stay bench-only relative controls). Power
        arrives first so a color never fires at a dark strip.
        """
        power_cmds, color_cmds = [], []
        for key in kept:
            if key == "power":
                value = kept[key]
                normalized = "on" if value is True else "off" if value is False else value
                if normalized not in ("on", "off"):
                    raise ModesError(f"lights[{device_id}].power needs on|off")
                power_cmds.append(("label", normalized))
            elif key == "intensity":
                value = kept[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ModesError(f"lights[{device_id}].intensity must be a number")
                power_cmds.append(("label", "on" if value > 0 else "off"))
            elif key == "color":
                label = kept[key]
                if label not in STRIP_FIXED_COLORS:
                    skipped.append(
                        {
                            "target": f"{device_id}.color",
                            "reason": f"{device_id}: unknown strip color {label!r}",
                        }
                    )
                    continue
                color_cmds.append(("label", label))
            elif key == "hue":
                value = kept[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ModesError(f"lights[{device_id}].hue must be a number")
                saturation = kept.get("saturation", 100)
                if isinstance(saturation, bool) or not isinstance(saturation, (int, float)):
                    raise ModesError(f"lights[{device_id}].saturation must be a number")
                if not 0 <= saturation <= 100:
                    raise ModesError(f"lights[{device_id}].saturation must be 0..100")
                color_cmds.append(("label", nearest_strip_color(value, saturation)))
            elif key == "saturation":
                if "hue" not in kept:
                    skipped.append(
                        {
                            "target": f"{device_id}.saturation",
                            "reason": f"{device_id}: saturation needs hue (strip has no white channel)",
                        }
                    )
                # else folded into the hue mapping above
            else:
                skipped.append(
                    {"target": f"{device_id}.{key}", "reason": f"ir does not support {key}"}
                )
        return power_cmds + color_cmds

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

    def _exec_power(self, power_plan, sleep, preset_note=None, power_delay_s=0, include_manual=False):
        skipped = list(power_plan["skipped"])
        protected = list(power_plan.get("protected") or [])
        if preset_note is not None:
            return {
                "status": "skipped",
                "applied": [],
                "skipped": [{"target": "power", "reason": preset_note}],
                "protected": [],
            }
        if not power_plan["plans"] and not skipped:
            return {
                "status": "skipped",
                "applied": [],
                "skipped": [{"target": "power", "reason": "mode declares no power changes"}],
                "protected": protected,
            }
        if self.power is None:
            return {"status": "skipped", "applied": [], "skipped": skipped, "protected": protected}
        applied = []
        for item in power_plan["plans"]:
            plugs = {step["device"]: step["plug"] for step in item["steps"]}
            for res in self.power.set_group_power(
                item["group"], item["on"], sleep, power_delay_s, include_manual=include_manual
            ):
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
        return {"status": status, "applied": applied, "skipped": skipped, "protected": protected}

    def _exec_lights(self, lights_plan):
        skipped = list(lights_plan["skipped"])
        capped = list(lights_plan.get("capped") or [])
        notes = []
        if not lights_plan["devices"] and not skipped:
            return {
                "status": "skipped",
                "applied": [],
                "skipped": [{"target": "lights", "reason": "mode declares no lights"}],
                "capped": capped,
                "notes": notes,
            }
        if not self.lights:
            return {"status": "skipped", "applied": [], "skipped": skipped, "capped": capped, "notes": notes}
        applied = []
        for item in lights_plan["devices"]:
            driver = self.lights[item["device"]]
            dwell = item.get("dwell_s") or 0
            fade = getattr(driver, "fade", None)
            if dwell and item["commands"] and fade is not None:
                commands = dict(item["commands"])
                try:
                    outcome = fade(commands, dwell)
                    applied.append(
                        {
                            "device": item["device"],
                            "fade": True,
                            "dwell_s": dwell,
                            "commands": commands,
                            "ok": outcome.ok,
                            "state": outcome.state,
                            "error": outcome.error,
                        }
                    )
                except Exception as exc:  # noqa: BLE001 - one light must not stop the mode
                    applied.append(
                        {
                            "device": item["device"],
                            "fade": True,
                            "dwell_s": dwell,
                            "commands": commands,
                            "ok": False,
                            "state": {},
                            "error": str(exc),
                        }
                    )
                continue
            if dwell and item["commands"] and fade is None:
                notes.append(
                    {
                        "target": item["device"],
                        "reason": f"{item['device']}: fade unsupported by driver, applied instantly",
                    }
                )
            batched = getattr(driver, "apply_many", None)
            if batched is not None and len(item["commands"]) > 1:
                try:
                    outcomes = list(
                        batched([{key: value} for key, value in item["commands"]]) or []
                    )
                except Exception as exc:  # noqa: BLE001 - one batch must not stop the mode
                    outcomes = [DriverResult(ok=False, error=str(exc)) for _ in item["commands"]]
                while len(outcomes) < len(item["commands"]):
                    outcomes.append(DriverResult(ok=False, error="batch returned too few outcomes"))
                for (key, value), outcome in zip(item["commands"], outcomes):
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
                continue
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
        return {"status": status, "applied": applied, "skipped": skipped, "capped": capped, "notes": notes}

    def _exec_obs(self, obs_plan, preset_note=None, auto_boot=False, power_status="skipped", collection=None):
        if preset_note is not None:
            return {"status": "skipped", "applied": [], "skipped": [{"target": "obs", "reason": preset_note}]}
        scene = obs_plan["scene"]
        if auto_boot and self.obs_boot is not None and power_status == "ready":
            job, started = self.obs_boot(scene, collection)
            verb = "started" if started else "already running"
            return {
                "status": "skipped",
                "applied": [],
                "skipped": [
                    {
                        "target": scene or "obs",
                        "reason": f"obs boot job {job['id']} {verb}; "
                        f"{scene or 'opening scene'} lands when OBS is ready",
                    }
                ],
                "boot_job": job["id"],
            }
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
