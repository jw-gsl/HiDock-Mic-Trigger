# Windows parity — closing the last four deferred items
Date: 2026-06-18
Branch: `windows-parity`

## Items & approach

### 1. Per-device storage bar  (was: "blocked by extractor")
**Wrong assumption — it's client-side.** macOS `HiDockViewModel.storageSummary`
uses fixed capacity constants (H1/H1E = 32 GB, P1 = 64 GB) and `used = Σ recording
bytes`; the device doesn't report capacity. Replicate on Windows:
- In `_refresh_device_strip`, compute `storage_text` per device: match SKU from the
  name → capacity; `used = Σ recording.length`; render "used / cap GB (free)".
- `device_strip` already renders `storage_text` when non-None.

### 2. Plaud SSO (WebEngine optional)
- Uncomment `PyQt6-WebEngine` in `requirements.txt` so the WebEngine SSO is the
  default install path (manual token-paste stays as the no-WebEngine fallback).
- Confirm `plaud_signin_dialog` picks WebEngine when present.

### 3. H1e idle glyph (shared H1 line-art)
- `device_icons.device_glyph_pixmap`: for H1E idle, fall back to `H1e_recording.png`
  (the product photo, which differs from H1) instead of `H1_glyph.svg`, so H1e is
  visually distinct from H1 even when idle. (Recording-state photos already differ.)

### 4. HiDock cache-paint-before-probe
- Add a `cached-status` command to `Windows-Script/extractor.py` that reports
  recordings from `state.json` (downloads) WITHOUT a USB probe (mirrors
  `plaud-cached-status`). Reuse `load_state`/`resolved_output_dir`.
- On launch, `_paint_cached_on_launch` runs `cached-status` for paired HiDock/volume
  devices and paints instantly, before the live `status` probe.

### 5. Merge expandable parent/child rows
- At merge time (`_merge_selected`), write a manifest `~/HiDock/merge_groups.json`:
  `{ merged_mp3_path: [piece_mp3_paths...] }`.
- `recording_model`: add `set_merge_groups(groups)`. Render a merged recording as a
  PARENT row with a ▸/▾ toggle in the name; expanding inserts its child piece rows
  (indented). Flat-model implementation via a computed display list + expand-state.
- `main_window`: load the manifest, feed the model, connect table clicks on the
  toggle to expand/collapse.

### 6. Toolbar consolidation (cosmetic)
- Light touch only: make the download buttons selection-aware — relabel
  "Download Selected" ↔ context, keep "Download New". Do NOT remove functionality;
  the macOS "drop Download New" choice is subjective and Windows users rely on it.
  Net: tighten labels/tooltips; no behavioural regression.

## Execution
- Subagent: merge expandable rows in `recording_model.py` (self-contained, hardest).
- Me: storage bar, cached-status command + launch wiring, Plaud reqs, H1e glyph,
  toolbar tidy, and merge-manifest write/wiring in `main_window.py`.
- Verify after each: import (offscreen Qt) + ruff + pytest (Windows-App + usb-extractor).
