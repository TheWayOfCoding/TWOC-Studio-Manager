# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hardware-attached tests. Pi only: skipped unless STUDIO_HW=1."""

import os

import pytest

pytestmark = pytest.mark.hardware


def test_placeholder():
    if os.environ.get("STUDIO_HW") != "1":
        pytest.skip("hardware tests run on the Pi with STUDIO_HW=1")
    assert True
