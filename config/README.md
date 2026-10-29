# Config layout

- `profiles/` — shared model profiles (generic fixture specs, public-safe).
- `studios/` — tracked **example** studios only (fictional data for tests/demos).
- `studios.local/` — your real studios. **Git-ignored, never committed.**
  Same file layout as `studios/`: `studio.json`, `devices.json`, `zones.json`,
  `modes.json`, `presets.json`.

The loader reads both studio roots; a duplicate studio id across roots is an
error. Keep personal hardware details in `studios.local/` only.
