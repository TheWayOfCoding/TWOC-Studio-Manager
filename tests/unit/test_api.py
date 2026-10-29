# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the REST API surface (mock drivers, example studio)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.mark.unit
def test_health_ok():
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "example-studio" in body["studios"]
    assert "version" in body


@pytest.mark.unit
def test_studios_list():
    response = client.get("/api/studios")
    assert response.status_code == 200
    assert {"id": "example-studio", "label": "Example Studio", "source": "studios"} in response.json()


@pytest.mark.unit
def test_studio_devices_merged():
    response = client.get("/api/studios/example-studio/devices")
    assert response.status_code == 200
    devices = {item["id"]: item for item in response.json()}
    assert sorted(devices) == ["demo-cam", "demo-key", "demo-rgb", "demo-screen"]
    assert devices["demo-key"]["spec"]["maxWatts"] == 60


@pytest.mark.unit
def test_unknown_studio_404():
    assert client.get("/api/studios/nope/devices").status_code == 404


@pytest.mark.unit
def test_bench_drivers_list():
    response = client.get("/api/testbench/drivers")
    assert response.status_code == 200
    ids = sorted(item["id"] for item in response.json())
    assert ids == ["mock-dmx", "mock-gvm", "mock-ir", "mock-kasa", "mock-obs", "mock-screen"]


@pytest.mark.unit
def test_bench_probe_and_apply():
    probe = client.post("/api/testbench/mock-kasa/probe")
    assert probe.status_code == 200
    assert probe.json()["ok"] is True
    apply = client.post("/api/testbench/mock-kasa/apply", json={"command": {"power": "on"}})
    assert apply.status_code == 200
    assert apply.json()["state"]["power"] == "on"


@pytest.mark.unit
def test_bench_unknown_driver_404():
    assert client.post("/api/testbench/nope/probe").status_code == 404
