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
        'id="pwr-delay-on"',
        'id="pwr-delay-s"',
        "power_delay_s",
        "applyMode(",
        "applyPreset(",
        "aiLook(",
        "aiSave(",
        'id="ai-preset-id"',
        'id="shows"',
        'id="show-confirm"',
        "loadShows(",
        "showRun(",
        "showStop(",
        "/modes/",
        "/presets/",
        "/shows/",
        "/ai/look",
        "/ai/save-preset",
    ):
        assert marker in html
