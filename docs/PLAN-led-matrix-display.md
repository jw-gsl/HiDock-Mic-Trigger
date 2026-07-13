# Heatmap grid as an LED-matrix scrolling display
Research date: 2026-06-26
Sources: hidock-mic-trigger/Sources/Views/MeetingHeatmapView.swift (7×53 grid),
AppDelegate.swift (download/transcription/summarise/mic-trigger event points),
HiDockViewModel.swift (meetingActivityByDay, syncStatus), the new agent-event
stream (docs/PLAN-formatted-cli-view.md). Windows: Windows-App/ (PyQt6).

## The idea
Repurpose the meeting-activity heatmap grid as a retro **LED dot-matrix display**
that can scroll pixel text and play simple animations in response to app events
(new download, transcription, summarise, mic-trigger active, errors, …) and as an
ambient status/stats ticker when idle.

## Why the grid fits this perfectly
The heatmap is already a **7 rows × ~53 columns** array of small square cells
(`cell = 11pt`, `gap = 3pt`, `weeksBack = 52` → 53 columns), coloured by
intensity level 0–4. That is *exactly* a classic LED dot-matrix:
- **7 pixels tall** is the canonical height of 5×7 dot-matrix fonts — every
  letter/digit fits with no redesign of the grid geometry.
- **~53 columns** is a usable marquee viewport (~8–9 characters at 5px + 1 gap),
  enough to scroll messages right-to-left.
- The existing **0–4 intensity levels** map straight onto LED brightness; colour
  can be swapped per event (green/blue/amber/red) using the same fill helper.

So the LED mode is a *reskin + animation driver* over the same cell grid — minimal
new layout, maximal reuse.

## Use cases — what it could show
Deliberately broad (trim later); grouped by intent.

### Event tickers (transient, auto-revert)
- **New download**: `↓ 3 NEW` or scroll the filename `Rec03.hda`.
- **Download progress**: a filling bar across row(s), or `42%` (drive from
  `syncDownloadProgress` / `currentlyDownloadingName`).
- **Transcription**: `TRANSCRIBING…` on start, `✓ Rec03` on finish, `✗ FAILED`
  on error (from the transcription queue + completion).
- **Summarise**: `SUMMARISING`, then the classified type `1:1 MEETING` / area
  (drive from the new agent `stage` events + `done`).
- **Mic-trigger active**: blinking `● REC` / pulsing red while ffmpeg holds the
  HiDock input (`hidockRecordingActive`).
- **Device connect/disconnect**: `H1 CONNECTED`, `PLAUD ✗`.
- **Sync complete**: `SYNC ✓ 12`.
- **Errors**: red `✗` + short reason.

### Ambient status / stats (idle loop)
- Clock `HH:MM`.
- Today's meeting count / **streak** (`5 DAY STREAK`) from `meetingActivityByDay`.
- Transcription/summarise **queue depth** (`Q:3`).
- Rolling **cost ticker** from agent `usage` events (`$0.13 today`).
- "Now processing" scroll of the current file name.
- Backlog nudge (`4 UNTRANSCRIBED`).

### Fun / visual
- **Boot animation** on launch (logo sweep / pixel wipe).
- **VU meter** driven by live mic level while recording (real dot-matrix vibe).
- **Knight-rider** sweep or spinner while busy.
- **Milestone celebration** (100th meeting → fireworks/confetti pixels).

### Reuse of the new agent-event stream
The formatted-CLI feature already emits `stage` / `tool` / `usage` / `done`
events. Piping those to the LED ticker gives "CLASSIFYING → WRITING SUMMARY → ✓"
for free — one event source feeds both the CLI pane and the LED strip.

## Architecture (macOS)
- **`LEDFont.swift`** — 5×7 bitmap glyphs for A–Z, 0–9, space, punctuation,
  a few icons (arrows, check, cross, dot). Each glyph = 5 column bytes (7 bits).
- **`LEDMatrix.swift`** — `ObservableObject` model:
  - frame buffer `[7][viewportCols]` of intensity (0–4) + per-cell colour.
  - a **message queue** with `enqueue(text:, colour:, priority:, holdMs:)`.
  - a scroll/animation engine ticking ~15–20 fps (Timer) shifting columns left.
  - pluggable **animations** (bar, sweep, VU, blink) behind a small protocol.
  - `notify(_ event: LEDEvent)` convenience API for call sites.
- **`LEDMatrixView.swift`** — renders the buffer. **Use a single SwiftUI
  `Canvas`** (one `GraphicsContext` draw) rather than 371 individual shapes —
  371 cells × 20 fps as separate views would be heavy. Sized to match the
  heatmap cell/gap so it visually replaces the grid in-place.
- **Event bus** — a thin `LEDEvent` enum; existing AppDelegate points call
  `viewModel.ledMatrix.notify(.downloadComplete(3))` etc. Hook points already
  exist: download-new completion, transcription queue transitions, `summarising`
  set changes, mic-trigger "healthy/active" signal, `syncStatus`.

### How it coexists with the heatmap
Recommended: **toggle + auto-takeover with revert.**
- A small switch in the heatmap header flips Heatmap ↔ LED.
- Even in Heatmap mode, a high-priority event can briefly *take over* the grid to
  scroll its message, then revert to the heatmap (configurable, default on).
- Alternative considered: a permanent thin LED strip *above* the heatmap — more
  screen cost; rejected for v1 in favour of reusing the same real estate.

### Settings
Enable/disable, brightness ceiling, auto-revert delay, idle-ticker on/off, and
per-event-class toggles (so e.g. mic `REC` can be disabled).

## Windows parity (PyQt6) — required by CLAUDE.md
Mirror with a `QWidget` that paints the grid in `paintEvent` (QPainter), sharing
the same 5×7 font table + scroll engine in Python. The event hook points exist in
the Windows app's sync/transcription flow. Update `PARITY.md`.

## Risks / considerations
- **Performance**: redraw 7×53 at ~20fps → use `Canvas`/`drawingGroup`, not 371
  live views. Pause the timer when nothing is animating (idle clock ticks at 1fps).
- **Distraction**: opt-in, auto-revert, per-event toggles; never steal focus.
- **Accessibility**: the same info already exists textually (`syncStatus`, CLI
  pane) — the LED is decorative/augmenting, not the only channel.
- **Readability**: 53 cols ≈ 8 chars; long messages must scroll, not truncate.

## Planned (build order — after the formatted-CLI feature ships on both platforms)
- [ ] `LEDFont` 5×7 glyph table (+ a few icon glyphs).
- [ ] `LEDMatrix` model: frame buffer + message queue + scroll engine (Canvas).
- [ ] `LEDMatrixView` sized to the heatmap; Heatmap ↔ LED toggle in the header.
- [ ] `LEDEvent` bus + wire the existing AppDelegate event points.
- [ ] Auto-takeover-with-revert behaviour + settings.
- [ ] Feed agent `stage`/`done` events into the ticker.
- [ ] Ambient idle loop (clock / streak / queue / cost).
- [ ] Optional: live mic-level VU while recording.
- [ ] Windows PyQt6 parity + PARITY.md.

## Open questions
- Default mode: heatmap with event takeover (recommended) vs LED-first?
- Monochrome green (authentic) vs per-event colour (more informative)? Lean
  per-event colour with a green idle.
- Should the LED live only in the main window heatmap, or also the menu-bar
  popover (smaller matrix)?
