# SPDX-License-Identifier: AGPL-3.0-or-later
"""OBS Studio driver over OBS WebSocket v5 (scene switch, state, guarded stream).

The wire client is injected (tests) or built lazily from `websocket-client`:
Hello -> Identify (v5 password auth when the server demands it) -> Identified,
then request/response calls with stray events skipped. Guarded stream control
stays unimplemented (module enforces confirmation first).
"""

import base64
import hashlib
import json

from .base import Driver, DriverResult

DEFAULT_PORT = 4455
DEFAULT_TIMEOUT = 10
RPC_VERSION = 1


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
                return DriverResult(ok=False, error="stream control is not implemented")
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


def _auth_secret(password, salt, challenge):
    """v5 auth string: b64(sha256(b64(sha256(password + salt)) + challenge))."""
    inner = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((inner + challenge).encode()).digest()).decode()


class ObsWsClient:
    """Sync v5 client over one websocket-client connection. call/close surface."""

    def __init__(self, sock):
        self._sock = sock
        self._seq = 0

    @classmethod
    def connect(cls, host, port, password, timeout=DEFAULT_TIMEOUT):
        try:
            import websocket
        except ImportError as exc:
            raise ObsError("obs-websocket client not installed (need websocket-client)") from exc
        try:
            sock = websocket.create_connection(f"ws://{host}:{port}", timeout=timeout)
        except Exception as exc:
            raise ObsError(f"obs connect failed ({host}:{port}): {exc}") from exc
        try:
            hello = cls._recv(sock)
            if hello.get("op") != 0:
                raise ObsError(f"obs handshake: expected Hello, got op {hello.get('op')}")
            identify = {"rpcVersion": RPC_VERSION, "eventSubscriptions": 0}
            auth = (hello.get("d") or {}).get("authentication")
            if auth is not None:
                if not password:
                    raise ObsError("obs requires a password (set one in the bench card)")
                identify["authentication"] = _auth_secret(password, auth["salt"], auth["challenge"])
            sock.send(json.dumps({"op": 1, "d": identify}))
            identified = cls._recv(sock)
            if identified.get("op") != 2:
                raise ObsError(f"obs handshake: expected Identified, got op {identified.get('op')}")
        except Exception:
            try:
                sock.close()
            except Exception:  # noqa: BLE001 - handshake failure already reports
                pass
            raise
        return cls(sock)

    @staticmethod
    def _recv(sock):
        try:
            return json.loads(sock.recv())
        except Exception as exc:
            raise ObsError(f"obs connection lost: {exc}") from exc

    def call(self, request, data):
        """One request/response round trip. Stray events are skipped."""
        self._seq += 1
        request_id = str(self._seq)
        try:
            self._sock.send(json.dumps({"op": 6, "d": {"requestType": request, "requestId": request_id, "requestData": data or {}}}))
        except Exception as exc:
            raise ObsError(f"obs send failed ({request}): {exc}") from exc
        while True:
            frame = self._recv(self._sock)
            if frame.get("op") == 5:
                continue
            if frame.get("op") != 7:
                raise ObsError(f"obs call {request}: unexpected op {frame.get('op')}")
            body = frame.get("d") or {}
            if body.get("requestId") != request_id:
                continue
            status = body.get("requestStatus") or {}
            if status.get("result") is not True:
                raise ObsError(f"obs call {request} failed ({status.get('code')}): {status.get('comment')}")
            return body.get("responseData") or {}

    def close(self):
        try:
            self._sock.close()
        except Exception:  # noqa: BLE001 - close must never raise
            pass


def _default_client(host, port, password):
    """Build a live v5 client (connects + identifies immediately)."""
    return ObsWsClient.connect(host, port, password)
