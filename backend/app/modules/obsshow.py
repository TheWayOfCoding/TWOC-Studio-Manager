# SPDX-License-Identifier: AGPL-3.0-or-later
"""OBS show module: scene list/switch over one WebSocket driver, guarded stream."""

from ..core.safety import check_stream
from .base import Module


class ObsModuleError(Exception):
    """Missing driver or rejected show action."""


class ObsShowModule(Module):
    """Show control over an injected {"ws": driver}. No raw socket I/O."""

    def __init__(self, name, drivers):
        super().__init__(name, drivers)
        if "ws" not in self.drivers:
            raise ObsModuleError('OBS module needs a "ws" driver')

    def describe(self):
        return {
            "module": self.name,
            "transports": sorted(self.drivers),
            "actions": ["state", "scenes", "switch"],
            "guarded": ["stream start/stop need explicit confirmation"],
        }

    def state_info(self):
        """Connection state without switching anything."""
        return self.drivers["ws"].apply({"action": "state"})

    def list_scenes(self):
        """Scene list plus the current program scene."""
        return self.drivers["ws"].apply({"action": "scenes"})

    def switch_scene(self, scene):
        """Cut the program feed to a named scene."""
        if not scene:
            raise ObsModuleError("switch_scene needs a scene name")
        return self.drivers["ws"].apply({"action": "switch", "scene": scene})

    def set_streaming(self, action, confirmed=False):
        """Start/stop the stream. Refuses without explicit confirmation."""
        check_stream(action, confirmed)
        return self.drivers["ws"].apply({"action": "stream", "stream": action})
