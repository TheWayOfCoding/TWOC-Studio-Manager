# SPDX-License-Identifier: AGPL-3.0-or-later
"""Validated configuration, addressable by id."""

from .store import (
    Registry,
    RegistryError,
    UnknownDeviceError,
    UnknownModeError,
    UnknownPresetError,
    UnknownZoneError,
)

__all__ = [
    "Registry",
    "RegistryError",
    "UnknownDeviceError",
    "UnknownModeError",
    "UnknownPresetError",
    "UnknownZoneError",
]
