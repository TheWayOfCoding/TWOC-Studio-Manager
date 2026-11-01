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
    """One backdrop screen. Commands: {theme?, text?, wash?} or {audio, track?}.

    Wash is {"color": css, "opacity": 0..1} (tints the kiosk behind the
    caption) or null to clear it back off.
    """

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
            if "wash" in command:
                wash = command["wash"]
                if wash is None:
                    state.pop("wash", None)
                elif (
                    not isinstance(wash, dict)
                    or not isinstance(wash.get("color"), str)
                    or not wash["color"]
                    or isinstance(wash.get("opacity"), bool)
                    or not isinstance(wash.get("opacity"), (int, float))
                    or not 0 <= wash["opacity"] <= 1
                ):
                    return DriverResult(ok=False, error='wash needs {"color": css, "opacity": 0..1}')
                else:
                    state["wash"] = {"color": wash["color"], "opacity": wash["opacity"]}
            if "theme" not in command and "text" not in command and "wash" not in command:
                return DriverResult(ok=False, error="push needs theme, text, and/or wash")
        SCREEN_STATE[self.screen] = state
        return DriverResult(ok=True, state={"screen": self.screen, **state})

    def safe_stop(self):
        """Stop the audio bed. Theme stays (a dark screen helps nobody)."""
        state = screen_state(self.screen)
        state["audio"] = "stop"
        state.pop("track", None)
        SCREEN_STATE[self.screen] = state
        return DriverResult(ok=True, state={"screen": self.screen, **state})
