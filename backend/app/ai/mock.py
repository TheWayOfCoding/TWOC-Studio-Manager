# SPDX-License-Identifier: AGPL-3.0-or-later
"""Mock provider: deterministic cozy look. Keeps the demo alive with no API."""

from .base import Provider


class MockProvider(Provider):
    """First light in the manifest goes warm-dim; first screen gets the prompt."""

    def name(self):
        return "mock"

    def direct(self, prompt, targets, manifest):
        directives = {"lights": {}, "screens": {}}
        if targets in ("lights", "both"):
            lights = (manifest or {}).get("lights") or {}
            if lights:
                first = sorted(lights)[0]
                caps = lights[first].get("capabilities") or {}
                look = {"intensity": 30}
                if caps.get("cct", True):
                    look["cct"] = 4200
                directives["lights"][first] = look
        if targets in ("screens", "both"):
            screens = (manifest or {}).get("screens") or []
            for screen in screens[:1]:
                directives["screens"][screen] = {"theme": "ai", "text": (prompt or "untitled")[:120]}
        return directives
