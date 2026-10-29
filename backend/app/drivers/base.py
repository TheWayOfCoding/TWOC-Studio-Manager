# SPDX-License-Identifier: AGPL-3.0-or-later
"""Driver contract: every hardware adapter implements this interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class DriverResult:
    """Outcome of a driver call. `state` is always JSON-serializable."""

    ok: bool
    state: dict = field(default_factory=dict)
    error: str | None = None


class Driver(ABC):
    """Leaf adapter for one device over one transport. No modes, UI, or AI."""

    def __init__(self, driver_id):
        self.driver_id = driver_id

    @abstractmethod
    def capabilities(self):
        """Return the static capability description of this driver."""

    @abstractmethod
    def get_state(self):
        """Return the current device state."""

    @abstractmethod
    def apply(self, command):
        """Apply a validated command dict. Returns a DriverResult."""

    @abstractmethod
    def safe_stop(self):
        """Bring the device to a safe resting state. Must not raise on failure."""

    def probe(self):
        """Discover/exercise the device for the Test Bench. Default: report caps."""
        try:
            return DriverResult(ok=True, state={"probed": True, "capabilities": self.capabilities()})
        except Exception as exc:  # noqa: BLE001 - probe must never raise
            return DriverResult(ok=False, error=str(exc))

    def as_dict(self):
        """Serialize identity plus capabilities for API responses."""
        return {"id": self.driver_id, "capabilities": self.capabilities()}
