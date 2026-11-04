# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the screens scaffold (in-memory state, no sockets)."""

import pytest

from app.drivers.screen import SCREEN_STATE, ScreenDriver
from app.modules.screens import ScreensModule


@pytest.fixture(autouse=True)
def clean_state():
    SCREEN_STATE.clear()
    yield
    SCREEN_STATE.clear()


@pytest.fixture()
def module():
    return ScreensModule("screens", {"back": ScreenDriver("screen-back", "back")})


@pytest.mark.unit
def test_push_roundtrip(module):
    result = module.push("back", theme="stream_starting", text="Hello")
    assert result.ok is True
    assert result.state == {"screen": "back", "theme": "stream_starting", "text": "Hello"}
    assert module.drivers["back"].get_state().state["text"] == "Hello"


@pytest.mark.unit
def test_push_needs_content(module):
    assert module.push("back").ok is False


@pytest.mark.unit
def test_audio_transport(module):
    played = module.audio("back", "play", track="lofi")
    assert played.ok is True
    assert played.state["track"] == "lofi"
    stopped = module.audio("back", "stop")
    assert stopped.state["audio"] == "stop"
    assert "track" not in stopped.state
    assert module.audio("back", "pause").ok is False


@pytest.mark.unit
def test_audio_volume_defaults_gentle(module):
    played = module.audio("back", "play", track="lofi")
    assert played.ok is True
    assert played.state["volume"] == 10


@pytest.mark.unit
def test_audio_explicit_volume_full_range_at_driver(module):
    """The driver takes any 0..100 level; the gentle cap lives in the API."""
    assert module.audio("back", "play", volume=20).state["volume"] == 20
    assert module.audio("back", "play", volume=100).state["volume"] == 100
    replay = module.audio("back", "play")
    assert replay.state["volume"] == 100  # omitted volume replays the last level


@pytest.mark.unit
def test_audio_rejects_bad_volume(module):
    for bad in ("loud", 101, -1, True, 12.5):
        assert module.audio("back", "play", volume=bad).ok is False


@pytest.mark.unit
def test_audio_stop_keeps_volume(module):
    module.audio("back", "play", volume=15)
    stopped = module.audio("back", "stop")
    assert stopped.state["audio"] == "stop"
    assert stopped.state["volume"] == 15
    assert "track" not in stopped.state


@pytest.mark.unit
def test_safe_stop_keeps_theme(module):
    module.push("back", theme="work")
    module.audio("back", "play")
    stopped = module.drivers["back"].safe_stop()
    assert stopped.state == {"screen": "back", "theme": "work", "audio": "stop", "volume": 10}


@pytest.mark.unit
def test_apply_mapping_unknown_screen(module):
    results = module.apply_mapping({"back": {"theme": "live"}, "nope": {"theme": "x"}})
    assert results["back"].ok is True
    assert results["nope"].ok is False
    assert "nope" in results["nope"].error


@pytest.mark.unit
def test_push_empty_strings_clear_screen(module):
    """Empty theme/text is the explicit clear: theme goes, text blanks.

    The bench/control pages send this from their Clear button; before,
    empty inputs were dropped to null and the old look stayed forever.
    """
    module.push("back", theme="live", text="Hello")
    cleared = module.push("back", theme="", text="")
    assert cleared.ok is True
    assert "theme" not in cleared.state
    assert cleared.state["text"] == ""
    state = module.drivers["back"].get_state().state
    assert "theme" not in state
    assert state["text"] == ""


@pytest.mark.unit
def test_text_ttl_expires(module):
    """A timed text vanishes from state once its seconds run out."""
    pushed = module.push("back", text="Brief", text_ttl_s=60)
    assert pushed.ok is True
    assert pushed.state["text"] == "Brief"
    assert "text_expires_at" in pushed.state
    SCREEN_STATE["back"]["text_expires_at"] = 0.0  # long past (monotonic)
    state = module.drivers["back"].get_state().state
    assert "text" not in state
    assert "text_expires_at" not in state


@pytest.mark.unit
def test_animation_set_clear_and_validate(module):
    """Animations are their own layer: known names only, empty clears."""
    lit = module.drivers["back"].apply({"animation": "fireplace"})
    assert lit.ok is True
    assert lit.state["animation"] == "fireplace"
    tank = module.drivers["back"].apply({"animation": "fishtank"})
    assert tank.state["animation"] == "fishtank"
    assert module.drivers["back"].apply({"animation": "country"}).ok is False
    cleared = module.drivers["back"].apply({"animation": ""})
    assert cleared.ok is True
    assert "animation" not in cleared.state


@pytest.mark.unit
def test_new_animations_and_paired_song(module):
    """Space/reef/task scenes validate; a mapping track starts the bed."""
    for name in ("underwater", "starfield", "nebula", "galaxy", "code", "arcade", "gears", "dice",
                 "puzzle", "bistro", "tailor", "unbox", "aperture", "circuit", "photons"):
        result = module.push("back", animation=name)
        assert result.ok is True, name
        assert result.state["animation"] == name
    paired = module.apply_mapping({"back": {"animation": "nebula", "track": "space", "volume": 12}})
    assert paired["back"].ok is True
    assert paired["back"].state["audio"] == "play"
    assert paired["back"].state["track"] == "space"


@pytest.mark.unit
def test_audio_fx_one_shots(module):
    """FX stamp name+seq into state; unknown names and bad volumes fail."""
    first = module.fx("back", "coin", volume=40)
    assert first.ok is True
    assert first.state["fx"] == {"name": "coin", "seq": 1, "volume": 40}
    second = module.fx("back", "levelup")
    assert second.state["fx"]["seq"] == 2
    assert module.fx("back", "airhorn").ok is False
    assert module.fx("back", "coin", volume=101).ok is False
    via_map = module.apply_mapping({"back": {"animation": "galaxy", "fx": "stinger", "volume": 25}})
    assert via_map["back"].ok is True


@pytest.mark.unit
def test_animation_via_module_push_and_mapping(module):
    pushed = module.push("back", animation="fishtank")
    assert pushed.ok is True
    assert pushed.state["animation"] == "fishtank"
    results = module.apply_mapping({"back": {"animation": "fireplace"}})
    assert results["back"].state["animation"] == "fireplace"


@pytest.mark.unit
def test_timed_card_takes_animation_with_it(module):
    """A timed card's animation expires with its text, same as theme."""
    module.push("back", animation="fireplace", text="Brief", text_ttl_s=60)
    SCREEN_STATE["back"]["text_expires_at"] = 0.0
    state = module.drivers["back"].get_state().state
    assert "text" not in state
    assert "animation" not in state


@pytest.mark.unit
def test_text_ttl_takes_pushed_theme_with_it(module):
    """A timed card expires whole: text AND the theme pushed with it.

    Field report: the text vanished but the candle theme kept pulsing
    on an empty screen. A theme pushed in the same breath as timed
    text is part of the card; a theme pushed on its own is not.
    """
    module.push("back", theme="candle", text="Brief", text_ttl_s=60)
    SCREEN_STATE["back"]["text_expires_at"] = 0.0
    state = module.drivers["back"].get_state().state
    assert "text" not in state
    assert "theme" not in state


@pytest.mark.unit
def test_text_ttl_keeps_earlier_theme(module):
    """Timed text alone never kills a theme that was already up."""
    module.push("back", theme="live")
    module.push("back", text="Brief", text_ttl_s=60)
    SCREEN_STATE["back"]["text_expires_at"] = 0.0
    state = module.drivers["back"].get_state().state
    assert "text" not in state
    assert state["theme"] == "live"


@pytest.mark.unit
def test_text_ttl_validation(module):
    assert module.push("back", text="x", text_ttl_s=-1).ok is False
    assert module.push("back", text="x", text_ttl_s="soon").ok is False
    assert module.push("back", text="x", text_ttl_s=True).ok is False
    assert module.push("back", text_ttl_s=30).ok is False  # ttl needs text
    sticky = module.push("back", text="Stay")
    assert "text_expires_at" not in sticky.state


@pytest.mark.unit
def test_wash_splash_and_clear(module):
    """Wash tints the kiosk behind the caption; null clears it back off."""
    splashed = module.drivers["back"].apply({"wash": {"color": "#2040ff", "opacity": 0.5}})
    assert splashed.ok is True
    assert splashed.state["wash"] == {"color": "#2040ff", "opacity": 0.5}
    assert module.drivers["back"].get_state().state["wash"] == {"color": "#2040ff", "opacity": 0.5}
    cleared = module.drivers["back"].apply({"wash": None})
    assert cleared.ok is True
    assert "wash" not in cleared.state
    assert "wash" not in module.drivers["back"].get_state().state


@pytest.mark.unit
def test_wash_rejects_bad_shape(module):
    assert module.drivers["back"].apply({"wash": "blue"}).ok is False
    assert module.drivers["back"].apply({"wash": {"color": "#fff", "opacity": 9}}).ok is False
    assert module.drivers["back"].apply({"wash": {}}).ok is False
