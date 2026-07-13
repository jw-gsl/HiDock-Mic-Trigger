# LED ticker: fixed dot grid, real LED-sign behaviour
Planning date: 2026-07-04 · Tightened: 2026-07-09
Status: PLAN ONLY (no code changes yet)
Sources: Views/LEDMatrixView.swift (Canvas draw, offColor ghost, background param),
Views/MeetingHeatmapView.swift (ledPanel, ledBackground), LEDMatrix.swift
(track/mode/scroll engine).

## The model (corrected 2026-07-09 — this supersedes everything below the line)
Behave like a **real LED sign**:
- The **dots never move.** The grid is fixed. Motion is an *illusion* created by
  turning fixed dots **on/off** as content scrolls **right → left**. There is no
  position animation, no overlay, no ghosting, no sub-pixel — those concepts do
  not apply.
- There is **one layer of content** — a virtual horizontal "filmstrip" of
  columns = `[heatmap columns] + [message columns]`. Each frame, every fixed
  grid dot is set from the filmstrip sampled at an advancing integer offset. A
  dot is either **off** (uniform dim grey, always the same) or **on** (a colour).
- **At rest** the filmstrip is positioned so the viewport shows the **heatmap**
  (identical to the normal heatmap). On an event, the offset advances so content
  moves right→left: the heatmap dots wink off column-by-column (appear to slide
  off the left), the **message winks on from the right in the middle rows**, it
  travels left across, then the **heatmap winks back in from the right** until it
  fills the viewport again (back to rest).
- **Colours:**
  - Heatmap dots keep their green **intensity gradient** (light→dark = meeting
    volume — real data).
  - Message dots are **brightest (full) green**.
  - **Red** for the REC blink and for error messages. No other per-event
    colours (blue/amber dropped).

### Why this is simpler than the current code
The `draw()` primitive is already right in spirit: it paints dots at fixed
positions (`x = v*pitch`) and decides lit/off by sampling `track[v+offset]`. What's
wrong is (a) the heatmap is a *separate static backdrop* (`background`) with the
message stamped on top, and (b) `offColor` **dims the day colour in the text band
while a message shows** — that ghost/flip is the "overlay" and the flicker. Fix =
make the heatmap part of the same filmstrip and delete the ghost.

## Design (tightened)
### Fixed grid, two dot states
- **Off dot:** one constant dim grey, everywhere, every frame. Never dims/flips.
- **On dot:** colour + brightness from the filmstrip cell (heatmap intensity
  green / brightest green message / red).

### The filmstrip (one content buffer, sampled onto the grid)
- Columns 0–6 rows tall (Mon–Sun). Heatmap columns fill all 7 rows at their day
  intensity; message columns light only the middle 5 rows (Tue–Sat) in bright
  green (Mon/Sun rows off for message columns).
- Order (in right→left scroll terms): `[heatmap][gap][message][gap][heatmap]`.
  Rest = viewport over a heatmap section. One event = advance the offset through
  the message and land back on a heatmap section (identical, so it reads as
  "heatmap → message → heatmap"). Multiple queued messages: chain
  `[heatmap][msg1][msg2]…[heatmap]` and scroll through once, then rest.
- **Integer-column stepping** at the scroll-speed rate (this IS how an LED sign
  moves — one dot-column per step). No fractional offset.

### Rendering
- Single `Canvas`, redrawn by `TimelineView` at the column-step rate (keep the
  current cheap step-rate redraw; drop to a paused state at rest so idle CPU ≈ 0
  — a static heatmap doesn't need re-drawing).
- For each fixed cell (v, row): `cell = filmstrip[v + offset]`; if that cell's
  (row) is lit → fill with its colour·brightness, else fill the constant grey.

## What changes vs current code
- **Delete `offColor` ghosting and the separate `background` param.** Off dots
  are a constant grey; the heatmap is no longer a backdrop.
- **Build one filmstrip** in `LEDMatrix`: heatmap columns (from the day-colour
  data MeetingHeatmapView already computes) + message columns, as a single
  `[LEDColumn]` where each column carries per-row colour. Sample it in `draw()`.
- **Rest state = heatmap section of the filmstrip**, not "message-only track +
  static heatmap behind."
- **Colours:** message columns = brightest green; heatmap columns = intensity
  green; REC/error = red. Remove blue/amber.
- **Idle:** when parked on the heatmap with nothing queued, stop the timeline
  (no redraws) — the fixed heatmap is static, matching the perf work already done
  elsewhere.

## Files to touch (when implemented)
- LEDMatrix.swift — build the unified filmstrip (heatmap head + message +
  heatmap tail); right→left integer offset; rest-on-heatmap; queue handling;
  colours (green gradient / bright green / red).
- Views/LEDMatrixView.swift — remove `offColor`/`background` ghost; draw fixed
  grey off-dots + on-dots sampled from the filmstrip; pause when parked at rest.
- Views/MeetingHeatmapView.swift — feed the heatmap day-colour columns into the
  filmstrip (not as a ghost backdrop). Keep month + weekday labels around it.

## Decisions (2026-07-09)
- **Dots are fixed; motion = on/off only.** No position animation / overlay /
  sub-pixel. Right→left.
- **Colours:** heatmap intensity-green, message brightest green, REC/errors red;
  drop blue/amber.
- Heatmap is the rest state; one event scrolls message through and returns to
  heatmap.

## Open questions
- Message travel: scroll the message fully across the middle band right→left
  (marquee), or slide it to centre and hold briefly before continuing? (Lean:
  marquee straight through — simplest, consistent with "moves right→left".)
- Multiple queued events: chain into one pass (recommended) vs one pass each.
