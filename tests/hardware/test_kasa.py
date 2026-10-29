# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hardware: Kasa discovery (and opt-in safe-plug toggle) on the Pi LAN."""

import os
import time

import pytest

from app.drivers.kasa import KasaDriver, discover_plugs

pytestmark = pytest.mark.hardware


def _need_hw():
    if os.environ.get("STUDIO_HW") != "1":
        pytest.skip("hardware tests run on the Pi with STUDIO_HW=1")


def test_discover_finds_plugs():
    _need_hw()
    plugs = discover_plugs(
        os.environ.get("KASA_USERNAME"), os.environ.get("KASA_PASSWORD"), discovery_timeout=10
    )
    assert len(plugs) >= 1, "no Kasa plugs discovered on the LAN"
    print("plugs:", [(plug["host"], plug.get("alias")) for plug in plugs])


def test_safe_plug_toggle_restores():
    _need_hw()
    host = os.environ.get("STUDIO_SAFE_PLUG")
    if not host:
        pytest.skip("set STUDIO_SAFE_PLUG to a plug host to enable the toggle test")
    driver = KasaDriver("safe", host, os.environ.get("KASA_USERNAME"), os.environ.get("KASA_PASSWORD"))
    before = driver.get_state()
    assert before.ok
    was_on = before.state["power"] == "on"
    assert driver.apply({"on": not was_on}).ok
    time.sleep(2)
    assert driver.get_state().state["power"] == ("off" if was_on else "on")
    assert driver.apply({"on": was_on}).ok
    time.sleep(2)
    assert driver.get_state().state["power"] == before.state["power"]
