# SPDX-License-Identifier: AGPL-3.0-or-later
"""Provider contract: prompt + targets + manifest in, directives out.

Directives are mode-like: {"lights": {device: {...}}, "screens": {...}}.
The director validates them by planning (same gate as modes) before anything
moves, so providers can never emit DMX bytes, packets, shell, or URLs.
"""

from abc import ABC, abstractmethod


class Provider(ABC):
    """One AI backend. Stateless: every call carries its own manifest."""

    @abstractmethod
    def name(self):
        """Provider id for logs and responses."""

    @abstractmethod
    def direct(self, prompt, targets, manifest):
        """Return directives for targets ("lights"|"screens"|"both")."""
