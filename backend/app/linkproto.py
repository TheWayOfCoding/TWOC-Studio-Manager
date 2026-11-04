# SPDX-License-Identifier: AGPL-3.0-or-later
"""Audio-link protocol: DTMF commands over a wire between PCs.

The hub plays short framed tone bursts out of one audio device; a listener
on the main PC decodes them off a mic input and runs allow-listed local
actions. The primary path is hub TX -> main RX (one cable). A second cable
(main TX -> hub RX) carries optional reverse acks in the SAME framing —
same alphabet, same check digit, decoded by the same functions.

Stdlib only (math/wave/struct): both ends and the tests share this file
with no extra installs beyond sounddevice for raw audio I/O.
"""

import math
import struct
import wave
from io import BytesIO

ROWS = [697, 770, 852, 941]
COLS = [1209, 1336, 1477, 1633]
SYMBOLS = ("1", "2", "3", "A", "4", "5", "6", "B", "7", "8", "9", "C", "*", "0", "#", "D")
DATA_SYMBOLS = "0123456789ABCD"
PREAMBLE = "#"

# Command space (two data digits each; unknown-but-valid codes decode
# fine and each end ignores what it doesn't recognize). The alphabet is
# 14 symbols, so the two-digit space holds 196 codes -- five used today
# leaves ample room; new commands just take the next free code. Hub TX ->
# main RX: ping (loop test), obs (launch OBS), shutdown (power the
# workstation down). Main TX -> hub RX (reverse acks, heard via
# POST /api/link/listen): ack (command received), obs_ready (OBS
# websocket up), obs_fail (launch attempted, websocket never came).
COMMANDS = {"ping": "00", "obs": "01", "ack": "02", "obs_ready": "03", "obs_fail": "04", "shutdown": "05"}

PRE_MS, GAP_MS, TONE_MS, INTER_MS = 200, 100, 120, 80
AMPLITUDE = 0.35


def _symbol_freqs(symbol):
    index = SYMBOLS.index(symbol)
    return ROWS[index // 4], COLS[index % 4]


def _check_digits(digits):
    total = sum(DATA_SYMBOLS.index(d) for d in digits)
    return DATA_SYMBOLS[total % len(DATA_SYMBOLS)]


def frame_for(command):
    """Framed symbol string for a command name. Raises KeyError/ValueError."""
    digits = COMMANDS[command]
    if len(digits) != 2 or any(d not in DATA_SYMBOLS for d in digits):
        raise ValueError(f"command needs two data digits: {command!r}")
    return PREAMBLE + digits + _check_digits(digits)


def _tone(freq, n, rate):
    return [math.sin(2 * math.pi * freq * i / rate) for i in range(n)]


def encode_samples(frame, rate=44100):
    """Mono float samples for a framed symbol string."""
    out = []
    spans = []
    for pos, symbol in enumerate(frame):
        row, col = _symbol_freqs(symbol)
        ms = PRE_MS if pos == 0 else TONE_MS
        n = int(rate * ms / 1000)
        mixed = _tone(row, n, rate)
        high = _tone(col, n, rate)
        spans.append([AMPLITUDE * (a + b) / 2 for a, b in zip(mixed, high)])
        gap_ms = GAP_MS if pos == 0 else (INTER_MS if pos < len(frame) - 1 else 0)
        spans.append([0.0] * int(rate * gap_ms / 1000))
    for span in spans:
        out.extend(span)
    return out


def encode_wav(frame, rate=44100):
    """16-bit mono WAV bytes for a framed symbol string."""
    samples = encode_samples(frame, rate)
    pcm = struct.pack("<%dh" % len(samples), *[int(max(-1.0, min(1.0, s)) * 32767) for s in samples])
    buf = BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm)
    return buf.getvalue()


def _goertzel(samples, rate, freq):
    step = 2 * math.pi * freq / rate
    cosine, sine = math.cos(step), math.sin(step)
    coeff = 2 * cosine
    first, second = 0.0, 0.0
    for sample in samples:
        next_value = sample + coeff * first - second
        second, first = first, next_value
    real = first * cosine - second
    imag = first * sine
    return real * real + imag * imag


def _detect_symbol(segment, rate):
    row_power = [_goertzel(segment, rate, f) for f in ROWS]
    col_power = [_goertzel(segment, rate, f) for f in COLS]
    row = max(range(4), key=row_power.__getitem__)
    col = max(range(4), key=col_power.__getitem__)
    if row_power[row] <= 0 or col_power[col] <= 0:
        return None
    row_sorted = sorted(row_power, reverse=True)
    col_sorted = sorted(col_power, reverse=True)
    if row_sorted[0] < 4 * row_sorted[1] or col_sorted[0] < 4 * col_sorted[1]:
        return None
    return SYMBOLS[row * 4 + col]


def _segments(samples, rate):
    hop = max(1, int(rate * 0.02))
    peak = max((abs(s) for s in samples), default=0.0)
    floor = max(0.03, peak * 0.15)
    loud = []
    for start in range(0, len(samples), hop):
        chunk = samples[start : start + hop]
        rms = math.sqrt(sum(s * s for s in chunk) / len(chunk)) if chunk else 0.0
        loud.append(rms >= floor)
    segments, begin = [], None
    quiet_run = 0
    for index, is_loud in enumerate(loud + [False]):
        if is_loud:
            if begin is None:
                begin = index
            quiet_run = 0
        elif begin is not None:
            quiet_run += 1
            if quiet_run * hop >= rate * 0.06 or index == len(loud):
                segments.append((begin * hop, index * hop))
                begin, quiet_run = None, 0
    return [samples[a:b] for a, b in segments if b - a >= rate * 0.07]


def _symbols_for(samples, rate):
    symbols = []
    for segment in _segments(list(samples), rate):
        symbol = _detect_symbol(segment, rate)
        if symbol is not None:
            symbols.append(symbol)
    return symbols


def decode_samples(samples, rate=44100):
    """Decode a framed command from mono float samples. None when absent."""
    if not samples:
        return None
    symbols = _symbols_for(samples, rate)
    if PREAMBLE not in symbols:
        return None
    tail = symbols[symbols.index(PREAMBLE) + 1 :]
    if len(tail) < 3:
        return None
    digits, check = tail[:2], tail[2]
    if any(d not in DATA_SYMBOLS for d in digits) or check not in DATA_SYMBOLS:
        return None
    if _check_digits(digits) != check:
        return None
    return "".join(digits)


def decode_all_samples(samples, rate=44100):
    """Every framed digit-string in a buffer, in order (multi-burst windows).

    Long listen windows can hold several bursts (e.g. ack then obs_ready);
    each preamble-delimited frame decodes independently, corrupt frames
    drop out, unknown-but-valid codes decode fine for the caller to filter.
    """
    if not samples:
        return []
    symbols = _symbols_for(samples, rate)
    frames, idx = [], 0
    while idx < len(symbols):
        try:
            start = symbols.index(PREAMBLE, idx)
        except ValueError:
            break
        idx = start + 1
        tail = symbols[start + 1 : start + 4]
        if len(tail) < 3:
            continue
        digits, check = tail[:2], tail[2]
        if any(d not in DATA_SYMBOLS for d in digits) or check not in DATA_SYMBOLS:
            continue
        if _check_digits(digits) != check:
            continue
        frames.append("".join(digits))
    return frames


def decode_command(samples, rate=44100):
    """Decode samples to a command NAME, or None (unknown codes stay None)."""
    digits = decode_samples(samples, rate)
    if digits is None:
        return None
    for name, code in COMMANDS.items():
        if code == digits:
            return name
    return None


def decode_all_commands(samples, rate=44100):
    """decode_all_samples mapped to command NAMEs (unknown codes dropped)."""
    names = []
    for digits in decode_all_samples(samples, rate):
        for name, code in COMMANDS.items():
            if code == digits:
                names.append(name)
                break
    return names
