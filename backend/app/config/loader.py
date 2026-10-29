# SPDX-License-Identifier: AGPL-3.0-or-later
"""Load and validate per-studio JSON configuration.

Layout under a config root::

    profiles/*.json                 shared model profiles (generic, public-safe)
    studios/<id>/studio.json        studio metadata
    studios/<id>/{devices,zones,modes}.json   arrays
    studios/<id>/presets.json       id -> preset object
    studios.local/<id>/...          same layout, git-ignored real studios

Both studio roots are loaded; a duplicate studio id across roots is an error.
"""

import json
from pathlib import Path

import jsonschema

SCHEMA_DIR = Path(__file__).parent / "schemas"
STUDIO_FILES = ("studio", "devices", "zones", "modes", "presets")


class ConfigError(Exception):
    """Raised when configuration is missing, malformed, or invalid."""


def _schema(name):
    path = SCHEMA_DIR / f"{name}.schema.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot load schema {name}: {exc}") from exc


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"missing config file: {path}") from exc
    except ValueError as exc:
        raise ConfigError(f"invalid JSON in {path}: {exc}") from exc


def validate(instance, schema_name, where):
    """Validate an instance against a named schema, raising ConfigError."""
    try:
        jsonschema.validate(instance=instance, schema=_schema(schema_name))
    except jsonschema.ValidationError as exc:
        raise ConfigError(f"invalid {schema_name} in {where}: {exc.message}") from exc
    return instance


def load_profiles(config_root):
    """Load shared profiles keyed by id."""
    profiles = {}
    for path in sorted(Path(config_root, "profiles").glob("*.json")):
        profile = validate(_read_json(path), "profile", str(path))
        if profile["id"] in profiles:
            raise ConfigError(f"duplicate profile id: {profile['id']}")
        profiles[profile["id"]] = profile
    return profiles


def load_studio(config_root, studio_id):
    """Load and validate one studio's files from either studio root."""
    for root_name in ("studios", "studios.local"):
        studio_dir = Path(config_root, root_name, studio_id)
        if (studio_dir / "studio.json").exists():
            break
    else:
        raise ConfigError(f"unknown studio: {studio_id}")
    loaded = {"dir": studio_dir}
    for name in STUDIO_FILES:
        data = _read_json(studio_dir / f"{name}.json")
        if name in ("devices", "zones", "modes"):
            if not isinstance(data, list):
                raise ConfigError(f"{name}.json in {studio_dir} must be a list")
            loaded[name] = [validate(item, name[:-1], f"{studio_dir}/{name}.json") for item in data]
            ids = [item["id"] for item in loaded[name]]
            if len(set(ids)) != len(ids):
                raise ConfigError(f"duplicate ids in {studio_dir}/{name}.json")
        elif name == "presets":
            if not isinstance(data, dict):
                raise ConfigError(f"presets.json in {studio_dir} must be an object")
            for preset_id, preset in data.items():
                validate(preset, "preset", f"{studio_dir}/presets.json#{preset_id}")
            loaded[name] = data
        else:
            loaded[name] = validate(data, "studio", f"{studio_dir}/studio.json")
    if loaded["studio"]["id"] != studio_id:
        raise ConfigError(f"studio id mismatch in {studio_dir}/studio.json")
    return loaded


def list_studios(config_root):
    """List available studios as {id, label, source} dicts."""
    found = {}
    for root_name in ("studios", "studios.local"):
        base = Path(config_root, root_name)
        if not base.is_dir():
            continue
        for studio_dir in sorted(p for p in base.iterdir() if p.is_dir()):
            meta_path = studio_dir / "studio.json"
            if not meta_path.exists():
                continue
            meta = validate(_read_json(meta_path), "studio", str(meta_path))
            if meta["id"] in found:
                raise ConfigError(f"duplicate studio id across roots: {meta['id']}")
            found[meta["id"]] = {"id": meta["id"], "label": meta["label"], "source": root_name}
    return [found[key] for key in sorted(found)]


def check_references(loaded, profiles, studio_id):
    """Verify cross-file id references inside one studio. Raises ConfigError."""
    zone_ids = {zone["id"] for zone in loaded["zones"]}
    device_ids = {device["id"] for device in loaded["devices"]}
    for device in loaded["devices"]:
        if device["zone"] not in zone_ids:
            raise ConfigError(f"device {device['id']} references unknown zone {device['zone']}")
        profile_id = device.get("profile")
        if profile_id is not None and profile_id not in profiles:
            raise ConfigError(f"device {device['id']} references unknown profile {profile_id}")
    for mode in loaded["modes"]:
        for zone_id in mode.get("zones", []):
            if zone_id not in zone_ids:
                raise ConfigError(f"mode {mode['id']} references unknown zone {zone_id}")
    for preset_id, preset in loaded["presets"].items():
        for device_id in (preset.get("lights") or {}):
            if device_id not in device_ids:
                raise ConfigError(f"preset {preset_id} references unknown device {device_id}")
    default_mode = loaded["studio"].get("defaultMode")
    mode_ids = {mode["id"] for mode in loaded["modes"]}
    if default_mode is not None and default_mode not in mode_ids:
        raise ConfigError(f"studio {studio_id} defaultMode references unknown mode {default_mode}")


def load_all(config_root):
    """Load profiles plus every studio. Returns {profiles, studios}."""
    profiles = load_profiles(config_root)
    studios = {}
    for entry in list_studios(config_root):
        loaded = load_studio(config_root, entry["id"])
        check_references(loaded, profiles, entry["id"])
        studios[entry["id"]] = loaded
    return {"profiles": profiles, "studios": studios}
