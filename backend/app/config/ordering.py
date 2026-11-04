# SPDX-License-Identifier: AGPL-3.0-or-later
"""Custom list ordering: one small JSON file per studio, cached in memory.

Layout: studios.local/<id>/ordering.json (tracked studios may also carry
one under studios/<id>/). Shape::

    {"zones": ["hub", "workstation-computer", ...],
     "devices": ["pc-main", ...],
     "modes": [...], "presets": [...], "plugs": ["plug-1", ...]}

Best practice for JSON at this scale: the file is tiny (id arrays only),
so it is read once at load, kept in the Registry, and written atomically
(tmp + replace) on save. No database, no per-request disk reads. Ids in
the file that no longer exist are ignored; ids missing from the file
append in alphabetical order, so the fallback is always alphabetical.
"""

import json
import os
from pathlib import Path

COLLECTIONS = ("zones", "devices", "modes", "presets", "plugs", "panels", "modalities")

# Dashboard cards, in page order. Ordered via the same ordering.json
# ("panels" collection) and applied client-side by data-panel id.
PANELS = [
    ("modalities", "Modalities"),
    ("obs", "OBS"),
    ("zones", "Zones"),
    ("power", "Power"),
    ("hub", "Hub Manager"),
    ("ai", "AI look"),
]


class OrderingError(Exception):
    """Unusable ordering save (bad shape, non-local studio, unknown ids)."""


def ordering_path(config_root, studio_id):
    """Prefer the git-ignored local studio dir; fall back to tracked."""
    local = Path(config_root) / "studios.local" / studio_id / "ordering.json"
    if local.parent.is_dir():
        return local
    tracked = Path(config_root) / "studios" / studio_id / "ordering.json"
    return tracked if tracked.parent.is_dir() else local


def load_ordering(config_root, studio_id):
    """Return {collection: [ids]}; missing/broken file reads as empty."""
    path = ordering_path(config_root, studio_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    clean = {}
    for name in COLLECTIONS:
        value = data.get(name)
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            clean[name] = list(dict.fromkeys(value))
    return clean


def ordered_ids(custom, all_ids, labels=None):
    """Custom order first (existing ids only), rest alphabetical by id.

    (The ordering page's A-Z buttons sort by display label explicitly;
    the implicit fallback everywhere stays id order, matching the
    long-standing sorted() behaviour lists already had.)
    """
    all_ids = list(all_ids)
    known = set(all_ids)
    head = [item for item in (custom or []) if item in known]
    seen = set(head)
    tail = sorted(item for item in all_ids if item not in seen)
    return head + tail


def save_ordering(config_root, studio_id, ordering, valid_ids):
    """Validate and atomically persist ordering. valid_ids: {collection: set}.

    Only local (studios.local) studios are writable; tracked example
    studios are refused so a save can never dirty the repo.
    """
    local_dir = Path(config_root) / "studios.local" / studio_id
    if not local_dir.is_dir():
        raise OrderingError(f"ordering save only for local studios (no studios.local/{studio_id})")
    if not isinstance(ordering, dict):
        raise OrderingError("ordering must be an object of collection to id list")
    clean = {}
    for name, ids in ordering.items():
        if name not in COLLECTIONS:
            raise OrderingError(f"unknown ordering collection: {name!r}")
        if not isinstance(ids, list) or not all(isinstance(v, str) for v in ids):
            raise OrderingError(f"ordering.{name} must be a list of id strings")
        unknown = [v for v in ids if v not in valid_ids.get(name, set())]
        if unknown:
            raise OrderingError(f"ordering.{name} names unknown ids: {unknown}")
        clean[name] = list(dict.fromkeys(ids))
    path = local_dir / "ordering.json"
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(clean, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)
    return clean
