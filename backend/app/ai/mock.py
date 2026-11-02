# SPDX-License-Identifier: AGPL-3.0-or-later
"""Mock provider: deterministic cozy look. Keeps the demo alive with no API."""

from .base import Provider


class MockProvider(Provider):
    """First light drops to half its usual intensity; first screen gets the prompt.

    Models the baseline contract for the demo: cozy means below normal, and
    the normal level comes from the manifest, not a hardcoded guess.
    """

    def name(self):
        return "mock"

    def direct(self, prompt, targets, manifest):
        directives = {"lights": {}, "screens": {}}
        if targets in ("lights", "both"):
            lights = (manifest or {}).get("lights") or {}
            if lights:
                first = sorted(lights)[0]
                entry = lights[first] or {}
                caps = entry.get("capabilities") or {}
                usual = entry.get("usual") or {}
                base = usual.get("intensity")
                if isinstance(base, bool) or not isinstance(base, (int, float)):
                    base = 30  # no baseline declared: previous demo level
                look = {"intensity": max(0, round(base / 2))}
                usual_cct = usual.get("cct")
                if caps.get("cct", True):
                    if isinstance(usual_cct, bool) or not isinstance(usual_cct, (int, float)):
                        usual_cct = 4200  # no baseline cct: previous demo warmth
                    look["cct"] = usual_cct
                directives["lights"][first] = look
        if targets in ("screens", "both"):
            screens = (manifest or {}).get("screens") or []
            for screen in screens[:1]:
                directives["screens"][screen] = {"theme": "ai", "text": (prompt or "untitled")[:120]}
        return directives
