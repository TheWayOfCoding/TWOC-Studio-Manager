# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the data-map page and its read APIs (no JS harness)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

STATIC = Path(__file__).resolve().parents[2] / "backend" / "app" / "static"


@pytest.mark.unit
def test_datamap_route_serves_page():
    """GET /datamap returns the link-map page."""
    client = TestClient(app)
    response = client.get("/datamap")
    assert response.status_code == 200
    assert "Link map" in response.text
    assert "/api/studios/" in response.text


@pytest.mark.unit
def test_datamap_markers_present():
    """Diagram, live tables, and health checks all ship in the page."""
    html = (STATIC / "datamap.html").read_text(encoding="utf-8")
    for marker in (
        "dm-studio",
        "dm-collections",
        "dm-devices",
        "dm-health",
        "dmLoad()",
        "/ranges",
        "/api/profiles",
        "data-t=\"devices\"",
        "all hard links resolve",
    ):
        assert marker in html


@pytest.mark.unit
def test_datamap_linked_from_every_admin_menu():
    """Each hub page links the data map in Administration."""
    for page in ("index.html", "control.html", "equipment.html", "modality.html",
                 "ordering.html", "obs.html", "bench.html", "datamap.html"):
        html = (STATIC / page).read_text(encoding="utf-8")
        assert 'href="/datamap"' in html, page


@pytest.mark.unit
def test_profiles_endpoint_lists_shared_models():
    """GET /api/profiles exposes the shared spec truth (ids + kinds)."""
    client = TestClient(app)
    response = client.get("/api/profiles")
    assert response.status_code == 200
    ids = {entry["id"] for entry in response.json()}
    assert {"gvm-1500d", "demo-flood", "bytech-strip"} <= ids
