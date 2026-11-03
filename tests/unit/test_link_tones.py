# SPDX-License-Identifier: AGPL-3.0-or-later
"""Unit tests for the audio-link tone protocol (synthetic audio, no hardware)."""

import struct
import wave
from io import BytesIO

import pytest

from app.linkproto import (
    COMMANDS,
    decode_all_commands,
    decode_all_samples,
    decode_command,
    decode_samples,
    encode_samples,
    encode_wav,
    frame_for,
)


def _wav_to_floats(payload):
    with wave.open(BytesIO(payload), "rb") as handle:
        assert handle.getnchannels() == 1 and handle.getsampwidth() == 2
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    shorts = struct.unpack("<%dh" % (len(raw) // 2), raw)
    return [s / 32767 for s in shorts], rate


@pytest.mark.unit
def test_frame_shape_and_timing():
    frame = frame_for("obs")
    assert frame[0] == "#" and len(frame) == 4
    samples = encode_samples(frame, 44100)
    seconds = len(samples) / 44100
    assert 0.75 < seconds < 0.9  # preamble + 3 digits + gaps


@pytest.mark.unit
def test_round_trip_every_command():
    for rate in (44100, 48000):
        for name in COMMANDS:
            assert decode_command(encode_samples(frame_for(name), rate), rate) == name


@pytest.mark.unit
def test_round_trip_through_wav_bytes():
    floats, rate = _wav_to_floats(encode_wav(frame_for("obs"), 44100))
    assert decode_command(floats, rate) == "obs"


@pytest.mark.unit
def test_corrupt_check_digit_rejected():
    frame = frame_for("obs")
    bad = frame[:-1] + ("0" if frame[-1] != "0" else "1")
    assert bad != frame
    assert decode_samples(encode_samples(bad, 44100), 44100) is None


@pytest.mark.unit
def test_silence_and_empty_decode_none():
    assert decode_samples([], 44100) is None
    assert decode_samples([0.0] * 44100, 44100) is None


@pytest.mark.unit
def test_unknown_valid_code_has_no_name():
    assert decode_samples(encode_samples("#055", 44100), 44100) == "05"
    assert decode_command(encode_samples("#055", 44100), 44100) is None


@pytest.mark.unit
def test_reverse_ack_codes_pinned():
    """Main TX -> hub RX acks share the framing; codes are a stable contract."""
    assert {k: COMMANDS[k] for k in ("ping", "obs", "ack", "obs_ready", "obs_fail")} == {
        "ping": "00",
        "obs": "01",
        "ack": "02",
        "obs_ready": "03",
        "obs_fail": "04",
    }
    for rate in (44100, 48000):
        for name in ("ack", "obs_ready", "obs_fail"):
            assert decode_command(encode_samples(frame_for(name), rate), rate) == name


@pytest.mark.unit
def test_decode_all_finds_bursts_in_order():
    """A long wait window holding ack-then-obs_ready decodes both, ordered."""
    rate = 44100
    silence = [0.0] * int(rate * 0.5)
    buf = encode_samples(frame_for("ack"), rate) + silence + encode_samples(frame_for("obs_ready"), rate)
    assert decode_all_samples(buf, rate) == ["02", "03"]
    assert decode_all_commands(buf, rate) == ["ack", "obs_ready"]
    assert decode_all_samples([0.0] * rate, rate) == []
    assert decode_all_commands([], rate) == []


@pytest.mark.unit
def test_decode_all_drops_corrupt_keeps_unknown_digits():
    """Corrupt frames vanish; unknown-but-valid codes decode as digits only."""
    rate = 44100
    good = frame_for("ping")
    bad = good[:-1] + ("0" if good[-1] != "0" else "1")
    gap = [0.0] * int(rate * 0.4)  # real bursts land seconds apart, never glued
    buf = encode_samples(bad, rate) + gap + encode_samples("#055", rate) + gap + encode_samples(good, rate)
    assert decode_all_samples(buf, rate) == ["05", "00"]
    assert decode_all_commands(buf, rate) == ["ping"]


class _FakeCallbackStream:
    """Canned capture: feeds script samples through the stream callback."""

    def __init__(self, samples, chunk_frames, pace_s=0.005):
        self.samples = list(samples)
        self.chunk_frames = chunk_frames
        self.pace_s = pace_s
        self.callback = None
        self.thread = None

    def bind(self, callback):
        self.callback = callback
        return self

    def __enter__(self):
        import threading

        self.thread = threading.Thread(target=self._feed, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.thread.join(timeout=5)
        return False

    def _feed(self):
        import time as time_mod

        import numpy as np

        while self.samples:
            piece, self.samples = self.samples[: self.chunk_frames], self.samples[self.chunk_frames :]
            self.callback(np.array([[s] for s in piece]), len(piece), None, None)
            time_mod.sleep(self.pace_s)


def _stub_capture(monkeypatch, samples, chunk_frames=11025):
    from app.drivers import link_audio

    monkeypatch.setattr(link_audio, "_sd", lambda: object())
    monkeypatch.setattr(
        link_audio,
        "_open_input_stream",
        lambda sd, rate, device, callback: _FakeCallbackStream(samples, chunk_frames).bind(callback),
    )


@pytest.mark.unit
def test_listen_until_returns_on_first_expected(monkeypatch):
    """Rolling listen exits at the answer instead of burning the window."""
    from app.drivers import link_audio

    rate = 44100
    script = [0.0] * int(rate * 0.5) + encode_samples(frame_for("ack"), rate)
    _stub_capture(monkeypatch, script)
    heard = link_audio.listen_until(8.0, ["ack"], rate=rate, poll_s=0.1, window_s=2.0)
    assert heard["commands"] == ["ack"]
    assert heard["seconds"] < 2.0


@pytest.mark.unit
def test_listen_until_waits_out_unexpected(monkeypatch):
    """Non-matching bursts are recorded; the wait still runs its course."""
    from app.drivers import link_audio

    rate = 44100
    script = [0.0] * int(rate * 0.3) + encode_samples(frame_for("ping"), rate)
    _stub_capture(monkeypatch, script)
    heard = link_audio.listen_until(0.6, ["ack"], rate=rate, poll_s=0.1, window_s=1.0)
    assert heard["commands"] == ["ping"]
    assert heard["seconds"] >= 0.5


@pytest.mark.unit
def test_listen_until_collapses_repeat_decodes(monkeypatch):
    """One burst reads as one entry however many polls re-decode it."""
    from app.drivers import link_audio

    rate = 44100
    script = [0.0] * int(rate * 0.3) + encode_samples(frame_for("ack"), rate)
    _stub_capture(monkeypatch, script)
    heard = link_audio.listen_until(1.0, ["obs_ready"], rate=rate, poll_s=0.1, window_s=1.0)
    assert heard["commands"] == ["ack"]
