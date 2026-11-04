# SPDX-License-Identifier: AGPL-3.0-or-later
"""The per-user listener copy must run standalone (show-seat regression).

Each Windows account that runs the show holds its own pc-link folder with no
repo backend behind it, so listener.py/send.py fall back to a top-level
linkproto import. Shipping that folder without linkproto.py (or dropping the
fallback import) kills the copy at import time with no log line -- exactly
how a show seat silently never started.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PCLINK = REPO_ROOT / "tools" / "pc-link"


@pytest.mark.unit
def test_per_user_file_list_ships_linkproto():
    """README step 3 names every file a standalone seat copy needs."""
    readme = (PCLINK / "README.md").read_text(encoding="utf-8")
    for name in ("listener.py", "send.py", "requirements-link.txt", "linkproto.py"):
        assert name in readme


@pytest.mark.unit
def test_standalone_copy_imports_off_repo(tmp_path):
    """listener.py + send.py + linkproto.py import with no backend tree."""
    for name in ("listener.py", "send.py"):
        shutil.copy(PCLINK / name, tmp_path / name)
    shutil.copy(REPO_ROOT / "backend" / "app" / "linkproto.py", tmp_path / "linkproto.py")
    for module in ("listener", "send"):
        proc = subprocess.run(
            [sys.executable, "-c", f"import {module}; print(sorted({module}.COMMANDS))"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert "linkproto" not in (proc.stderr or "")
        assert "obs" in proc.stdout
