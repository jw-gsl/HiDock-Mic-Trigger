# Windows Parity Assessment (macOS → Windows)
Research date: 2026-06-18
Branch: `windows-parity`
Baseline: `PARITY.md` created 2026-04-05 (commit `f564ee3`), last reviewed 2026-04-22
Sources: full `git log f564ee3..main` (264 commits), `hidock-mic-trigger/Sources/` (Swift),
`Windows-App/` (PyQt6), verified by per-commit path analysis + grep against `Windows-App/`

## Methodology

Every commit since `PARITY.md` was created was classified by which app it touched:
- **111** commits touched the macOS app (`hidock-mic-trigger/`)
- **13** commits touched the Windows app (`Windows-App/`) — only **6** of those were true "both"
- **~91** commits touched macOS-only and never the Windows app

**Conclusion: Windows feature work effectively stopped in late April / May.** The last real
Windows commits were the 2026-04-09 "Add Windows parity" batch (audio playback, merge speakers,
undo, re-diarize, queue dialog) and the 2026-05-19 Sortformer diarization backend switch. The
entire June wave (Plaud, summarisation, embedded CLI) and most late-April macOS UI work
(device cards, import, model-manager rethink, voice-training overhaul, merge-candidate detection)
never crossed over.

Every "absent on Windows" claim below was grep-verified, not inferred.

## What DID land on both since April (NOT gaps)

- Merge & Trim audio (manual) — `2026-04-07`
- Transcript viewer, timestamps, speaker tagging — `2026-04-08`
- Default Speaker Labels (diarization) ON — `2026-04-08`
- Windows audio playback, merge speakers, undo, re-diarize (+`--n-speakers` on re-diarize) — `2026-04-09`
- Model size GB display — `2026-04-20`
- Storage header unreachability (scattered-row version) — `2026-04-21`
- "Removed" state, status palette, trim/merge polish, security fixes — `2026-04-25`
- NeMo Sortformer diarization + word-level alignment (shared pipeline) — `2026-05-19`

---

## Tier 1 — Whole subsystems missing on Windows (CONFIRMED IN SCOPE)

### 1. Summarisation suite — 0 references in `Windows-App/`
The largest single gap. The entire June summarise wave is macOS-only. Windows must add:
- [ ] Summarise Selected button + Auto-summarise toggle (`7df6f9a`, slice 3)
- [ ] Summary column in recordings table (absent in `recording_model.py`)
- [ ] Per-recording "Summarise" / "Ask Claude" context actions + Summarised/Summarising status (`5329f6b`)
- [ ] In-app Summary Viewer (classification header + rendered markdown) (`b0f130b`)
- [ ] Summary classification header + Reclassify dropdown + dedup on re-summarise (`0c24f60`)
- [ ] Summary Type filter (filter recordings by classification) (`9eeeb0b`)
- [ ] Summary Templates manager — import / new / iterate / reveal / delete (`8664c49`, slice 5)
- [ ] Summarisation Provider menu (Auto / Claude / Codex / Gemini / Ollama) (`a9b2c9f`)
- [ ] Typed, template-driven engine; engine auto-detect + honor `[summarization].engine` (`fdb9145`, `da6983c`, `9c2dff1`)
- [ ] "Show CLI while summarising" toggle (`afdcd71`)
- [ ] Refinements: no guidance leak, cleaner CLI stream, date injection (`6c95387`)
- Note: Windows has only the old `cowork_dialog.py` prompt-copy path (see Tier 3 #17).

### 2. Plaud cloud sync — 0 references in `Windows-App/` (CONFIRMED IN SCOPE)
Entire cloud subsystem is macOS-only. Windows must add:
- [ ] Plaud as a paired sync device + cloud backend in extractor (`925035e`, `8732c8d`)
- [ ] SSO sign-in (Google/Apple) + region auto-detect (`bcc3a50`, `4c12a76`)
- [ ] Cloud token auto-refresh + expired-session handling (`9a21a36`, `ead0815`)
- [ ] Plaud card: storage bar + file count (`4ebdd24`)
- [ ] "Sign in required" clickable chip that launches sign-in (`79c0df4`)
- [ ] Show downloaded recordings when signed out / offline (`209b7d6`)
- [ ] Paint cached/downloaded Plaud recordings on launch + concurrent HiDock+Plaud cache paint (`ebb0efc`, `96a82b8`)
- [ ] Local Plaud timestamps (`6c95387`)
- Reference: standalone `plaud-sync/` Tauri app exists — backend logic can be mirrored.

### 3. Embedded CLI / terminal — 0 references in `Windows-App/` (CONFIRMED IN SCOPE)
Two distinct macOS features, both in scope:
- [ ] Terminal… PTY pane (SwiftTerm) — interactive shell, e.g. `claude auth login` (`52c76d4`)
- [ ] Embedded CLI pane: Ask Claude Code + in-app summarise activity feed + CLI toggle (`8227d14`)
- Windows note: needs a PyQt terminal widget + PTY (`pywinpty`/ConPTY). Decide whether to embed a
  full shell or a scoped command runner.

---

## Tier 2 — Significant macOS-only features

- [ ] **Device cards UI (Phase 1+2)** — replace scattered status/storage rows with a card grid:
  state chips (Connected / Connecting / Recording / Unreachable / Not connected), per-card storage
  bar, per-device Reconnect button, per-card filter toggle, adaptive grid, hide disconnected
  volumes, Recording chip pinned to the device ffmpeg actually holds (`3036ac1`, `e8765ab`, `5ef3f4d`, `c450b01`, `24d40b9`)
- [ ] **Per-device storage summary** used / capacity / free (H1 32 GB, P1 64 GB) (`692605f`) — Windows
  only has the unreachability header
- [ ] **Model Manager rethink** — two-tier (Pipeline Stages vs Supporting Models) + per-stage backend
  picker (Whisper/Parakeet, Lite/Sortformer, VAD, TitaNet) (`e780d33`, `0a9bdaa`). Windows model
  dialog is still single-whisper-model (no stage/backend refs).
- [ ] **Voice Training window** (distinct from Voice Library list) — smart samples, per-sample
  reassign, review state, cross-meeting speaker ID, quality scores, AI name suggestion, voice-library
  auto-matching wired into diarization (`9940e2f`, `375a92a`, `1890a56`, `82b0024`)
- [ ] **Word-range / mid-segment split** (voice-training Layer 1 v2) — `WordTokensView` drag-select
- [ ] **Re-cluster from labels** (`recluster-with-anchors`, Layer 2) — Windows has re-diarize but not
  anchor-based reclustering
- [ ] **Automatic merge-candidate detection** — split-chain detection, whole-row tint, inline tick
  toggle, expandable merge groups, hide already-merged (`9b5ec52`, `df4d85c`, `1236c8e`, `7571adc`).
  Windows has only a manual Merge button.
- [ ] **Import Audio File (local browse)** — Import toolbar button, "Imported" virtual device,
  Imported status, Remove Import context menu, HiDock-convention naming, keep-visible-through-filters
  (`28944f4`, `2278622`, `d50fce4`). Windows has only volume-import via device-manager scan.
- [ ] **Transcription queue window** — `transcription_queue_dialog.py` EXISTS on Windows but is
  ORPHANED (not referenced from `main_window.py`/`app.py`). Wire it up + port the deadlock fix
  (`1b62339`) and pause/resume/cancel/reorder/progress.
- [ ] **Cache-paint on launch** — paint recordings table from cache before USB probes; instant
  startup (`920971a`, `5f3a737`). Absent on Windows.
- [ ] **Speaker-count override on initial transcribe** — "Transcribe with Speaker Count" context
  action (`49caacb`). Windows has `--n-speakers` only inside re-diarize.
- [ ] **Trigger health UX** — Waiting/Active/healthy states, wait message, live recording badge,
  restart timestamp, auto-download recovery (`f957631`, `888df75`). Windows has basic running/stopped.

---

## Tier 3 — Polish / divergence / cleanup

- [ ] **Cowork is a stale divergence** — macOS REMOVED Cowork (`9c2dff1`) in favour of summarisation,
  but Windows still wires the "✨ Cowork" footer button (`main_window.py:480`) + `cowork_dialog.py`.
  Replace with summarisation (Tier 1 #1) or remove.
- [ ] **Skip/Unskip terminology + semantics** — macOS renamed Mark Done → Skip / Unmark → Unskip and
  made Skip also opt a recording out of transcription (`0907b9f`, `d3b505c`). Windows still shows
  "Mark Done" / "Mark as Downloaded" (old behaviour).
- [ ] **Check for Firmware Updates menu + Delete Local Copy context action** (`82d0acb`) — absent on Windows
- [ ] **Transcribed-this-session badge** on app/dock icon (`28e6659`) — absent on Windows
- [ ] **Transcript viewer: speaker stats header + Copy All** (`7f3bab5`, `24a9a6d`) — absent on Windows
- [ ] **Download-complete notification (toast)** — Windows shows status-bar text only; macOS posts a
  user notification with actions
- [ ] **Per-SKU device artwork** — Windows shares the H1 asset for H1e; no H1/H1E/P1 product photos
- [ ] **Toolbar consolidation** — selection-driven verbs, dropped Download New, unified Remove button
  (`37a0676`, `32388de`) — Windows toolbar still on older layout

---

## Intentional platform differences (NOT gaps)

- Preferred/Fallback mic — CoreAudio concept, macOS only
- ASR backend: macOS uses Parakeet TDT **MLX** (Apple Silicon); Windows uses **whisper.cpp**.
  Sortformer diarization is shared. (Backend differs by OS; UX should still match.)
- Launch on login — macOS LaunchAgent; Windows configured externally
- Row selection (checkbox vs highlight), device filter (buttons vs combo), icons (SF Symbols vs
  emoji), theme (SwiftUI vs QSS)

---

## Suggested build order (biggest value first; no time estimates)

1. **Cleanup + low-risk wiring**: remove/replace Cowork; wire the orphaned queue dialog; Skip/Unskip
   rename; firmware menu + Delete Local Copy.
2. **Summarisation suite** (Tier 1 #1) — largest user-visible gap; touches toolbar, table column,
   viewer, templates, provider menu, engine config.
3. **Device cards UI + storage summary** (Tier 2) — self-contained, high visual parity.
4. **Model Manager rethink** (Tier 2) — pipeline stages + backend picker.
5. **Voice Training window + word-range split + recluster-with-anchors** (Tier 2) — transcript depth.
6. **Embedded CLI / terminal** (Tier 1 #3) — PyQt PTY widget.
7. **Plaud cloud sync** (Tier 1 #2) — largest backend lift; mirror `plaud-sync/` logic.
8. **Remaining Tier 2/3**: merge-candidate detection, import-audio-file, cache-paint on launch,
   speaker-count override, trigger health UX, badges, artwork, toasts.

## Action item

`PARITY.md` itself is stale and must be rebuilt — it predates Summarisation, embedded CLI/terminal,
Voice Training, merge-candidate detection, import-audio, the Model Manager rethink, and the entire
Plaud subsystem. Update it row-by-row as each gap above closes.
