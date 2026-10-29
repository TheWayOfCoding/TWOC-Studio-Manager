# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pytest bootstrap: make backend/ importable as top-level packages."""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
