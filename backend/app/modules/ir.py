# SPDX-License-Identifier: AGPL-3.0-or-later
"""IR module: capture remote codes to JSON, replay them on demand."""

import colorsys
import json
import tempfile
import time
from pathlib import Path

from .base import Module

# Fixed-exposure measured appearance of each official fixed-color label
# (AVMatrix capture at fixed 5000K/1/60/ISO100 — the canonical color spec).
# Maps hue/saturation asks to the button that actually looks closest. Keep
# in sync with strip renames: the keys ARE the official labels.
STRIP_FIXED_COLORS = {
    "amber": (28, 136, 53),
    "azure": (10, 127, 238),
    "blue": (5, 54, 237),
    "chartreuse": (48, 71, 23),
    "cyan": (14, 154, 217),
    "green": (10, 159, 66),
    "lime": (32, 105, 38),
    "magenta": (22, 45, 238),
    "mint": (13, 159, 193),
    "neon green": (27, 156, 64),
    "pink": (28, 43, 238),
    "red": (74, 16, 8),
    "royal blue": (8, 50, 238),
    "teal": (7, 103, 240),
    "violet": (14, 47, 238),
    "white": (27, 134, 231),
}


def nearest_strip_color(hue, saturation=100):
    """Official fixed-color label nearest a hue/saturation ask (RGB distance).

    Hue wraps (circular); saturation clamps 0-100. Ties break
    alphabetically so the mapping is deterministic.
    """
    hue = float(hue) % 360
    saturation = min(100, max(0, float(saturation)))
    red, green, blue = colorsys.hsv_to_rgb(hue / 360, saturation / 100, 1.0)
    target = (red * 255, green * 255, blue * 255)
    best, best_d2 = None, None
    for label in sorted(STRIP_FIXED_COLORS):
        cr, cg, cb = STRIP_FIXED_COLORS[label]
        d2 = (target[0] - cr) ** 2 + (target[1] - cg) ** 2 + (target[2] - cb) ** 2
        if best_d2 is None or d2 < best_d2:
            best, best_d2 = label, d2
    return best


class IrModuleError(Exception):
    """Capture/replay failure (nothing heard, ambiguous press, unknown label)."""


class IrCodeStore:
    """JSON map of label -> 3-byte code. Atomic writes, missing file reads {}."""

    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(data, dict):
            raise IrModuleError(f"code store is not an object: {self.path}")
        return data

    def save_label(self, label, code):
        code = list(code)
        if len(code) != 3 or any(not isinstance(b, int) or not 0 <= b <= 255 for b in code):
            raise IrModuleError("code must be 3 byte ints")
        data = self.load()
        existing = data.get(label)
        if isinstance(existing, dict):
            entry = {"code": code, "works": existing.get("works"), "notes": existing.get("notes", "")}
            for key, value in existing.items():
                entry.setdefault(key, value)
        else:
            entry = {"code": code, "works": None, "notes": ""}
        data[label] = entry
        self._write(data)
        return data

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.NamedTemporaryFile(
            "w", dir=str(self.path.parent), delete=False, encoding="utf-8"
        )
        try:
            json.dump(data, tmp, indent=2, sort_keys=True)
            tmp.write("\n")
            tmp.close()
            Path(tmp.name).replace(self.path)
        except BaseException:
            try:
                Path(tmp.name).unlink()
            except OSError:
                pass
            raise

    def get(self, label):
        try:
            entry = self.load()[label]
        except KeyError as exc:
            raise IrModuleError(f"unknown IR label: {label}") from exc
        code = entry.get("code") if isinstance(entry, dict) else entry
        if (
            not isinstance(code, list)
            or len(code) != 3
            or any(not isinstance(b, int) or not 0 <= b <= 255 for b in code)
        ):
            raise IrModuleError(f"malformed code for label: {label}")
        return code

    def mark(self, label, works=None, notes=None):
        """Record replay effectiveness for a captured label."""
        data = self.load()
        if label not in data:
            raise IrModuleError(f"unknown IR label: {label}")
        entry = data[label]
        old = entry if isinstance(entry, dict) else {}
        merged = {"code": self.get(label)}
        merged["works"] = bool(works) if works is not None else old.get("works")
        merged["notes"] = notes if notes is not None else old.get("notes", "")
        for key, value in old.items():
            merged.setdefault(key, value)
        data[label] = merged
        self._write(data)
        return merged


class IrModule(Module):
    """IR capture/replay over USB-serial (GPIO transport later)."""

    def __init__(self, name, drivers, store):
        super().__init__(name, drivers)
        if "usb" not in self.drivers:
            raise IrModuleError("IR module needs a 'usb' driver")
        self.store = store if isinstance(store, IrCodeStore) else IrCodeStore(store)

    def describe(self):
        return {"module": self.name, "transports": sorted(self.drivers), "codes": len(self.store.load())}

    def listen(self, seconds=5.0):
        return self.drivers["usb"].listen(seconds)

    def capture(self, label, seconds=8.0):
        """Save the single code heard in the window. Press the remote once."""
        heard = self.listen(seconds)
        if heard.get("error"):
            raise IrModuleError(heard["error"])
        unique = []
        for code in heard["codes"]:
            if code not in unique:
                unique.append(code)
        if not unique:
            raise IrModuleError("heard nothing: aim the remote at the module and press once")
        if len(unique) > 1:
            raise IrModuleError(f"heard {len(unique)} distinct codes: press only once per capture")
        self.store.save_label(label, unique[0])
        return {"label": label, "code": unique[0]}

    def send_label(self, label, times=1, gap_s=0.3, sleep=time.sleep):
        """Send a saved label N times (e.g. brightness ramps). Last result wins."""
        return self._send_repeated(self.store.get(label), times, gap_s, sleep)

    def send_code(self, code, times=1, gap_s=0.3, sleep=time.sleep):
        return self._send_repeated(code, times, gap_s, sleep)

    def _send_repeated(self, code, times, gap_s, sleep):
        times = max(int(times), 1)
        result = None
        for i in range(times):
            result = self.drivers["usb"].apply({"send": code})
            if not result.ok or i == times - 1:
                break
            sleep(gap_s)
        return result

    def codes(self):
        return self.store.load()

    def mark(self, label, works=None, notes=None):
        return self.store.mark(label, works, notes)
