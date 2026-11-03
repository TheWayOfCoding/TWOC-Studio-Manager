# SPDX-License-Identifier: AGPL-3.0-or-later
"""Audio-link playback + listen: tone bursts out of a hub audio device,
reverse acks back in through a hub input (no pairing).

sounddevice imports lazily: the hub answers "no audio backend" instead of
crashing when PortAudio is missing (notably under service accounts, which
often see zero audio devices — check /api/link/devices first).
"""

import time

from .base import DriverResult

RATE = 44100


class LinkError(Exception):
    """Missing backend, unknown device, or playback failure."""


def _sd():
    try:
        import sounddevice  # noqa: PLC0415 - optional hub dependency
    except ImportError as exc:
        raise LinkError("no audio backend: pip install sounddevice on the hub") from exc
    return sounddevice


def output_devices():
    """Playback devices as [{index, name, channels}]. Empty when backendless."""
    try:
        sd = _sd()
    except LinkError:
        return []
    devices = []
    for index, info in enumerate(sd.query_devices()):
        if (info.get("max_output_channels") or 0) > 0:
            devices.append(
                {"index": index, "name": info.get("name"), "channels": info["max_output_channels"]}
            )
    return devices


def _resolve_device(want):
    if want is None:
        return None
    if isinstance(want, int) and not isinstance(want, bool):
        return want
    lowered = str(want).lower()
    for device in output_devices():
        if lowered in str(device.get("name") or "").lower():
            return device["index"]
    raise LinkError(f"unknown audio output (see /api/link/devices): {want!r}")


def input_devices():
    """Capture devices as [{index, name, channels}]. Empty when backendless."""
    try:
        sd = _sd()
    except LinkError:
        return []
    devices = []
    for index, info in enumerate(sd.query_devices()):
        if (info.get("max_input_channels") or 0) > 0:
            devices.append(
                {"index": index, "name": info.get("name"), "channels": info["max_input_channels"]}
            )
    return devices


def _resolve_input(want):
    if want is None:
        return None
    if isinstance(want, int) and not isinstance(want, bool):
        return want
    lowered = str(want).lower()
    for device in input_devices():
        if lowered in str(device.get("name") or "").lower():
            return device["index"]
    raise LinkError(f"unknown audio input (see /api/link/inputs): {want!r}")


def record_samples(seconds, rate=RATE, device=None):
    """Blocking capture of mono float samples off a hub input."""
    sd = _sd()
    try:
        resolved = _resolve_input(device)
    except LinkError:
        raise
    except Exception as exc:
        raise LinkError(f"audio input lookup failed: {exc}") from exc
    try:
        frames = int(rate * seconds)
        captured = sd.rec(frames, samplerate=rate, channels=1, device=resolved, blocking=True)
    except Exception as exc:
        raise LinkError(f"capture failed: {exc}") from exc
    try:
        flat = captured.reshape(-1).tolist()
    except AttributeError:
        flat = [float(s) for row in captured for s in (row if isinstance(row, (list, tuple)) else (row,))]
    return [float(s) for s in flat]


def listen_once(seconds=2.0, rate=RATE, device=None):
    """Capture a short window and decode one reverse command (or nothing)."""
    from ..linkproto import decode_command, decode_samples

    samples = record_samples(seconds, rate, device)
    return {
        "command": decode_command(samples, rate),
        "digits": decode_samples(samples, rate),
        "seconds": round(len(samples) / rate, 2) if rate else 0.0,
    }


def listen_all(seconds, rate=RATE, device=None):
    """Capture ONE window and decode EVERY framed command in it, in order.

    Single-capture (not chunked polling) so a burst straddling a chunk
    boundary can never be missed — the whole wait is one buffer and the
    decoder finds each preamble-delimited frame wherever it lands.
    """
    from ..linkproto import decode_all_commands

    samples = record_samples(seconds, rate, device)
    return {
        "commands": decode_all_commands(samples, rate),
        "seconds": round(len(samples) / rate, 2) if rate else 0.0,
    }


RMS_GATE = 0.02


def _open_input_stream(sd, rate, device, callback):
    """One continuous capture stream (factored for tests to stub)."""
    return sd.InputStream(samplerate=rate, channels=1, device=device, callback=callback)


def listen_until(wait_s, expected, rate=RATE, device=None, poll_s=0.25, window_s=2.0):
    """Rolling capture that returns the moment an expected command decodes.

    Callback-driven: PortAudio pushes blocks into a trailing window while
    this polls it, so bursts are caught live with no chunk boundaries to
    straddle (blocking read() yields instant zeros on some Windows host
    APIs — observed on DirectSound). Quiet polls skip the decode via an
    RMS gate. Returns {"seconds": elapsed, "commands": per-burst decodes}
    with consecutive duplicates collapsed, so one burst reads as one entry.
    """
    import collections

    from ..linkproto import decode_all_commands

    sd = _sd()
    try:
        resolved = _resolve_input(device)
    except LinkError:
        raise
    except Exception as exc:
        raise LinkError(f"audio input lookup failed: {exc}") from exc
    expected = list(expected)
    window = collections.deque(maxlen=max(1, int(rate * window_s)))
    min_frames = max(1, int(rate * 0.5))

    def callback(indata, _frames, _time_info, _status):
        window.extend(float(s) for s in indata[:, 0])

    heard = []
    start = time.monotonic()
    try:
        with _open_input_stream(sd, rate, resolved, callback):
            while time.monotonic() - start < wait_s:
                time.sleep(poll_s)
                buf = list(window)
                if len(buf) < min_frames:
                    continue
                if (sum(s * s for s in buf) / len(buf)) ** 0.5 < RMS_GATE:
                    continue
                for name in decode_all_commands(buf, rate):
                    if not heard or heard[-1] != name:
                        heard.append(name)
                    if name in expected:
                        return {"seconds": round(time.monotonic() - start, 2), "commands": heard}
    except LinkError:
        raise
    except Exception as exc:
        raise LinkError(f"capture failed: {exc}") from exc
    return {"seconds": round(time.monotonic() - start, 2), "commands": heard}


def play_samples(samples, rate=RATE, device=None):
    """Blocking playback of mono float samples. Returns seconds played."""
    sd = _sd()
    try:
        resolved = _resolve_device(device)
    except LinkError:
        raise
    except Exception as exc:
        raise LinkError(f"audio device lookup failed: {exc}") from exc
    try:
        sd.play(list(samples), samplerate=rate, device=resolved, blocking=True)
    except Exception as exc:
        raise LinkError(f"playback failed: {exc}") from exc
    return len(samples) / rate


def send_tones(frame, rate=RATE, device=None):
    """Encode a framed symbol string and play it. Returns {digits, seconds}."""
    from ..linkproto import encode_samples

    seconds = play_samples(encode_samples(frame, rate), rate, device)
    return {"digits": frame, "seconds": round(seconds, 2)}


def device_state():
    """Diagnostic result for the devices endpoint. Never raises."""
    try:
        devices = output_devices()
    except Exception as exc:  # noqa: BLE001 - diagnostics report, never raise
        return DriverResult(ok=False, error=str(exc))
    if not devices:
        try:
            _sd()
        except LinkError as exc:
            return DriverResult(ok=False, state={"devices": []}, error=str(exc))
        return DriverResult(
            ok=True,
            state={
                "devices": [],
                "note": "backend present but no outputs visible (service accounts often see none)",
            },
        )
    return DriverResult(ok=True, state={"devices": devices})


def input_state():
    """Diagnostic result for the inputs endpoint. Never raises."""
    try:
        devices = input_devices()
    except Exception as exc:  # noqa: BLE001 - diagnostics report, never raise
        return DriverResult(ok=False, error=str(exc))
    if not devices:
        try:
            _sd()
        except LinkError as exc:
            return DriverResult(ok=False, state={"devices": []}, error=str(exc))
        return DriverResult(
            ok=True,
            state={
                "devices": [],
                "note": "backend present but no inputs visible (service accounts often see none)",
            },
        )
    return DriverResult(ok=True, state={"devices": devices})
