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

    def push(self, screen_id, theme=None, text=None, text_ttl_s=None, animation=None):
        """Push a theme and/or topic text to one screen.

        Empty theme/text/animation clears that field; text_ttl_s
        (seconds) makes the text auto-clear when it runs out (0/None =
        stays), taking any look pushed with it along.
        """
        command = {}
        if theme is not None:
            command["theme"] = theme
        if text is not None:
            command["text"] = text
        if text_ttl_s is not None:
            command["text_ttl_s"] = text_ttl_s
        if animation is not None:
            command["animation"] = animation
        return self._driver(screen_id).apply(command)

    def fx(self, screen_id, name, volume=None):
        """Fire one synthesized sound effect on a screen's speakers."""
        command = {"fx": name}
        if volume is not None:
            command["volume"] = volume
        return self._driver(screen_id).apply(command)

    def audio(self, screen_id, action, track=None, volume=None):
        """Play/stop the audio bed on one screen's speakers (volume 0..100)."""
        command = {"audio": action}
        if track is not None:
            command["track"] = track
        if volume is not None:
            command["volume"] = volume
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
                {key: spec[key] for key in ("theme", "text", "wash", "animation") if key in spec}
                or {"theme": "default"}
            )
            # A spec may also carry a song: {track, volume} starts the
            # bed alongside the visual (paired scene+song beats in
            # modalities/presentations); audio:"stop" silences it.
            if results[screen_id].ok and spec.get("fx"):
                driver.apply({"fx": spec["fx"], "volume": spec.get("volume", 30)})
            if results[screen_id].ok and (spec.get("track") or spec.get("audio")):
                audio_cmd = {"audio": spec.get("audio") or "play"}
                if spec.get("track") is not None:
                    audio_cmd["track"] = spec["track"]
                if spec.get("volume") is not None:
                    audio_cmd["volume"] = spec["volume"]
                audio_result = driver.apply(audio_cmd)
                if not audio_result.ok:
                    results[screen_id] = audio_result
                else:
                    merged = dict(results[screen_id].state)
                    merged.update(audio_result.state)
                    results[screen_id] = DriverResult(ok=True, state=merged)
        return results

    def _driver(self, screen_id):
        try:
            return self.drivers[screen_id]
        except KeyError as exc:
            raise ScreensModuleError(f"unknown screen: {screen_id}") from exc
