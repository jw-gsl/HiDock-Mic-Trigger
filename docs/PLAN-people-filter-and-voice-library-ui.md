# People filter + Voice Library UI polish
Planning date: 2026-07-11
Status: IN PROGRESS

## Goals (2026-07-11)
1. **Voice Library window**: search + sort speakers; show **# meetings** as well as
   **# samples**; a **filter button per speaker** (mirrors the device-card filter)
   that filters the main meeting list to meetings that person is in.
2. **Main UI person filter**: multi-select people, with an **AND / OR selector**
   (meetings with ALL selected people vs ANY).

## Data layer — people ↔ meetings
Source of truth for who's in a meeting = the `_diarized.json` `speaker_names`
(current, updated on rename/confirm — the .md frontmatter is stale). We ALREADY
open every transcribed entry's sidecar in `refreshTranscriptionState` for
`speakerReviewState`, so extend that single read to also return the non-generic
speaker names — no extra file scan.
- `speakerReviewState(path) -> (tagged, autoMatched, people:[String])`.
- Build `viewModel.meetingPeople: [String: Set<String>]` (recording name → people)
  in the refresh loop (per-row + merged). Derive `personMeetingCounts:[String:Int]`
  and `allPeople:[String]`.

## Voice Library UI
- Search field + sort picker (Name / Samples / Meetings / Recently updated).
- Row shows "N samples · M meetings" (meetings from personMeetingCounts).
- Filter button per row → sets the main person filter to just that person, brings
  the main window forward.

## Main UI person filter
- `@Published syncFilterPeople: Set<String>` + `syncPeopleFilterMode: .any/.all`.
- In `filteredEntriesNoDay`: when non-empty, keep entry iff
  - `.any`  → meetingPeople[name] ∩ filter ≠ ∅
  - `.all`  → meetingPeople[name] ⊇ filter
  (AND-combined with the existing device/status/day filters.)
- UI: a People filter control (menu/popover) with checkboxes for allPeople + an
  AND/OR segmented toggle; a chip/summary when active with a clear button.

## Decisions
- Reuse the existing sidecar read (no new Python verb / scan).
- Person filter AND-combines with device/status/day filters (same as they combine
  with each other); AND/OR only governs the multi-person set.
