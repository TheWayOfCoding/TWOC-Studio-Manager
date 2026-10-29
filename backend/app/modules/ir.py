# SPDX-License-Identifier: AGPL-3.0-or-later
"""IR module: capture remote codes to JSON, replay them on demand."""

import json
import tempfile
from pathlib import Path

from .base import Module


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

    def send_label(self, label):
        return self.drivers["usb"].apply({"send": self.store.get(label)})

    def send_code(self, code):
        return self.drivers["usb"].apply({"send": code})

    def codes(self):
        return self.store.load()

    def mark(self, label, works=None, notes=None):
        return self.store.mark(label, works, notes)
