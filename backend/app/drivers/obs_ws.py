# SPDX-License-Identifier: AGPL-3.0-or-later
"""OBS Studio driver over OBS WebSocket v5 (scene switch, state, guarded stream).

Scaffold: the wire client is injected (tests, bench trials) or built lazily
from `websocket-client` when installed. Live bring-up fills the handshake.
"""

from .base import Driver, DriverResult

DEFAULT_PORT = 4455


class ObsError(Exception):
    """Connection, auth, or protocol failure talking to OBS."""


class ObsWsDriver(Driver):
    """One OBS instance at host:port. Commands carry one action each.

    Actions: state, scenes, switch {scene}, stream {action, confirmed}.
    Stream actions additionally pass the safety gate (module enforces it).
    """

    def __init__(self, driver_id, host="127.0.0.1", port=DEFAULT_PORT, password=None, client=None):
        super().__init__(driver_id)
        self.host = host
        self.port = port
        self.password = password
        self._client = client

    def capabilities(self):
        return {
            "family": "obs",
            "transport": "websocket-v5",
            "actions": ["state", "scenes", "switch", "stream"],
            "guards": ["stream start/stop need explicit confirmation"],
        }

    def get_state(self):
        if self._client is None:
            return DriverResult(ok=True, state={"connected": False, "host": self.host, "port": self.port})
        try:
            version = self._client.call("GetVersion", {})
        except Exception as exc:  # noqa: BLE001 - drivers report, never raise
            return DriverResult(ok=False, state={"connected": False}, error=str(exc))
        return DriverResult(ok=True, state={"connected": True, **version})

    def apply(self, command):
        if not isinstance(command, dict):
            return DriverResult(ok=False, error="command must be an object")
        action = command.get("action")
        try:
            client = self._connected_client()
            if action == "state":
                return self.get_state()
            if action == "scenes":
                return DriverResult(ok=True, state=client.call("GetSceneList", {}))
            if action == "switch":
                scene = command.get("scene")
                if not scene:
                    return DriverResult(ok=False, error="switch needs a scene name")
                client.call("SetCurrentProgramScene", {"sceneName": scene})
                return DriverResult(ok=True, state={"scene": scene})
            if action == "stream":
                return DriverResult(ok=False, error="stream control is not implemented in the scaffold")
            return DriverResult(ok=False, error=f"unknown obs action: {action}")
        except Exception as exc:  # noqa: BLE001 - drivers report, never raise
            return DriverResult(ok=False, error=str(exc))

    def safe_stop(self):
        """Disconnect. Never changes OBS state, never raises."""
        try:
            if self._client is not None:
                self._client.close()
        except Exception:  # noqa: BLE001 - safe_stop must never raise
            pass
        finally:
            self._client = None
        return DriverResult(ok=True, state={"connected": False})

    def probe(self):
        """Connect and read the OBS version for the Test Bench."""
        try:
            self._connected_client()
            return self.get_state()
        except Exception as exc:  # noqa: BLE001 - probe must never raise
            return DriverResult(ok=False, error=str(exc))

    def _connected_client(self):
        if self._client is None:
            self._client = _default_client(self.host, self.port, self.password)
        return self._client


def _default_client(host, port, password):
    """Build a live v5 client. Raises ObsError until bring-up lands."""
    try:
        import websocket  # noqa: F401 - live handshake fills this in
    except ImportError as exc:
        raise ObsError("obs-websocket client not installed (live bring-up pending)") from exc
    raise ObsError("obs-websocket v5 handshake not implemented (live bring-up pending)")
