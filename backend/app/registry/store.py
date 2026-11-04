# SPDX-License-Identifier: AGPL-3.0-or-later
"""Addressable view over one studio's validated configuration plus profiles."""

class RegistryError(Exception):
    """Base error for unknown ids and broken references."""


class UnknownDeviceError(RegistryError):
    """No device with that id in this studio."""


class UnknownZoneError(RegistryError):
    """No zone with that id in this studio."""


class UnknownModeError(RegistryError):
    """No mode with that id in this studio."""


class UnknownPresetError(RegistryError):
    """No preset with that id in this studio."""


class Registry:
    """Addressable view over one studio's configuration plus shared profiles."""

    def __init__(self, studio, devices, zones, modes, presets, profiles, ordering=None, modalities=None):
        self.studio = studio
        self._devices = {item["id"]: item for item in devices}
        self._zones = {item["id"]: item for item in zones}
        self._modes = {item["id"]: item for item in modes}
        self._presets = dict(presets)
        self._profiles = dict(profiles)
        self.ordering = dict(ordering or {})
        self._modalities = dict(modalities or {})

    @classmethod
    def from_loaded(cls, loaded, profiles):
        """Build from loader.load_studio() output plus loader.load_profiles()."""
        return cls(
            loaded["studio"],
            loaded["devices"],
            loaded["zones"],
            loaded["modes"],
            loaded["presets"],
            profiles,
            ordering=loaded.get("ordering"),
            modalities=loaded.get("modalities"),
        )

    def _ordered(self, collection, ids, labels):
        from ..config.ordering import ordered_ids

        return ordered_ids(self.ordering.get(collection), ids, labels)

    @property
    def device_ids(self):
        labels = {k: v.get("name", k) for k, v in self._devices.items()}
        return self._ordered("devices", list(self._devices), labels)

    @property
    def zone_ids(self):
        labels = {k: v.get("label", k) for k, v in self._zones.items()}
        return self._ordered("zones", list(self._zones), labels)

    @property
    def mode_ids(self):
        labels = {k: v.get("label", k) for k, v in self._modes.items()}
        return self._ordered("modes", list(self._modes), labels)

    @property
    def preset_ids(self):
        labels = {k: (v.get("label", k) if isinstance(v, dict) else k) for k, v in self._presets.items()}
        return self._ordered("presets", list(self._presets), labels)

    @property
    def modality_ids(self):
        labels = {k: (v.get("label", k) if isinstance(v, dict) else k) for k, v in self._modalities.items()}
        return self._ordered("modalities", list(self._modalities), labels)

    def get_modality(self, modality_id):
        try:
            return self._modalities[modality_id]
        except KeyError as exc:
            raise RegistryError(f"unknown modality: {modality_id}") from exc

    def get_device(self, device_id):
        try:
            return self._devices[device_id]
        except KeyError as exc:
            raise UnknownDeviceError(f"unknown device: {device_id}") from exc

    def get_zone(self, zone_id):
        try:
            return self._zones[zone_id]
        except KeyError as exc:
            raise UnknownZoneError(f"unknown zone: {zone_id}") from exc

    def get_mode(self, mode_id):
        try:
            return self._modes[mode_id]
        except KeyError as exc:
            raise UnknownModeError(f"unknown mode: {mode_id}") from exc

    def get_preset(self, preset_id):
        try:
            return self._presets[preset_id]
        except KeyError as exc:
            raise UnknownPresetError(f"unknown preset: {preset_id}") from exc

    def get_profile(self, profile_id):
        try:
            return self._profiles[profile_id]
        except KeyError as exc:
            raise RegistryError(f"unknown profile: {profile_id}") from exc

    def devices_in_zone(self, zone_id):
        """Return device dicts in a zone, in configured display order."""
        self.get_zone(zone_id)
        return [self._devices[device_id] for device_id in self.device_ids if self._devices[device_id]["zone"] == zone_id]

    def effective_device(self, device_id):
        """Merge instance with its profile spec, capabilities, and effects."""
        instance = self.get_device(device_id)
        merged = dict(instance)
        profile_id = instance.get("profile")
        if profile_id is None:
            merged.update({"spec": {}, "capabilities": {}, "effects": []})
        else:
            profile = self.get_profile(profile_id)
            merged.update(
                {
                    "spec": profile.get("spec", {}),
                    "capabilities": profile.get("capabilities", {}),
                    "effects": profile.get("effects", []),
                }
            )
        return merged
