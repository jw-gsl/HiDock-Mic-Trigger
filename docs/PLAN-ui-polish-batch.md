# UI polish batch (2026-06-22)

Trigger: James's feedback batch after the meeting-activity heatmap landed. Split
into discrete pieces, worked through in order. Each ticked off as it ships.

## Items

### 1. Filter — add "Merged" option  ⬜
Add a Merged entry to the status filter so the user can see which recordings
have been merged. (Merge state: an entry whose name is in a `mergeGroups`
child list, or the merged-file rows.)

### 2. Filter — make it multi-select  ⬜
Convert the Filter dropdown to multi-select (like the Hide menu) so statuses can
be stacked (e.g. Downloaded + Transcribed). Today it's single-select
(`SyncStatusFilter`).

### 3. Status colour overhaul  ⬜
Review + redesign the status colours (`StatusLevel` → colour mapping). Currently:
success=green, transcribed=purple, summarised=indigo, warning=orange, error=red,
info=blue, skipped=teal, removed=muted-red. Write a short colour review first.

### 4. Tier-2 heatmap tooltips (speakers + action items)  ⬜
Per-day speakers + action items via a knowledge-graph–backed Python index,
fetched once on load and merged into the tooltip when present. (Plan in
PLAN-meeting-activity-heatmap.md.)

### 5. Heatmap date-mode switch (Recorded ↔ Transcribed)  ⬜
Toggle which date drives the grid. Default = Recorded. NOTE: entries now carry
`transcribedDate`, so the Transcribed mode needs no extra index — cheap.

### 6. Header spacing cleanup  ⬜
- (a) Move the "N shown · M total · K downloaded" summary next to the
  "N need tagging" indicator to reclaim space.
- (b) Move the "Refreshing…/Downloading…" status onto the "Meeting activity"
  line (next to the legend / hover hint), showing/hiding as needed — less is
  more.
- (c) With that space freed, move the Imports row/button up to just beneath the
  heatmap.

### 7. Consolidate feedback buttons into one dropdown  ⬜
"My feedback" + "Send feedback" → a single Feedback button with a dropdown menu.

### 8. Mic trigger "waiting" timer  ⬜
When the HiDock isn't connected and the trigger is waiting, show just "Waiting"
— stop counting up. Show elapsed time only when connected (how long it's been
connected), not while waiting.

## Execution order
8 (mic timer, self-contained) → 1+2 (filter merged + multi-select) →
5 (date-mode switch, cheap now) → 6+7 (layout cleanup + feedback dropdown) →
3 (colour overhaul, design review) → 4 (Tier-2 tooltips, Python index — biggest).

## Branch
Continue on `feature/meeting-activity-heatmap` (PR #45) for heatmap-related items
(4, 5, 6b); the rest are app-wide polish landing in the same PR to avoid stacked
branches. Merge when the batch is done.
