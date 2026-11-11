# SPDX-License-Identifier: AGPL-3.0-or-later
"""Static regression tests for the shared topbar menu (no JS harness).

Every hub page carries the same menu: Dashboard, Control, and an
Administration dropdown (Equipment first). A hamburger toggles the
menu on narrow screens; shared behavior lives in /nav.js.
"""

from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "backend" / "app" / "static"

NAV_PAGES = (
    "index.html",
    "control.html",
    "equipment.html",
    "modality.html",
    "ordering.html",
    "obs.html",
    "bench.html",
)

# Pages whose current link sits inside the Administration dropdown.
ADMIN_PAGES = {"equipment.html", "modality.html", "ordering.html", "obs.html", "bench.html"}


def _page(name):
    # File read, not HTTP: marker regression must run without a socket bind.
    return (STATIC / name).read_text(encoding="utf-8")


@pytest.mark.unit
def test_nav_structure_shared_across_pages():
    """Hamburger + dropdown + shared script on every page, no stale label."""
    for name in NAV_PAGES:
        html = _page(name)
        for marker in (
            '<nav id="mainnav" aria-label="Main">',
            'class="nav-toggle"',
            'aria-controls="mainnav"',
            'class="navdrop"',
            'class="navdrop-toggle"',
            'aria-controls="navadmin"',
            'class="navdrop-menu"',
            '<script src="/nav.js" defer></script>',
        ):
            assert marker in html, f"{name} missing {marker}"
        assert "navgroup" not in html, f"{name} still carries the flat label"


@pytest.mark.unit
def test_equipment_lives_inside_administration():
    """Equipment is the first dropdown entry, never a top-level link."""
    for name in NAV_PAGES:
        html = _page(name)
        assert '<a href="/control">Control</a><div class="navdrop">' in html.replace(
            '<a href="/control" aria-current="page">Control</a>', '<a href="/control">Control</a>'
        ), f"{name}: Control must lead straight into the dropdown"
        menu = html[html.index("navdrop-menu") :]
        assert "/equipment" in menu.split("</nav>")[0], f"{name}: Equipment not in dropdown"


@pytest.mark.unit
def test_nav_current_page_marked():
    """Exactly one current link per page; the toggle lights up inside admin."""
    for name in NAV_PAGES:
        html = _page(name)
        assert html.count('aria-current="page"') == 1, f"{name}: want one current link"
        if name in ADMIN_PAGES:
            assert 'aria-current="true">Administration' in html, f"{name}: toggle must glow"
        else:
            assert "aria-current=\"true\">Administration" not in html, f"{name}: toggle must not glow"


@pytest.mark.unit
def test_nav_css_and_script_served():
    """Dropdown + hamburger rules in the stylesheet; script has the toggles."""
    css = (STATIC / "hub.css").read_text(encoding="utf-8")
    for marker in (
        ".nav-toggle",
        ".navdrop-menu",
        ".topbar.open nav",
        ".navdrop:not(.open) > .navdrop-menu",
    ):
        assert marker in css
    js = (STATIC / "nav.js").read_text(encoding="utf-8")
    for marker in ("nav-toggle", "navdrop-toggle", "aria-expanded", "Escape"):
        assert marker in js


@pytest.mark.unit
def test_nav_js_route_serves():
    """GET /nav.js returns the shared menu script."""
    from fastapi.testclient import TestClient

    from app.main import app

    response = TestClient(app).get("/nav.js")
    assert response.status_code == 200
    assert "navdrop-toggle" in response.text
