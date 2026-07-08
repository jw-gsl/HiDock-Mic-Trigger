# Why the app became slow at scale — root cause & prevention
Analysis date: 2026-07-09
Context: after the HiNotes → H1 migration tripled the catalog (~590 → ~1,705
entries) the app beachballed / sat at ~30% CPU while idle. Fixed across
commits on `feature/ux-windows-voicelib` (merge-candidate cache, summary index,
memoised derived lists, guarded @Published writes, self-ticking uptime).

## The four concrete defects (found by `sample`-ing the live process)
1. **Uncached derived data on the view model.** `filteredEntriesNoDay`,
   `visibleEntries`, `displayRows`, `meetingActivityByDay` were *computed
   properties* — recomputed on **every SwiftUI render**, and `filteredEntriesNoDay`
   ran twice per render (a full O(n log n) sort of 1,705 items each).
2. **Per-row filesystem work.** `refreshTranscriptionState` called
   `findSummaryPath` per entry, and each call listed the **entire ~1,400-file
   Summaries directory** → ~1,705 full directory scans per refresh, on the main
   thread.
3. **Per-row O(candidates) work.** Each table row read `mergeCandidatePaths` /
   `effectiveMergeCandidates` (a `.filter` + `.contains(where:)` with slow
   Unicode string compares) and built a `.contextMenu` that filtered candidates
   again — all per row.
4. **Unconditional `@Published` write in a 1 Hz timer.** The uptime timer wrote
   `viewModel.triggerUptime` every second. `@Published` fires `objectWillChange`
   on **every** assignment regardless of equality — even writing `""` while the
   trigger waited for a device — so the whole window (incl. the 1,705-row table)
   re-rendered once a second, forever.

## Root cause (how this got into the code)
Not a classic bug — a **latent scaling assumption baked into idiomatic SwiftUI
patterns, never measured, then tripped by a 3× data jump.**

- **Built and tested at small N.** The app was developed against the dev's own
  HiDock (tens–hundreds of recordings). At N≈100–600, per-render recomputation,
  per-row directory scans, and 1 Hz whole-window re-renders are all
  imperceptible. Nothing "broke" at the migration — the constants simply
  multiplied past the point a human notices. There was no test at realistic (or
  10×) volume, so the cost was invisible until a real dataset arrived.
- **SwiftUI's cost model is invisible in code review.** Three of the four are
  SwiftUI traps that look fine on the page:
  - A computed property on an `ObservableObject` reads like "just a getter", but
    it re-runs on every dependent view render. Idiomatic Swift; an anti-pattern
    for *expensive* derived data.
  - `@Published` re-renders on assignment, **not** on change — so any
    unconditional write (a timer, a state-sync that reassigns unchanged values)
    invalidates every observing view.
  - `List` `rowContent` and `.contextMenu` closures run per row; a filter or a
    filesystem call there is silently multiplied by the row count.
  - Every view that observes one **fat view model** re-renders on *any* of its
    `@Published` fields changing — so a 1 Hz uptime string re-rendered the whole
    table.
- **Feature accretion without a perf budget.** Merge-candidate detection,
  summary-existence checks, the uptime readout, the LED ticker — each added a
  little per-render / per-row / per-second work. Individually invisible;
  together, at 3× data, fatal. There was no "is this O(1) per row / per render?"
  gate and no memoisation discipline.
- **`syncViewModelState()` grew to ~90 call sites**, each reassigning the whole
  entries array and other fields unconditionally — a broadcast that, combined
  with uncached derived data, meant frequent full recomputes.

## Fixes applied (and the pattern each establishes)
- **Memoise derived data behind a dirty flag** (`ensureDerived()` +
  `markDerivedDirty()` on inputs' `didSet`). Compute once per input change,
  cache for reads; reads stay synchronous/fresh. → renders are O(1).
- **List the Summaries dir once** (`buildSummaryIndex()` → stem→path map);
  O(1) per entry.
- **Cache per-row lookups** (`mergeCandidatePaths`, `mergeCandidates(forPath:)`)
  so row/context-menu work is O(1).
- **Guard `@Published` writes** with equality; for high-frequency UI (a ticking
  clock/uptime) use a **local `TimelineView`** so the tick never touches shared
  state.

## Prevention — make it not recur
- [ ] **No expensive work in a computed property** that a view reads per render —
      memoise (dirty flag) or precompute.
- [ ] **Per-row (List/contextMenu) work must be O(1)** — no filesystem, no
      filters; precompute maps in the view model.
- [ ] **Guard `@Published` writes** (skip if unchanged); never write shared state
      from a repeating timer — use a local `TimelineView`/`@State`.
- [ ] **Coalesce/debounce** state syncs; don't broadcast the whole model from ~90
      sites.
- [ ] **Consider splitting the fat view model** (or scoping observation) so
      high-churn fields (trigger/uptime/LED) don't invalidate the heavy table.
- [ ] **Add a scale smoke test**: launch against a synthetic ~2,000-entry
      catalog; assert idle CPU ≈ 0 and scrolling stays smooth; keep `sample` in
      the dev loop when touching the table/view model.
- [ ] When a bulk import/migration is planned, **profile at the post-migration
      size first** — the migration was the forcing function here.

## One-line takeaway
The code was correct but written for a small library; idiomatic SwiftUI hides
per-render / per-row / per-assignment cost, so a 3× data import turned four
un-memoised, un-guarded hot paths into a beachball. Memoise derived data, keep
per-row work O(1), guard published writes, and test at scale.
