# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static regression tests for the dashboard page structure (no JS harness)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.mark.unit
def test_dashboard_structure():
    """Mode dashboard: studio picker, mode/preset buttons, AI prompt, apply calls."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    for marker in (
        'id="studio"',
        'id="modes"',
        'id="presets"',
        'id="ai-prompt"',
        "applyMode(",
        "applyPreset(",
        "aiLook(",
        "/modes/",
        "/presets/",
        "/ai/look",
    ):
        assert marker in html
