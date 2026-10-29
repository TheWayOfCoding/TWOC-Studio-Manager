# SPDX-License-Identifier: AGPL-3.0-or-later
"""Kasa smart-plug driver (TP-Link HS103 and family) over the LAN.

Synchronous wrapper around python-kasa's asyncio API. Call only outside a
running event loop (FastAPI sync routes run in a threadpool, so this holds).
Newer firmware may require the TP-Link account credentials; pass them through
from Pi-local config, never from the repo.
"""

import asyncio

from .base import Driver, DriverResult


class KasaError(Exception):
    """Plug unreachable, auth failed, or command rejected."""


def _creds(username, password):
    if username or password:
        return {"username": username or "", "password": password or ""}
    return {}


async def _connected_device(host, username, password, timeout):
    import kasa

    device = await kasa.Discover.discover_single(host, timeout=timeout, **_creds(username, password))
    if device is None:
        raise KasaError(f"no Kasa device at {host}")
    await device.update()
    return device


async def _apply_and_verify(host, want, username, password, timeout):
    """Switch the relay, then prove the new state with a fresh re-read.

    Some firmware applies the relay change but answers the acknowledgement in
    an unexpected form ("Communication error on system:set_relay_state"). The
    re-read is authoritative: matching state means success regardless of the
    ack quirk.
    """
    device = await _connected_device(host, username, password, timeout)
    ack_error = None
    try:
        await device.turn_on() if want == "on" else await device.turn_off()
    except Exception as exc:  # noqa: BLE001 - verified below, not trusted
        ack_error = str(exc)
    await device.update()
    actual = "on" if device.is_on else "off"
    if actual != want:
        raise KasaError(ack_error or f"plug did not switch (still {actual})")
    state = {"power": actual, "alias": device.alias, "model": device.model}
    if ack_error:
        state["note"] = "verified by re-read (ack quirk)"
    return state


async def adiscover_plugs(username=None, password=None, discovery_timeout=8):
    """Discover plugs on the LAN. Returns [{host, alias, model}] (no secrets)."""
    import kasa

    found = await kasa.Discover.discover(
        discovery_timeout=discovery_timeout, **_creds(username, password)
    )
    plugs = []
    for host, device in found.items():
        try:
            await device.update()
            plugs.append(
                {"host": host, "alias": device.alias, "model": device.model, "on": device.is_on}
            )
        except Exception as exc:  # noqa: BLE001 - one bad plug must not hide the rest
            plugs.append({"host": host, "alias": None, "model": None, "on": None, "error": str(exc)})
    return sorted(plugs, key=lambda item: item["host"])


def discover_plugs(username=None, password=None, discovery_timeout=8):
    """Sync wrapper for adiscover_plugs."""
    return asyncio.run(adiscover_plugs(username, password, discovery_timeout))


class KasaDriver(Driver):
    """One smart plug at a fixed host. Commands: {"power": "on"|"off"}."""

    def __init__(self, driver_id, host, username=None, password=None, timeout=10):
        super().__init__(driver_id)
        self.host = host
        self.username = username
        self.password = password
        self.timeout = timeout

    def capabilities(self):
        return {"family": "kasa", "transport": "lan", "commands": ["on", "off", "state"]}

    def _connected(self):
        return asyncio.run(_connected_device(self.host, self.username, self.password, self.timeout))

    @staticmethod
    def _state_of(device):
        return {"power": "on" if device.is_on else "off", "alias": device.alias, "model": device.model}

    def get_state(self):
        try:
            return DriverResult(ok=True, state=self._state_of(self._connected()))
        except Exception as exc:  # noqa: BLE001 - drivers report, never raise
            return DriverResult(ok=False, error=str(exc))

    def apply(self, command):
        if not isinstance(command, dict):
            return DriverResult(ok=False, error="command must be an object")
        want = command.get("power")
        if want is None and isinstance(command.get("on"), bool):
            want = "on" if command["on"] else "off"
        if want not in ("on", "off"):
            return DriverResult(ok=False, error='command needs {"power": "on"|"off"}')
        try:
            state = asyncio.run(
                _apply_and_verify(self.host, want, self.username, self.password, self.timeout)
            )
            return DriverResult(ok=True, state=state)
        except Exception as exc:  # noqa: BLE001 - drivers report, never raise
            return DriverResult(ok=False, error=str(exc))

    def safe_stop(self):
        """Plugs never auto-cut: report state, change nothing."""
        result = self.get_state()
        state = dict(result.state)
        state["note"] = "safe_stop is a no-op for plugs"
        return DriverResult(ok=result.ok, state=state, error=result.error)

    def probe(self):
        try:
            device = self._connected()
            state = self._state_of(device)
            state["probed"] = True
            return DriverResult(ok=True, state=state)
        except Exception as exc:  # noqa: BLE001 - probe must never raise
            return DriverResult(ok=False, error=str(exc))
