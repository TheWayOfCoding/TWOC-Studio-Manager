# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for config loading and validation."""

from pathlib import Path

import pytest

from app.config.loader import ConfigError, check_references, list_studios, load_all, load_studio, validate

CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config"


@pytest.mark.unit
def test_list_studios():
    assert {"id": "example-studio", "label": "Example Studio", "source": "studios"} in list_studios(
        CONFIG_ROOT
    )


@pytest.mark.unit
def test_local_root_merges_and_dupes_rejected(tmp_path):
    import json

    (tmp_path / "studios" / "a").mkdir(parents=True)
    (tmp_path / "studios.local" / "b").mkdir(parents=True)
    (tmp_path / "studios" / "a" / "studio.json").write_text(
        json.dumps({"id": "a", "label": "A"}), encoding="utf-8"
    )
    (tmp_path / "studios.local" / "b" / "studio.json").write_text(
        json.dumps({"id": "b", "label": "B"}), encoding="utf-8"
    )
    assert sorted(entry["id"] for entry in list_studios(tmp_path)) == ["a", "b"]
    (tmp_path / "studios.local" / "b" / "studio.json").write_text(
        json.dumps({"id": "a", "label": "dup"}), encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="duplicate studio id"):
        list_studios(tmp_path)


@pytest.mark.unit
def test_load_studio_counts():
    loaded = load_studio(CONFIG_ROOT, "example-studio")
    assert len(loaded["devices"]) == 4
    assert len(loaded["zones"]) == 2
    assert len(loaded["modes"]) == 2
    assert set(loaded["presets"]) == {"default"}


@pytest.mark.unit
def test_unknown_studio():
    with pytest.raises(ConfigError, match="unknown studio"):
        load_studio(CONFIG_ROOT, "nope")


@pytest.mark.unit
def test_invalid_device_rejected():
    with pytest.raises(ConfigError):
        validate({"id": "x"}, "device", "test")


@pytest.mark.unit
def test_bad_zone_reference_rejected():
    data = load_all(CONFIG_ROOT)
    loaded = data["studios"]["example-studio"]
    loaded["devices"][0]["zone"] = "nope"
    with pytest.raises(ConfigError, match="unknown zone"):
        check_references(loaded, data["profiles"], "example-studio")


@pytest.mark.unit
def test_bad_profile_reference_rejected():
    data = load_all(CONFIG_ROOT)
    loaded = data["studios"]["example-studio"]
    loaded["devices"][0]["profile"] = "nope"
    with pytest.raises(ConfigError, match="unknown profile"):
        check_references(loaded, data["profiles"], "example-studio")


@pytest.mark.unit
def test_defaults_missing_reads_as_empty(tmp_path):
    import json
    import shutil

    shutil.copytree(CONFIG_ROOT / "profiles", tmp_path / "profiles")
    studio_dir = tmp_path / "studios.local" / "x"
    shutil.copytree(CONFIG_ROOT / "studios" / "example-studio", studio_dir)
    (studio_dir / "defaults.json").unlink()
    meta_path = studio_dir / "studio.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["id"] = "x"
    del meta["defaultSet"]
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    loaded = load_studio(tmp_path, "x")
    assert loaded["defaults"] == {}


@pytest.mark.unit
def test_bad_default_set_references_rejected():
    data = load_all(CONFIG_ROOT)
    loaded = data["studios"]["example-studio"]
    loaded["studio"]["defaultSet"] = "nope"
    with pytest.raises(ConfigError, match="unknown default set"):
        check_references(loaded, data["profiles"], "example-studio")
    loaded["studio"]["defaultSet"] = "default"
    loaded["defaults"]["default"]["devices"]["nope"] = {"intensity": 5}
    with pytest.raises(ConfigError, match="unknown device"):
        check_references(loaded, data["profiles"], "example-studio")
