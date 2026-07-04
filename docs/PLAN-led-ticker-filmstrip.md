# LED ticker: fixed grey grid + scrolling green "filmstrip" (kill the flicker)
Planning date: 2026-07-04
Status: PLAN ONLY (no code changes yet)
Sources: hidock-mic-trigger/Sources/Views/LEDMatrixView.swift (Canvas draw,
offColor band-ghosting, fixedCols/background params), Views/MeetingHeatmapView.swift
(ledPanel ~210, ledBackground ~244), LEDMatrix.swift (track/mode/scroll engine).

## Problem
The ticker still flickers. Two causes in the current implementation:
1. **Band-ghosting opacity flip.** `offColor` dims the Tue–Sat day-colours to
   `0.30` opacity *only while a message is showing* (`textShowing && inTextBand`).
   So the whole 5-row band **flashes darker when a message starts and brightens
   when it ends** — a visible flicker every message cycle.
2. **Everything is redrawn every step.** The grey/unlit dots are recomputed and
   repainted each column step alongside the lit ones, so any timing jitter or
   opacity change reads as the *background* flickering, not just the text.

## The model you described (target behaviour)
Keep a **fixed grey dot grid** that never moves or changes. The **coloured
(green) content — the heatmap contribution squares AND the ticker text — is a
single horizontal "filmstrip" that scrolls left**: as a message comes in from the
right it pushes the heatmap leftward; when it's done, the heatmap slides back.
Grey dots stay put; only which dots are *green* changes. No band-ghosting, no
background flicker.

## Design
### One fixed backdrop + one scrolling lit layer
- **Backdrop (drawn once per frame, always identical):** every grid cell gets its
  dim "off" dot at a fixed position. This layer is **independent of scroll/mode**
  — it never dims or flips, which removes flicker cause #1.
- **Lit layer (scrolls):** a **filmstrip** of columns built from:
  `[heatmap columns] + [spacer] + [message glyph columns] + [spacer]`, where each
  filmstrip column carries only its *lit* dots (green heatmap levels in rows 0–6;
  green/coloured text in the Tue–Sat band rows 1–5). The view samples the
  filmstrip at an integer scroll offset and paints only lit dots on top of the
  backdrop. Unlit filmstrip cells paint nothing (backdrop shows through).

### Motion (home = heatmap)
- **At rest:** offset positions the filmstrip so the **heatmap fills the
  viewport** (right-aligned, matching today's scrolled-to-trailing heatmap).
  Pixel-identical to the heatmap — toggling LED on/off moves nothing.
- **On event:** animate the offset so the heatmap scrolls left and the message
  enters from the right, plays through, then the offset returns to the rest
  position (heatmap home). "Reveal the message, then return" — recommended over
  an endless loop so the heatmap is the resting state.
- Grey backdrop never scrolls; only the lit-layer sample offset changes → "green
  moves, grey stays."

### Crispness / smoothness
- Integer-column stepping of the lit layer over the fixed grey grid is inherently
  flicker-free now (the earlier flicker was the ghost flip, not the stepping).
- If smoother motion is wanted, the lit layer *can* sub-pixel-scroll over the
  fixed grey grid — but since heatmap squares are day-quantised, integer-column
  stepping reads correctly and is simplest. Keep the step-rate `TimelineView`
  redraw (cheap, sharp).

### What changes vs current code
- **Delete the `offColor` band-ghost** (the `0.30` dim of the text band). The
  backdrop grey is unconditional and constant.
- **Build a filmstrip** in `LEDMatrix` (or the view) = heatmap columns +
  message columns, instead of `track` being message-only and the heatmap being a
  separate `background` array. The heatmap becomes the head of the same
  scrolling buffer, so it genuinely scrolls (rather than being a static ghost the
  text is stamped over).
- `LEDMatrix` gains a "home offset" (heatmap width) and an event-driven scroll
  target; `mode`/`trackStart` drive the offset animation; on completion it
  returns to home.
- `MeetingHeatmapView.ledBackground` (day colours) still supplies the heatmap
  portion — but as the **lit heatmap columns of the filmstrip**, not as ghosted
  unlit dots.

## Files to touch (when implemented)
- LEDMatrix.swift — filmstrip buffer (heatmap head + message tail), home/offset
  + reveal-and-return animation state.
- Views/LEDMatrixView.swift — draw fixed grey backdrop unconditionally; paint lit
  filmstrip on top at the scroll offset; remove `offColor` ghosting.
- Views/MeetingHeatmapView.swift — pass heatmap day-colour columns as the lit
  heatmap head of the filmstrip (adapt `ledBackground`/`ledPanel`).

## Open questions
- Reveal-and-return vs continuous loop of `[heatmap][message]` — recommend
  reveal-and-return (heatmap is home). Confirm.
- When multiple events queue, scroll through them back-to-back before returning
  home, or return home between each? (Lean: play the queue, then return.)
- Colour of ticker text vs heatmap green in the same green family — keep
  per-event colours for messages (blue/amber/red) so they're distinct from the
  heatmap's green, or force all ticker text green for a uniform panel? Confirm.
