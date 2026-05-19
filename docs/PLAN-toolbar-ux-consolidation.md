# Toolbar UX Consolidation — Pipeline-Aware Action Model

Research date: 2026-04-20
Branch: `feature/voice-training`

## Context: what the user said

> "I think there's now too many buttons, and really there's three steps to a process. How could we use the UX in a much better way to do that?"

Adding Summarize Selected + Summarize All on top of the existing button set would push the toolbar past the point of comprehension. The three pipeline stages (Download → Transcribe → Summarize) should be modelled as one action with state, not six.

## Current state (count the buttons)

The recording list today has **eleven** actions clustered across two rows:

**Header row (`SyncHeaderSection.swift`):**
- Download Selected
- Download New
- Skip
- Unskip (conditional)

**Toolbar row (`SyncToolbarSection.swift`):**
- Pair / Unpair
- Recordings folder / Transcripts folder
- Refresh
- Transcribe Selected
- Transcribe All
- Merge
- Trim
- Speaker Labels toggle
- Queue button (conditional)

**Toggles row:**
- Select All / None / New
- Device filter buttons
- Hide Downloaded
- Auto-download
- Auto-transcribe

If we add Summarize Selected + Summarize All we hit **thirteen actions**, and we still wouldn't have a sensible way to express "download + transcribe + summarize this in one click" — which is 95% of what the user actually wants.

## The insight: it's a pipeline, not six commands

Every recording has **one state** along a single axis:

```
[On device] → [Downloaded] → [Transcribed] → [Summarized]
                    ↑
                 [Skipped] (off-axis, user-initiated)
```

The user's intent for a given selection is almost always: *move it to the end of the pipeline.* The granular "download only" / "transcribe only" operations are edge cases used for debugging or re-running a single stage.

So the UI should lead with **"move this through the pipeline"** and relegate granular stage control to a secondary menu.

## Proposal

### Primary action: a single split button — "Process"

```
┌─────────────────────────┐ ┌───┐
│  Process 3 Selected     │ │ ▾ │
└─────────────────────────┘ └───┘
```

- **Main click**: runs every missing stage on the selection, in order. If item is on device → download + transcribe + summarize. If item is downloaded → transcribe + summarize. If already transcribed → summarize only.
- **Label updates smartly**:
  - No selection → "Process" (disabled)
  - Mixed states → "Process 3 Selected"
  - Tooltip breakdown: "Will download 1, transcribe 2, summarize 3"
- **Dropdown (▾) exposes stage control** for edge cases:
  - Download only
  - Transcribe only (requires downloaded)
  - Summarize only (requires transcribed)
  - Re-transcribe (force re-run, overwrites existing)
  - Re-summarize (force re-run)

### Secondary action: "Process All New" split button

Replaces both **Download New** and the need for a separate "transcribe everything untranscribed" action.

- Runs the full pipeline on every row that is not yet at the final state (and not skipped)
- Dropdown exposes the same stage-specific variants

### Deprecated buttons (remove)

- Download Selected → absorbed into Process
- Download New → absorbed into Process All New
- Transcribe Selected → absorbed into Process
- Transcribe All → absorbed into Process All New
- Auto-download + Auto-transcribe toggles → collapse into one "Auto-process new recordings" toggle

### Kept as-is (distinct operations, not on the pipeline)

- Merge (combines multiple recordings — distinct operation)
- Trim (edits a single recording — distinct operation)
- Skip / Unskip → moved into row right-click menu only (already there, remove from toolbar)
- Pair / Unpair / folder pickers / Refresh (device-level, not recording-level)
- Queue button (status, not action)

### Settings relocation

Moved out of toolbar into Settings panel:
- Speaker Labels (diarize) toggle — rarely changed, belongs in settings
- Auto-process new recordings toggle (the consolidated one)

### Status column tells the story

Each row's status badge already shows current state ("On device", "Downloaded", "Transcribed", "Skipped"). Add one more state: **"Summarized"** (green, final state). The status becomes the reading surface; the Process button is the writing surface.

### Before / After count

| | Before | After |
|---|---|---|
| Pipeline action buttons | 6 (Download Selected/New, Transcribe Selected/All, + proposed Summarize Selected/All) | 2 (Process, Process All New — both split buttons) |
| Auto toggles | 2 (Auto-download, Auto-transcribe) + proposed Auto-summarize | 1 (Auto-process new recordings) |
| Visible toolbar widgets | ~11 | ~6 |

## Interaction examples

**Case 1: user connects HiDock, sees 5 new recordings, wants them all transcribed + summarized.**
- Click **Process All New** → full pipeline runs, progress bar shows "Downloading 5 → Transcribing 5 → Summarizing 5".

**Case 2: user already has recordings downloaded, wants summaries on three specific ones.**
- Select 3 rows → **Process 3 Selected** button reads "Transcribe & Summarize 3 Selected" (smart label).
- Click → transcribe pass, then summarize pass.

**Case 3: user wants to re-run summarization because they changed the prompt.**
- Select rows → dropdown → **Re-summarize** → runs summarization only, overwrites existing.

**Case 4: user wants to download but not transcribe yet (rare).**
- Select rows → dropdown → **Download only**.

## Implementation sketch

1. Add new enum `PipelineAction { .full, .downloadOnly, .transcribeOnly, .summarizeOnly, .resummarize, .retranscribe }`.
2. Add `HiDockViewModel.onProcess(action: PipelineAction, scope: .selected | .allNew)`.
3. `AppDelegate` routes `.full` to a chained subprocess runner that does download → transcribe → summarize, stopping at failure.
4. Replace button set in `SyncHeaderSection.swift` and `SyncToolbarSection.swift`.
5. Smart label computation: `viewModel.processButtonLabel(for: selection)` returns label + tooltip.
6. Mirror in `Windows-App/ui/main_window.py` per PARITY.md.
7. Update `PARITY.md` toolbar rows.

## Open questions

- Should the split-button dropdown also show "Skip" as a quick action for rows you don't want to process? (Counter-argument: Skip is already on right-click, keeping it there keeps the dropdown focused on stages.)
- When "Process All New" is running and user selects rows, should the button switch to acting on selection? Probably yes — show two buttons side by side only when both have meaningful scope.
- Progress bar granularity: one bar per stage or one end-to-end bar? Suggest: one end-to-end bar with stage-name label ("Transcribing 2 of 3…").

## Rejected alternatives

- **Per-row inline action menu only (no toolbar buttons)**: loses batch affordance. Users want "do everything with one click".
- **Drag-to-stage pipeline view** (Kanban-style): too heavy, unfamiliar, doesn't fit existing list layout.
- **Keep all buttons, hide behind a single "More" overflow menu**: just moves clutter, doesn't reduce it.

## Completed

- [x] 2026-04-20 Analysed current toolbar (11 actions), identified pipeline-as-one-action insight.

## Planned

- [ ] Get user sign-off on the Process + Process All New split-button model.
- [ ] Implement PipelineAction enum + routing in macOS app.
- [ ] Implement in Windows app, update PARITY.md.
- [ ] Add "Summarized" status badge + state.
- [ ] Remove Auto-download / Auto-transcribe toggles, add single Auto-process.
- [ ] Move Speaker Labels toggle to Settings.
