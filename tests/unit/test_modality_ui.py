# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static regression tests for the Modality Manager page (no JS harness)."""

from pathlib import Path

import pytest

MODALITY_HTML = Path(__file__).resolve().parents[2] / "backend" / "app" / "static" / "modality.html"


def _modality_html():
    # File read, not HTTP: marker regression must run without a socket bind.
    return MODALITY_HTML.read_text(encoding="utf-8")


@pytest.mark.unit
def test_modality_picker_drives_every_action():
    """One dropdown selection feeds Edit, Run, Stop, and Delete together."""
    html = _modality_html()
    for marker in (
        'id="modality-pick"',
        'id="run-state"',
        "picked()",
        "editSelected(",
        "runSelected(",
        "stopSelected(",
        "deleteSelected(",
        "newModality(",
        "loadAll(",
        "/modalities",
        ">Edit</button>",
        ">Run selected</button>",
    ):
        assert marker in html
    # No per-row edit buttons: selection is the single source of truth.
    assert 'onclick="selectModality(' not in html


@pytest.mark.unit
def test_modality_editor_forms_present():
    """Forms-first editor: id/label/kind, beat cards, save path."""
    html = _modality_html()
    for marker in (
        'id="ed-id"',
        'id="ed-label"',
        'id="ed-kind"',
        'id="beats"',
        "addBeat(",
        "collectBeats(",
        "saveModality(",
        "selectModality(",
    ):
        assert marker in html
