# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static regression tests for the dashboard page structure (no JS harness)."""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.mark.unit
def test_dashboard_structure():
    """Dashboard: studio picker, modality runs, AI prompt, zone/power/OBS controls."""
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    for marker in (
        'id="studio"',
        'id="ai-prompt"',
        'id="ai-provider"',
        "aiLook(",
        "aiSave(",
        'id="ai-preset-id"',
        'data-panel="modalities"',
        'id="modality-pick"',
        'id="modality-out"',
        "loadModalities(",
        "modalityStart(",
        "modalityRun(",
        "modalityStop(",
        "modalityPoll(",
        ">Start</button>",
        "/presets/",
        "/modalities/",
        "/ai/look",
        "/ai/save-preset",
    ):
        assert marker in html
    for gone in (
        'data-panel="modes"',
        'data-panel="presets"',
        'data-panel="presentations"',
        "applyMode(",
        "applyPreset(",
        "showRun(",
        "loadShows(",
        "/modes/",
        "/shows/",
    ):
        assert gone not in html
    for marker in (
        'id="actions"',
        'id="undo-btn"',
        'id="ai-scope"',
        'id="ai-scope-zones"',
        'id="ai-scope-lights"',
        "loadScope(",
        "scopeRefresh(",
        "scope_mode",
        'id="zones"',
        'id="power"',
        'id="all-off"',
        'id="all-off-manual"',
        "loadZones(",
        "zonePower(",
        "zoneObsAuto(",
        "Start OBS automatically after boot?",
        "powerAllOff(",
        "/zones/",
        "/power/all-off",
        'id="ai-mic"',
        "aiMic(",
        "SpeechRecognition",
        'id="status"',
        'id="obs"',
        'id="obs-confirm"',
        'id="obs-collection"',
        "loadStatus(",
        ">Refresh</button>",
        "zone-pwr",
        'x.id === "workstation-computer"',
        "obsSwitch(",
        "obsStream(",
        "obsCollection(",
        "/obs/status",
        "/obs/switch",
        "/obs/stream",
        "/obs/collection",
        'data-panel="pcobs"',
        'id="pcobs-collection"',
        'id="pcobs-scene"',
        'id="pcobs-status"',
        'id="pcobs-out"',
        "pcobsStart(",
        "pcobsStop(",
        "pcobsStatus(",
        "/obs/boot",
        "loadActions(",
        "undoLast(",
        "(non-JSON reply",
        "/actions",
        "/actions/undo",
        'id="snap-id"',
        "snapHardware(",
        "/presets/from-hardware",
        'id="hub-confirm"',
        'id="hub-out"',
        "hubRestart(",
        "/api/hub/restart",
    ):
        assert marker in html


@pytest.mark.unit
def test_ai_mic_block_static():
    """Mic block: tall prompt box, auto-direct, guidance, friendly errors.

    File read, not HTTP: must stay green without a socket bind.
    """
    from pathlib import Path as PathLib

    html = (PathLib(__file__).resolve().parents[2] / "backend" / "app" / "static" / "index.html").read_text(
        encoding="utf-8"
    )
    for marker in (
        '<textarea id="ai-prompt" rows="6"',
        'id="ai-mic-auto"',
        'id="ai-mic-hint"',
        "aiMicError(",
        "isBrave()",
        "auto-direct on hear",
        "chrome://settings/content/microphone",
    ):
        assert marker in html


@pytest.mark.unit
def test_hub_css_served_and_linked():
    """Shared stylesheet serves as CSS and every human page links it."""
    css = client.get("/hub.css")
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert ".topbar" in css.text
    for path in ("/", "/bench", "/control", "/equipment"):
        page = client.get(path)
        assert page.status_code == 200
        assert 'href="/hub.css"' in page.text


@pytest.mark.unit
def test_equipment_structure():
    """Equipment page: studio/device pickers, facts, key/value editor, save."""
    response = client.get("/equipment")
    assert response.status_code == 200
    html = response.text
    for marker in (
        'id="eq-studio"',
        'id="eq-device"',
        'id="eq-facts"',
        'id="eq-rows"',
        'id="eq-out"',
        "eqAdd(",
        "eqSave(",
        "loadDevices(",
        "/devices/",
        "/details",
    ):
        assert marker in html
