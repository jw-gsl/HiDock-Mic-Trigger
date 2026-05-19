# UI Consolidation — Device state + pipeline actions

Research date: 2026-04-21
Scope: the main sync window (`SyncHeaderSection` + `SyncToolbarSection` + filter row).

## Current state (5 rows of controls, several overlaps)

I audited what's on screen right now:

**Row 1 — Status**
- Coloured dot + `syncStatus` text
- **🔴 Recording pill** (added today) — floats independently from the device it refers to
- Selection summary: `N shown · M total · X downloaded · Y selected`

**Row 2 — Storage**
- Per-device storage line: `💾 H1: 4.4 / 32 GB (28 GB free, 255 files) · P1: 1.1 / 64 GB …`
- Or error: `💾 H1: ⚠ Device not responding @ 12:33 — last seen 2.5 GB (258 files)`

**Row 3 — Folders + Download actions**
- Recordings folder path · Transcripts folder path
- Download Selected · Download New · Skip · Unskip (conditional)

**Row 4 — Main toolbar (11 buttons)**
- Pair · Unpair · Recordings · Transcripts · Refresh · Import · Transcribe Selected · Transcribe All · Merge · Trim · Remove · Speaker Labels toggle · Queue (conditional) · Needs Tagging pill (conditional)

**Row 5 — Filter + toggles**
- Select All / None / New
- Filter chips per device: `[● H1 ↻] [● P1 ↻]` — each chip has a status dot AND a reconnect icon
- Hide Downloaded · Auto-download · Auto-transcribe

### Problems with this layout

1. **Device state is scattered**. H1's reachability is a dot on a filter chip in row 5. H1's recording state is a pill in row 1. H1's storage is in row 2. If H1 goes unreachable while recording, the user has to scan three rows to piece it together.
2. **The filter chips are doing double duty**. They're filter buttons, status indicators, and reconnect buttons all at once. Dense and hard to read.
3. **Pipeline actions are split** across rows 3 (Download/Skip) and 4 (Transcribe/Merge/Trim/Remove). The Process-split-button plan (separate doc) already addresses most of this but hasn't shipped.
4. **Configuration clutter** sits in row 4: Pair, Unpair, Recordings folder, Transcripts folder, Speaker Labels toggle. These are infrequent. They don't need premium toolbar real estate.
5. **Three "Auto" toggles** stacked together — each one is asymmetric in what it triggers, and the names don't map cleanly to the Process pipeline we're heading toward.

## Proposed layout (3 zones instead of 5 rows)

### Zone A — Device strip (top, replaces rows 1+2+part of 5)

One row per paired device, stacked vertically. Each device card:

```
┌──────────────────────────────────────────────────────────────────────────┐
│ 🎙 H1  ████████░░░░░░░░░░░░░░  4.4 / 32 GB   255 files                   │
│                                                                          │
│        🔴 Recording          ↻ Reconnect    ⦿ Filter                    │
└──────────────────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────────────────┐
│ 🎙 P1  ██░░░░░░░░░░░░░░░░░░░░  1.1 / 64 GB   155 files                   │
│                                                                          │
│        ✓ Connected           ↻ Reconnect    ⦿ Filter                    │
└──────────────────────────────────────────────────────────────────────────┘
```

State combinations land on one card:
- **Connected, idle**: green checkmark chip, filter & reconnect greyed but available
- **Connected, streaming audio to the mic-trigger**: red "Recording" chip replaces the checkmark, same row
- **Unreachable**: orange warning chip + inline reason ("Device not responding @ 12:33"), reconnect icon filled-orange for attention, storage bar shows last-known with a subtle stripe pattern
- **Imports virtual device** (if any imports exist): one card with icon 📦, no storage bar, no reconnect — just a filter chip and the count

One card = one device = all the state you need for that device.

**Nothing device-related lives outside these cards.** The status row becomes generic pipeline status only (e.g. "Transcribing 3/5", "Skipped 131 recordings").

### Zone B — Pipeline toolbar (single row, replaces rows 3 & most of 4)

Once the toolbar UX plan ships, this becomes:

```
┌──────────────────────────────────────────────────────────────────────────┐
│ [Process Selected ▾]  [Process All New ▾]    [Import]  [Merge] [Trim]   │
│                                                                         │
│                                    [Remove]  [Queue (3)] [Tagging: 12] │
└──────────────────────────────────────────────────────────────────────────┘
```

Removed from the toolbar (moved elsewhere):
- **Pair / Unpair** → Device Manager dialog (already exists, they're duplicates here)
- **Recordings folder / Transcripts folder** → Settings pane (chosen once, rarely changed)
- **Refresh** → automatic (runs on device-appear already; manual-refresh goes on the device card as a secondary gesture)
- **Speaker Labels toggle** → Settings pane (rarely toggled)

### Zone C — Selection + view (compact, replaces row 5's middle and right)

```
┌──────────────────────────────────────────────────────────────────────────┐
│ [All] [None] [New]    [Hide Downloaded]    [Auto-process ▾]             │
└──────────────────────────────────────────────────────────────────────────┘
```

The three Auto toggles collapse into one **Auto-process** setting with a dropdown that exposes granular control for power users (Download / Transcribe / Summarise / All / Off).

The device filter chips **move into Zone A** as a "Filter" button on each device card — it's a device-level affordance and belongs with the device.

### Row count delta

| Before | After |
|---|---|
| 5 rows of controls + chrome | Zone A (per-device card, 1 per device) + Zone B (1 row) + Zone C (1 row) |
| ~20 widgets visible | ~8–10 widgets + device cards |
| Same info scattered in 3 places | Each fact in exactly one place |

## Visual sketch — the device card in detail

```
┌─── H1 ────────────────────────────────────────────────────────────────────┐
│                                                                           │
│  🎙  HiDock H1          [🔴 Recording]                                     │
│  ────────────────────                                                     │
│                                                                           │
│  Storage  ████████░░░░░░░░░░░░░░░░░░░░░░  4.4 / 32 GB        ⟳ Reconnect │
│  Files    255 recordings                                      ⦿ Filter    │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

- Title row: icon + name + primary state chip (Connected / Recording / Unreachable). Never more than one chip; states are mutually exclusive.
- Storage bar: fills from the capacity table. The `+` truncation flag becomes a subtle stripe or chevron at the right edge of the bar.
- Secondary actions stay small and right-aligned. Reconnect becomes primary (larger, orange fill) only when the device is unreachable.
- Whole card tint shifts subtly when the device is the active filter, so the cards also serve as "where the table is scoped to" indicators.

For an import-only virtual device:

```
┌─── Imported files ────────────────────────────────────────────────────────┐
│                                                                           │
│  📦  Imported          3 files, 1.4 GB                         ⦿ Filter   │
│                                                                           │
└───────────────────────────────────────────────────────────────────────────┘
```

## Information still available, now in context

| What you used to look for | Where it is now |
|---|---|
| Is H1 connected? | H1 card's title chip |
| Is the mic-trigger recording? | H1 card (red "Recording" chip) — you see which device is being held |
| Why can't I see new H1 recordings? | H1 card's orange "Unreachable" chip with timestamp |
| How full is H1? | H1 card's storage bar |
| Reconnect H1 | H1 card's reconnect icon |
| Filter to H1 only | H1 card's filter chip (whole card highlights) |
| How many total sync entries? | Bottom-of-table summary (small, low priority) |

The current "Recording" pill in the top bar vanishes — it's folded into the device card where the causation is self-evident (this specific device is being held by the trigger).

## Implementation split

Phase 1 (this plan's deliverable) — reorganisation only, no new behaviour:
- Build a `DeviceCardView` that consumes everything we already publish on `HiDockViewModel` (syncDeviceConnected, syncDeviceStorage, syncDeviceLastError, syncDeviceLastOK, hidockRecordingActive)
- Replace the status + storage + filter rows with a vertical stack of `DeviceCardView`s
- Move Pair/Unpair/folders/Speaker Labels into Settings + Device Manager
- Trim the toolbar to just Import / Merge / Trim / Remove / Queue / Tagging until the Process-button plan ships

Phase 2 — Process-button rollout (separate plan, already scoped):
- Replace Download/Transcribe split into Process / Process All New
- Collapse Auto toggles
- This cleans up Zone B further

Phase 3 — per-device recording attribution:
- Today the red pill is device-agnostic. Parse the trigger CLI output to know which HiDock is being held, so the pill appears on the specific device card rather than on any HiDock card.

## Risks / tradeoffs

- **Vertical space**. Two or three device cards stacked takes more vertical space than a single row of dots. For users with two devices this is fine; if someone pairs four HiDocks and two volumes it'd feel heavy. Mitigation: collapse cards when collapsed-by-default is set; expand on hover or when there's an active issue.
- **Feature relocation** is disruptive to muscle memory. Pair/Unpair moving to the Device Manager dialog is safe (dialog already has those buttons). Folders moving to Settings is low-risk since they're set-once. Speaker Labels is a toggle some users flip often — may need a keyboard shortcut.
- **Filter-via-card** is a new gesture — users who currently use the chip row will need one session to find the filter chip on the card. Worth the concentration of information.

## Rejected alternatives

- **Do nothing** — current layout loses the user on every revisit. Ship.
- **Tabs** (Devices / Recordings / Settings) — heavier cognitively, and most of what they'd see on the Devices tab still needs to be visible while operating on recordings.
- **Floating status panel** — modal/floating UI breaks the single-window model and hides state when you need it.

## Planned

- [ ] Build `DeviceCardView` component; consume existing view-model fields.
- [ ] Replace `SyncHeaderSection` status/storage rows with a vertical stack of device cards.
- [ ] Add an "Imported files" virtual card when any imports exist.
- [ ] Move filter chips into the cards; delete the filter row in `SyncToolbarSection`.
- [ ] Move Pair/Unpair/Recordings folder/Transcripts folder/Speaker Labels out of the toolbar into existing dialogs or a new Settings pane.
- [ ] Red Recording pill removed from header after the per-device card exists.
- [ ] Once attribution lands (Phase 3), show Recording chip on the specific device being held.
