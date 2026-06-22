# Meeting activity heatmap (GitHub-style contribution grid)

Research date: 2026-06-21
Trigger: James asked for a GitHub-profile-style contribution heatmap, but for
**meetings per day over the last year**, with a hover tooltip showing the count
plus richer metrics (speakers, action/to-do items). Placement: top-right header
space next to the device cards (H1 / P1 / Plaud).

## What GitHub's heatmap is
A grid of small rounded squares: **columns = weeks** (oldest left → newest
right, ~53 columns for a year), **rows = days of week** (Sun…Sat, 7 rows).
Each cell's **colour intensity** buckets the day's activity (none → low → high).
Hover = tooltip with the date and the count. Month labels along the top, weekday
labels down the left.

## Data — what's actually available (verified against live state.json + files)

### Primary metric: meetings/day — DENSE, reliable ✅
- Every HiDock recording has `createDate` (format **`YYYY/MM/DD`**, e.g.
  `2026/01/19`) + `createTime` (`HH:MM:SS`) — `Models.swift` HiDockSyncRecording.
- **Plaud caveat:** Plaud recordings have `createDate == nil`; their date is in
  the `name` field as `YYYY-MM-DD HH:MM:SS` (e.g. `2026-06-17 17:50:18`). Must
  parse the name for Plaud rows.
- ~569 recordings spanning ~1 year (P1 from 2025-07, H1 from 2026-01) — plenty
  of density for a meaningful heatmap.
- All of this is **already in memory** in `viewModel.syncEntries`. No file IO.

### Secondary metric: total duration/day — DENSE, reliable ✅
- Every recording has `duration: Double` (seconds). Summed per day this is a
  great always-available secondary stat ("3 meetings · 2h 14m").

### Tertiary metrics: speakers, action items — SPARSE today ⚠️
- **Speakers**: only in diarized JSON (`~/HiDock/Raw Transcripts/*_diarized.json`,
  count unique `speaker_id`) or the knowledge-graph `meeting_people` table. NOT
  exposed in the Swift layer. Requires file parsing / a new index.
- **Action items**: live in summary bodies under `## 🎯 Next Steps` (`[ ]`
  checkboxes) and in the KG `action_items` table. **Only 2 typed summaries exist
  right now**, so this metric is near-empty today and grows only as summaries
  are created.
- Honest position: ship the heatmap on the dense metrics (meetings + duration),
  and show speakers/action-items in the tooltip **only when data exists for that
  day** — so it's never misleadingly blank, and gets richer over time.

## UI placement
- Device cards render in `DeviceStripView` (`SyncHeaderSection.swift`) as a
  `LazyVGrid(adaptive minimum: 320)`. A full-year grid is wide (~53×11px ≈ 580px
  + labels), which competes with the cards for width.
- Options:
  - **A — compact recent window in the top-right** (e.g. last ~17 weeks / 4
    months ≈ 190px), click → popover with the full year. Fits the described
    "space top-right next to Plaud" cleanly.
  - **B — full-year strip below the device cards**, full width. Most faithful to
    GitHub but not "top-right".
  - **C — full year top-right, horizontally scrollable** in a fixed ~360px box.
- Leaning **A**: compact + click-to-expand respects the requested location and
  the limited width, and the popover gives the full-year view on demand.

## Implementation approach
- **Hand-rolled grid, not Swift Charts.** Swift Charts has no native heatmap
  (you'd fake it with RectangleMarks), and the GitHub look is trivial as a
  `LazyHGrid`/`VStack` of 11px `RoundedRectangle`s with bucketed fill colours.
  Full control, no new framework dependency, macОS 13+ safe.
- **New computed model** on HiDockViewModel: `meetingActivityByDay -> [Date: DayActivity]`
  where `DayActivity { count, totalDuration, speakers?, actionItems? }`.
  - count/duration computed from `syncEntries` (in memory, cheap, reactive).
  - speakers/actionItems: lazy/optional — phase 2, sourced from a lightweight
    index over Raw Transcripts / Summaries (or the KG) so we don't parse files
    on the hot path.
- **View**: `MeetingHeatmapView` — 7×N grid, 5 intensity buckets (GitHub green
  ramp or app-accent ramp), month + weekday labels, `.help()`/custom tooltip on
  each cell. Click a cell → could filter the recordings table to that day
  (nice-to-have).

## Build phases
1. **Data**: `DayActivity` + `meetingActivityByDay` on the view model (meetings +
   duration only). Date parsing incl. the Plaud name-parse fallback.
2. **View**: `MeetingHeatmapView` compact form, intensity buckets, tooltip
   (date · meetings · duration), placed top-right of the header.
3. **Expand**: click → full-year popover.
4. **Tertiary metrics**: speakers + action items in the tooltip when available
   (needs a small transcript/summary index; coordinate with `summaries_index.py`
   / knowledge graph).
5. **Nice-to-have**: click a day → filter the recordings table to that date.

## Parity
macOS first. Windows app (PyQt6) would need its own implementation later.

## Decisions (confirmed with James 2026-06-21)
- **Placement: full-width year strip below the device cards** (option B). Grid is
  7 cells high (days Mon–Sun) × ~53 wide (weeks), ~11px squares so they stay
  comfortably hoverable.
- **Tooltip = tiered stats** (see below): always-on free stats now, sparse
  file-sourced stats layered on later via a per-day index.

## Rollover (tooltip) stats — tiered by reliability/cost

### Tier 1 — free, dense, already in `syncEntries` (always shown, hover-instant)
- **Meetings** that day (also drives the square's colour bucket).
- **Total length** — Σ `duration` across the day's recordings.
- **By device** — count per deviceId, e.g. "2 on H1 · 1 on Plaud".
- **Pipeline progress** — "N transcribed · M summarised" (from `transcribed` /
  `summaryPath`).

### Tier 2 — needs a per-day index, shown only when that day has data
- **Speakers** — KG `meeting_people` (or unique `speaker_id` in diarized JSON).
- **Action / to-do items** — KG `action_items` (or `[ ]` under `## 🎯 Next Steps`
  in typed summaries). Only 2 summaries today → near-empty until more exist.
- **Topics / areas** — summary frontmatter `type` / `area`.

### How Tier 2 is sourced (avoid hover-time file IO)
Do NOT parse transcript/summary files on hover. The knowledge graph already
indexes speakers + action items per meeting in SQLite. Add a small Python
subcommand returning a **per-day rollup** `{date: {meetings, duration, speakers,
actionItems, areas}}`; the app fetches it once on load and caches it. Hover stays
instant; reuses existing KG indexing instead of reimplementing parsing in Swift.

Proposed tooltip:
```
Fri, 12 Jun 2026
3 meetings · 2h 14m
2 on H1 · 1 on Plaud
3 transcribed · 1 summarised
─────────────
5 speakers · 4 action items     (only if the day has summary/transcript data)
```

## Build order (revised)
1. **Tier 1 heatmap** — `DayActivity{count,duration,byDevice,transcribed,summarised}`
   + `meetingActivityByDay` on the view model (all in-memory), date parse incl.
   Plaud name-parse fallback. `MeetingHeatmapView` full-width strip below cards,
   5 intensity buckets, tooltip with the Tier-1 lines. **Ships on real data
   immediately.**
2. **Tier 2 index** — Python per-day rollup subcommand (KG-backed); app fetches
   once on load, merges speakers/action-items/areas into the tooltip when present.
3. **Nice-to-have** — click a day → filter the recordings table to that date.
