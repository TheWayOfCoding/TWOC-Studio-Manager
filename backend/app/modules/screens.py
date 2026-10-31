# SPDX-License-Identifier: AGPL-3.0-or-later
"""Screens module: theme/text pushes plus audio-bed transport per screen."""

from ..drivers.base import DriverResult
from .base import Module


class ScreensModuleError(Exception):
    """Unknown screen id."""


class ScreensModule(Module):
    """Backdrop compositor over {screen_id: driver}. Screen+speakers are one AV surface."""

    def describe(self):
        return {"module": self.name, "screens": sorted(self.drivers)}

    def push(self, screen_id, theme=None, text=None):
        """Push a theme and/or topic text to one screen."""
        command = {}
        if theme is not None:
            command["theme"] = theme
        if text is not None:
            command["text"] = text
        return self._driver(screen_id).apply(command)

    def audio(self, screen_id, action, track=None):
        """Play/stop the audio bed on one screen's speakers."""
        command = {"audio": action}
        if track is not None:
            command["track"] = track
        return self._driver(screen_id).apply(command)

    def apply_mapping(self, mapping):
        """Apply a {screen_id: spec} mapping. Plugs into ModesModule(screens=...)."""
        results = {}
        for screen_id, spec in (mapping or {}).items():
            try:
                driver = self._driver(screen_id)
            except ScreensModuleError as exc:
                results[screen_id] = DriverResult(ok=False, error=str(exc))
                continue
            spec = spec if isinstance(spec, dict) else {}
            results[screen_id] = driver.apply(
                {key: spec[key] for key in ("theme", "text") if key in spec} or {"theme": "default"}
            )
        return results

    def _driver(self, screen_id):
        try:
            return self.drivers[screen_id]
        except KeyError as exc:
            raise ScreensModuleError(f"unknown screen: {screen_id}") from exc
