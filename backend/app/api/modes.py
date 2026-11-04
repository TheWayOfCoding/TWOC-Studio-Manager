# SPDX-License-Identifier: AGPL-3.0-or-later
"""Mode and preset endpoints: list catalogs, apply across subsystems.

Driver wiring is honest: plugs resolve through the LAN plug-map (entries are
claimed by zone, so the example studio never touches real hardware), GVM
panels through registry BLE addresses. Anything unwired degrades by name.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from ..core.logging import get_logger
from ..core.safety import SafetyViolation
from ..drivers.dmx_light import DmxLightDriver
from .obs import boot_try_start
from ..drivers.dmx_usb import get_shared_driver
from ..drivers.gvm_ble import GvmBleDriver
from ..drivers.ir_usb_serial import IrLabelDriver, default_port
from ..drivers.kasa import KasaDriver, resolve_host
from ..drivers.ninja_ble import NinjaError
from ..drivers.obs_ws import DEFAULT_PORT as OBS_DEFAULT_PORT
from ..drivers.obs_ws import ObsWsDriver
from ..drivers.screen import ScreenDriver
from ..modules.screens import ScreensModule
from .ninja import _fixed_cct, shared_driver
from ..modules.modes import ModesError, ModesModule
from ..modules.power import PowerModule, UnknownPowerGroupError
from ..registry import (
    Registry,
    UnknownDeviceError,
    UnknownModeError,
    UnknownPresetError,
)

log = get_logger("api")
router = APIRouter()

ACTION_LOG_CAP = 20  # per-studio entries; undos pop two and push one, so depth drains, never loops


def _registry_or_404(request, studio_id):
    try:
        return request.app.state.registries[studio_id]
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown studio: {studio_id}") from exc


def _plug_map(config_root):
    """LAN plug inventory. Missing or broken reads as empty (degraded power)."""
    path = Path(config_root) / "studios.local" / "plug-map.json"
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))["plugs"]
    except (OSError, ValueError, KeyError) as exc:
        log.warning("plug-map unreadable: %s", exc)
        return []
    return entries if isinstance(entries, list) else []


def _plug_drivers(registry, config_root):
    """Claim plug-map entries whose zone exists in this studio. Hosts stay server-side.

    Plugs are identified by alias ("Studio Plug N"), resolved to the live
    host at build time; the stored host is only the offline fallback.
    Entries may carry optional "username"/"password" for account-locked plugs
    (hub-local file, never committed, never echoed in responses).
    """
    zone_ids = set(registry.zone_ids)
    drivers = {}
    for entry in _plug_map(config_root):
        if not isinstance(entry, dict):
            continue
        plug, host, zone = entry.get("plug"), entry.get("host"), entry.get("zone")
        if not plug or zone not in zone_ids:
            continue
        alias = entry.get("alias")
        if alias:
            host = resolve_host(alias, entry.get("username"), entry.get("password"), fallback=host)
        if not host:
            continue
        drivers[plug] = KasaDriver(plug, host, entry.get("username"), entry.get("password"))
    return drivers


def _light_drivers(registry, config_root):
    """One BLE driver per addressed GVM panel, one DMX adapter per mapped
    light, one broadcast driver per addressed Ninja light, one label driver
    per IR strip.

    DMX adapters share the bench's universe driver, and Ninja drivers share
    the bench's per-address cache, so every surface walks from the same
    levels instead of restarting. Bad maps skip wiring here; the planner
    names them (degraded, never silent). Never exposes addresses.
    """
    drivers = {}
    universe = None
    for device_id in registry.device_ids:
        device = registry.get_device(device_id)
        from ..modules.modes import effective_family

        family = effective_family(device)
        if family == "gvm":
            address = (device.get("driverAddress") or {}).get("ble")
            if not address:
                continue
            drivers[device_id] = GvmBleDriver(device_id, address)
        elif family == "dmx":
            if not device.get("controllable", True):
                continue
            if universe is None:
                universe = get_shared_driver()
            address = (device.get("driverAddress") or {}).get("dmx") or {}
            spec = registry.effective_device(device_id).get("spec") or {}
            bounds = spec.get("cctRange")
            cct_range = (
                bounds
                if address.get("cct") is not None and isinstance(bounds, list) and len(bounds) == 2
                else None
            )
            drivers[device_id] = DmxLightDriver(
                device_id, universe, address.get("dimmer"), address.get("cct"), cct_range
            )
        elif family == "ninja":
            address = device.get("driverAddress") or {}
            try:
                spec = registry.effective_device(device_id).get("spec") or {}
                drivers[device_id] = shared_driver(
                    address.get("channel"), address.get("group"), _fixed_cct(spec)
                )
            except NinjaError:
                continue  # planner names the bad map
            except Exception:  # noqa: BLE001 - one bad light must not break the build
                continue
        elif family == "ir":
            if device.get("type") != "lightstrip":
                continue
            studio_id = registry.studio.get("id")
            store = (
                Path(config_root)
                / "studios.local"
                / studio_id
                / "ir-codes"
                / f"{device_id}.json"
            )
            port = os.environ.get("IR_PORT") or default_port()
            drivers[device_id] = IrLabelDriver(device_id, store, port=port)
    return drivers


def _address_labels(registry):
    """BLE address -> panel label, for scrubbing hardware echoes from responses."""
    mapping = {}
    for device_id in registry.device_ids:
        device = registry.get_device(device_id)
        address = (device.get("driverAddress") or {}).get("ble")
        if address:
            mapping[address] = device.get("name", device_id)
    return mapping


def _scrub(node, mapping):
    if not mapping:
        return node
    if isinstance(node, dict):
        return {key: _scrub(value, mapping) for key, value in node.items()}
    if isinstance(node, list):
        return [_scrub(value, mapping) for value in node]
    if isinstance(node, str):
        for address, label in mapping.items():
            node = node.replace(address, label)
    return node


def _obs_applier(registry, config_root):
    """Per-request OBS scene applier from hub-local obs.json. None when unconfigured.

    obs.json maps studio ids to {"host", "port"?, "password"?}; a studio
    without an entry keeps the honest "obs applier not wired" skip. A missing
    file is normal (OBS not configured yet); only malformed JSON warns.
    Passwords are passed to the driver, never echoed in responses.
    """
    path = Path(config_root) / "studios.local" / "obs.json"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        entries = json.loads(raw).get("studios") or {}
    except ValueError as exc:
        log.warning("obs.json malformed: %s", exc)
        return None
    cfg = entries.get(registry.studio.get("id")) or {}
    if not isinstance(cfg, dict) or not cfg.get("host"):
        return None
    host, port, password = cfg["host"], cfg.get("port", OBS_DEFAULT_PORT), cfg.get("password")

    def apply_scene(scene):
        # Long connect window: modes routinely land seconds after OBS starts.
        driver = ObsWsDriver("obs-modes", host, port, password, connect_attempts=8, connect_delay_s=2.0)
        try:
            return driver.apply({"action": "switch", "scene": scene})
        finally:
            driver.safe_stop()

    return apply_scene


def _screens_applier(registry):
    """Screens applier over screen-driver devices. None when the studio has none."""
    drivers = {}
    for device_id in registry.device_ids:
        if registry.get_device(device_id).get("driver") == "screen":
            drivers[device_id] = ScreenDriver(f"screen-{device_id}", device_id)
    if not drivers:
        return None
    return ScreensModule("screens", drivers).apply_mapping


def build_modes_module(registry, config_root):
    """Wire a modes module: plug-map power, GVM + DMX + Ninja + IR lights, OBS, screens."""
    plugs = _plug_drivers(registry, config_root)
    power = PowerModule("power", registry, plugs) if plugs else None
    return ModesModule(
        "modes",
        registry,
        power=power,
        lights=_light_drivers(registry, config_root),
        obs=_obs_applier(registry, config_root),
        screens=_screens_applier(registry),
    )


def _apply_fade(body):
    """Optional apply-time fade override. None means the content's own."""
    if body is None:
        return None
    return body.get("fade_s")


def _apply_power_delay(body):
    """Optional apply-time power stagger (0..5s). None means instant."""
    if body is None:
        return None
    return body.get("power_delay_s")


def _apply_include_manual(body):
    """Optional apply-time protected-zone override. False means policy holds."""
    if body is None:
        return False
    value = body.get("include_manual", False)
    if not isinstance(value, bool):
        raise ModesError(f"include_manual must be true|false: {value!r}")
    return value


def _studio_log(request, studio_id):
    """This studio's action history (oldest first). Modes modules are
    per-request, so the history lives on the app, not the module."""
    return request.app.state.action_log.setdefault(studio_id, [])


def record_action(request, studio_id, kind, entry_id, content, result, restores=None):
    """Append one apply to the studio's history. Modes/presets resolve live
    at undo time (content None); custom (AI) entries carry their directives."""
    entries = _studio_log(request, studio_id)
    entry = {
        "seq": (entries[-1]["seq"] + 1) if entries else 1,
        "at": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "id": entry_id,
        "content": content,
        "ok": result["ok"],
        "statuses": {name: item["status"] for name, item in result["subsystems"].items()},
    }
    if restores is not None:
        entry["restores"] = restores
    entries.append(entry)
    del entries[:-ACTION_LOG_CAP]
    return entry


def _replay(module, entry):
    """Re-apply one history entry through the normal validated path."""
    if entry["kind"] == "undo":
        target = entry.get("restores") or {}
        kind, entry_id = target.get("kind"), target.get("id")
    else:
        kind, entry_id = entry["kind"], entry["id"]
    if kind == "mode":
        return {"kind": kind, "id": entry_id}, module.apply_mode(entry_id)
    if kind == "preset":
        return {"kind": kind, "id": entry_id}, module.apply_preset(entry_id)
    if kind == "custom":
        if not isinstance(entry.get("content"), dict):
            raise ModesError("cannot undo: original AI content unavailable")
        return {"kind": kind, "id": entry_id}, module.apply_custom(entry_id, entry["content"])
    if kind == "power":
        raise ModesError("cannot undo a power action (re-apply a mode to restore it)")
    raise ModesError(f"cannot undo: unknown history kind {kind!r}")


@router.get("/api/studios/{studio_id}/modes")
def modes_list(studio_id: str, request: Request):
    """List a studio's modes plus its default mode id."""
    registry: Registry = _registry_or_404(request, studio_id)
    return {
        "default": registry.studio.get("defaultMode"),
        "modes": [
            {
                "id": mode_id,
                "label": registry.get_mode(mode_id).get("label", mode_id),
                "powersOff": bool(registry.get_mode(mode_id).get("powerOff")),
            }
            for mode_id in registry.mode_ids
        ],
    }


@router.post("/api/studios/{studio_id}/modes/{mode_id}/apply")
def modes_apply(studio_id: str, mode_id: str, request: Request, body: dict | None = None):
    """Apply a mode across power, lights, OBS, and screens. Staged, gated.

    Optional body {"fade_s": dwell, "power_delay_s": secs, "include_manual": bool}
    fades every light, staggers plug steps, and optionally cuts policy-protected
    zones for this apply only.
    """
    registry: Registry = _registry_or_404(request, studio_id)
    module = build_modes_module(registry, request.app.state.config_root)
    module.obs_boot = lambda scene, collection=None: boot_try_start(request, studio_id, scene, collection)
    log.info("mode apply", extra={"fields": {"studio": studio_id, "mode": mode_id}})
    try:
        result = module.apply_mode(
            mode_id,
            fade_s=_apply_fade(body),
            power_delay_s=_apply_power_delay(body),
            include_manual=_apply_include_manual(body),
        )
    except UnknownModeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (UnknownDeviceError, UnknownPowerGroupError, ModesError, SafetyViolation) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record_action(request, studio_id, "mode", mode_id, None, result)
    return _scrub(result, _address_labels(registry))


@router.get("/api/studios/{studio_id}/presets")
def presets_list(studio_id: str, request: Request):
    """List a studio's presets."""
    registry: Registry = _registry_or_404(request, studio_id)
    return {
        "presets": [
            {"id": preset_id, "label": registry.get_preset(preset_id).get("label", preset_id)}
            for preset_id in registry.preset_ids
        ]
    }


@router.post("/api/studios/{studio_id}/presets/{preset_id}/apply")
def presets_apply(studio_id: str, preset_id: str, request: Request, body: dict | None = None):
    """Apply a preset's lights (and screen when the screen module lands).

    Optional body {"fade_s": dwell} fades every light for this apply only
    (power_delay_s is accepted but presets never touch power).
    """
    registry: Registry = _registry_or_404(request, studio_id)
    module = build_modes_module(registry, request.app.state.config_root)
    log.info("preset apply", extra={"fields": {"studio": studio_id, "preset": preset_id}})
    try:
        if body is not None and "include_manual" in body:
            _apply_include_manual(body)  # validated, then ignored: presets don't change power
        result = module.apply_preset(preset_id, fade_s=_apply_fade(body), power_delay_s=_apply_power_delay(body))
    except UnknownPresetError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (UnknownDeviceError, UnknownPowerGroupError, ModesError, SafetyViolation) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record_action(request, studio_id, "preset", preset_id, None, result)
    return _scrub(result, _address_labels(registry))


@router.get("/api/studios/{studio_id}/actions")
def actions_list(studio_id: str, request: Request):
    """This studio's action history, oldest first, plus whether undo can run."""
    _registry_or_404(request, studio_id)
    entries = list(request.app.state.action_log.get(studio_id, []))
    return {"actions": entries, "can_undo": len(entries) >= 2}


@router.post("/api/studios/{studio_id}/actions/undo")
def actions_undo(studio_id: str, request: Request):
    """Re-apply the previous history entry (undo needs two entries to stand on).

    Pops the latest two entries and replays the older one through the normal
    validated apply path, then records the restore. A failed replay puts the
    history back untouched and reports the honest error.
    """
    registry: Registry = _registry_or_404(request, studio_id)
    entries = _studio_log(request, studio_id)
    if len(entries) < 2:
        raise HTTPException(status_code=409, detail="nothing to undo yet (need two applies)")
    current = entries.pop()
    target = entries.pop()
    module = build_modes_module(registry, request.app.state.config_root)
    log.info("undo", extra={"fields": {"studio": studio_id, "target": target.get("id")}})
    try:
        restored, result = _replay(module, target)
    except (UnknownModeError, UnknownPresetError) as exc:
        entries.extend([target, current])
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (UnknownDeviceError, UnknownPowerGroupError, ModesError, SafetyViolation) as exc:
        entries.extend([target, current])
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    record_action(request, studio_id, "undo", restored["id"], target.get("content"), result, restores=restored)
    payload = _scrub(result, _address_labels(registry))
    payload["restored"] = restored
    payload["undid"] = {"kind": current["kind"], "id": current["id"]}
    return payload
