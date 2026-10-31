# SPDX-License-Identifier: AGPL-3.0-or-later
"""Backdrop screen + speaker driver (scaffold): hub-held state, kiosk pages poll it.

Backdrop devices are browsers pointed at /screen/{id}; this driver owns the
theme/text/audio-bed state they render. Per-process memory is enough for one
uvicorn worker; persistence arrives with the live bring-up.
"""

from .base import Driver, DriverResult

SCREEN_STATE = {}


def screen_state(screen_id):
    """Current scaffold state for one screen (empty dict when untouched)."""
    return dict(SCREEN_STATE.get(screen_id, {}))


class ScreenDriver(Driver):
    """One backdrop screen. Commands: {theme?, text?} or {audio, track?}."""

    def __init__(self, driver_id, screen=None):
        super().__init__(driver_id)
        self.screen = screen or driver_id

    def capabilities(self):
        return {
            "family": "screen",
            "transport": "hub-state",
            "commands": ["push", "audio", "state"],
            "note": "scaffold: kiosk page polls GET /api/screens/{id}/state",
        }

    def get_state(self):
        return DriverResult(ok=True, state={"screen": self.screen, **screen_state(self.screen)})

    def apply(self, command):
        if not isinstance(command, dict):
            return DriverResult(ok=False, error="command must be an object")
        state = screen_state(self.screen)
        if "audio" in command:
            action = command["audio"]
            if action not in ("play", "stop"):
                return DriverResult(ok=False, error='audio needs "play"|"stop"')
            state["audio"] = action
            if action == "play":
                state["track"] = command.get("track", state.get("track", "bed"))
            else:
                state.pop("track", None)
        else:
            if "theme" in command:
                state["theme"] = command["theme"]
            if "text" in command:
                state["text"] = command["text"]
            if "theme" not in command and "text" not in command:
                return DriverResult(ok=False, error="push needs theme and/or text")
        SCREEN_STATE[self.screen] = state
        return DriverResult(ok=True, state={"screen": self.screen, **state})

    def safe_stop(self):
        """Stop the audio bed. Theme stays (a dark screen helps nobody)."""
        state = screen_state(self.screen)
        state["audio"] = "stop"
        state.pop("track", None)
        SCREEN_STATE[self.screen] = state
        return DriverResult(ok=True, state={"screen": self.screen, **state})
