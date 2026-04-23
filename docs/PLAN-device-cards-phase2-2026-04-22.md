# Device cards — Phase 2 refinements + H1 unreachable debug
Research date: 2026-04-22
Branch: `feature/voice-training`
Relates to: `docs/PLAN-ui-consolidation-2026-04-21.md`

## Where we left off (Phase 1, merged yesterday)

Commits on the branch since Phase 1 landed:
- `6420857` — plan
- `3036ac1` — Phase 1: per-device `DeviceCardView` + `DeviceStripView` replaced the 3 scattered header rows
- `516aada` — fix: card title used raw `displayName` instead of `cleanName`; USB STUB phantom rejected from auto-pair

Card state chips (mutually exclusive): ✓ Connected · 🔴 Recording · ⚠ Unreachable · ⊘ Not connected. Storage bar + reconnect + filter icons live on each card.

## User feedback after living with Phase 1 (today)

1. **Cards are full-width and stacked vertically.** With 2–4 devices the header gets tall. Cards should sit in a grid so two fit side-by-side.
2. **Glyphs don't differentiate models.** H1 and H1E render the same image (`DeviceGlyphH1`); all volumes use a grey `externaldrive` SF Symbol. Hard to tell cards apart at a glance.
3. **Imported-files virtual card** is noise — remove it. Imports are still accessible from the File menu and the table filter.
4. **Volumes shouldn't show when disconnected.** Only show a volume card when the volume is currently mounted/connected.
5. **HiDock cards (both H1 and P1) matter** — keep them prominent even when one is unreachable.
6. **H1 keeps reporting Unreachable even though it is physically plugged in.**

## H1 unreachable — root cause (what I found in the code)

There are **two** distinct failure modes stacking on each other:

### Cause A — mic-trigger holds the H1 during every status refresh

`AppDelegate.reconnectDevice` (the manual ↻ button) correctly does **pause trigger → probe → restart trigger** (commit `516a4fe`). Comment in the code: _"ffmpeg exits synchronously on SIGTERM but the HiDock firmware needs a short moment to release the USB endpoint before the next query will succeed. 800ms is empirically enough on M-series."_

But `refreshSyncStatus` (the automatic refresh that runs on launch, after downloads, on manual Refresh button, etc.) **does not pause the trigger**. It fires a `status --timeout-ms 5000` while ffmpeg is still holding the H1. That call either:
- Times out → UI marks H1 `Unreachable`
- Returns "held by ffmpeg" style error → UI marks H1 `Unreachable`

`autoConnectSyncIfPaired` on launch has the same problem but worse: it uses a 2-second timeout, and `startTrigger()` runs two lines earlier in `applicationDidFinishLaunching` (line 282 vs `autoConnectSyncIfPaired` at line 285). So the very first refresh after launch races ffmpeg and usually loses on the H1.

### Cause B — genuine device-side USB stall

Commit `516aada` notes: _"user's H1 is still physically USB-stalled (direct extractor probe hangs >15s even with no ffmpeg running). That's a device-side issue — unplug/replug is still required."_ This is separate from Cause A and fixes itself on a physical reseat — not something the app can work around, but we can surface it more honestly.

### Why the pattern looks like "keeps being unreachable"

- App launches → trigger starts → auto-connect probes H1 → fails (Cause A) → `syncDeviceLastError` set
- Any subsequent Refresh → same pattern, same failure
- Reconnect button (the ↻) is the *only* path that actually pauses the trigger, and it works — but the error immediately comes back the next time Refresh runs
- When Cause B is also present (USB stall), even the Reconnect probe hangs and takes 30s to time out

## Proposed fixes

### Fix 1 — Card layout: adaptive grid

Replace `DeviceStripView`'s `VStack` with `LazyVGrid` using `GridItem(.adaptive(minimum: 320))`. Two cards fit side-by-side at default window width; reflows to one column if the window is narrow or if only one device is paired.

Tradeoff: loses the strict top-to-bottom priority order we had. Mitigate by sorting HiDocks before volumes so the important cards land in the first row.

### Fix 2 — Glyph differentiation

Three sub-fixes:
1. **Add `DeviceGlyphH1e` asset.** Today we only have `DeviceRecordingH1e`. Fall back to tinting the H1 glyph with an "E" badge until an asset is ready. (Trivial to wire up once the asset is dropped in `Assets.xcassets`.)
2. **Volume icon:** switch from `externaldrive` to a coloured SF Symbol (`externaldrive.fill` + a secondary tint) or fetch the actual volume icon via `NSWorkspace.shared.icon(forFile:)`. Latter is nicer but heavier.
3. **Recording glyph already exists per-SKU** (`DeviceRecordingH1`/`H1e`/`P1`) — already correctly selected when `recording == true`.

### Fix 3 — Drop imports card

Remove `ImportsCardView` and the `hasImports` branch in `DeviceStripView`. Import action stays in File ▸ Import. Import filter stays as a table-level chip (or gets a lightweight chip on the existing filter row). Confirm with James before deleting the struct vs. keeping it dormant.

### Fix 4 — Hide disconnected volumes

In `DeviceStripView`, filter:
```swift
viewModel.syncPairedDevices.filter { device in
    device.deviceType == .hidock
    || viewModel.syncDeviceConnected[device.deviceId] == true
}
```
HiDocks always render (they're paired, important, and their "unreachable" state is useful signal). Volumes only render when `syncDeviceConnected` says true.

### Fix 5 — H1 unreachable: pause the trigger for refresh too (Cause A)

Extend the reconnect pattern to `refreshSyncStatus` **when at least one paired HiDock is present and the trigger is running**:

1. Stop trigger
2. Wait 800ms for USB endpoint to free
3. Run status probes for all paired devices
4. Restart trigger

Impact: a Refresh briefly interrupts recording. We should only do this when:
- a HiDock is paired (volumes don't need it), and
- the last status attempt actually failed, or the user explicitly pressed Refresh (not on automatic refresh loops)

Alternative: don't mark the device unreachable on a status-query failure *if* the trigger is currently running — show the card as "Connected (mic trigger holding)" instead. This is lower risk but relies on the last-known-good state.

Best approach: combine them. Don't flip to Unreachable on status failure while trigger is running; and on manual Reconnect or explicit Refresh, do the pause-probe-restart dance.

### Fix 6 — H1 unreachable: be honest about Cause B

When the reconnect probe times out at the full `extractorProcessTimeout` (30s) with no stderr, the message should explicitly suggest unplug/replug rather than the generic "Device not responding" — the user has already paid a 30s wait and deserves the escalation.

## Implementation order

1. Fixes 1 + 3 + 4 — pure layout, zero behavioural risk. Safe to ship together.
2. Fix 2 — asset work; needs an `H1e` glyph file, otherwise ship with a text-badge fallback.
3. Fix 5 — behavioural, needs James's sign-off on "is it OK for Refresh to briefly stop recording on a HiDock".
4. Fix 6 — cosmetic error-message improvement, ship alongside Fix 5.

## Planned

- [x] Fix 1: Grid layout in `DeviceStripView` — `LazyVGrid(.adaptive(minimum: 320))`
- [x] Fix 3: Remove `ImportsCardView` from the strip (struct kept dormant in file for future reuse)
- [x] Fix 4: Hide disconnected volume cards (HiDocks always render, volumes only when `syncDeviceConnected == true`)
- [x] Fix 2: Switched from monochrome template-rendered SVG `DeviceGlyph*` to colour product-photo PNG `DeviceRecording*` assets. Each SKU has its own artwork so H1/H1E/P1 are visibly distinct — no text badge needed. Volume cards use `NSWorkspace.icon(forFile: "/Volumes/<name>")` for the real Finder icon.
- [x] Fix 5: Stop-probe-restart for `refreshSyncStatus` **and** `autoConnectSyncIfPaired`. Pattern: if any paired HiDock + trigger running → `stopTrigger()`, wait 800ms, run probes, `startTrigger()` after all probes resolve.
- [x] Fix 6: Timeout escalation — `runExtractor`'s full-timeout branch now says "unplug and replug to reset the USB endpoint" instead of the generic "busy recording" message.

## What shipped vs original plan — deltas

- **Fix 2 landed as a bigger swap** than the plan suggested. User feedback during implementation: _"I preferred the images rather than the glyphs"_ — turned out the `DeviceGlyph*` assets were configured as template-rendering SVGs, so they were rendering as flat monochrome silhouettes while the `DeviceRecording*` assets are actual product photos. We now use the product photos for all states (recording is already signalled by the red chip), which implicitly differentiates H1 vs H1E and removes the need for the "E" text badge.
- **Imports card:** `ImportsCardView` struct remains in `DeviceCardView.swift` but is no longer referenced by `DeviceStripView`. Trivial to re-surface if imports need a card again.
- **Build verified:** `xcodebuild -configuration Debug` clean, auto-deployed to `/Applications`, new pids confirmed via `pgrep`.

## Follow-ups

- Windows port does not have Phase 2 device cards yet. PARITY.md flags the rows as "macOS only" / "macOS ahead". Forward these to `Windows-App/main_window.py` + the PyQt6 device section when the Windows work resumes.

## Second pass (same day, 2026-04-22 — after first round didn't fully land)

The Phase 2 commit deployed, but the user saw H1 still stuck on Unreachable with "device held by Python (pid X)" errors — the pause-trigger guard wasn't firing on the launch-time refresh (process was still nil when refreshSyncStatus dispatched from `showSyncWindow`). Second-pass root cause + fixes:

### Root-cause findings

- **ffmpeg wasn't the whole story.** The "held by" error was pointing at a Python pid that turned out to be the app's *own just-exited extractor subprocess*. The macOS kernel's `IOUSBHostInterface` registry retains ownership attribution for a brief window after a pyusb process exits, so the next extractor invocation races it and reports "[Errno 13] Access denied — device held by Python (pid N)". Two sources pumped extractor calls back-to-back: (1) `loadCachedRecordings()` firing a 500ms USB probe per paired HiDock right after launch, (2) `showSyncWindow()`'s `refreshSyncStatus()` running its 5000ms probes immediately after. Each `status` command does a `dev.reset()` inside `prepare_device`, so this was 4 USB resets stacked in under 2 seconds at launch on a two-device setup.
- **The H1 has a genuine device-side stall (Cause B from the plan).** Once ffmpeg was guaranteed paused and the kernel had released registry ownership, the H1 probe *still* ran the full 30s and was killed by the outer `extractorProcessTimeout`. This matches the note in commit `516aada`: "direct extractor probe hangs >15s even with no ffmpeg running". Physical reseat is the only remedy.

### Fixes landed

- [x] **Removed `loadCachedRecordings()` from the launch path.** No more extra USB reset at startup — `refreshSyncStatus`/`autoConnectSyncIfPaired` populate the table within seconds anyway.
- [x] **Gated `showSyncWindow()`'s `refreshSyncStatus()` on `didInitialAutoConnect`.** During the early-launch window the pause-trigger guard can't help (the trigger hasn't started yet), so we let `autoConnectSyncIfPaired` own the first probe instead. Re-opens after launch still refresh normally.
- [x] **Added a 250ms USB teardown cooldown after any HiDock-targeted extractor subprocess exits.** Gives the kernel time to drop the exited process from ioregistry before the next probe starts. Only paid on HiDock runs (`productId != nil`), not `list-devices`/volume/`set-output`.
- [x] **`renderSyncStatus` no longer wipes rows on `connected:false`.** Preserve the last-known entries and storage; the card flips to Unreachable but the table keeps context. Previous behaviour was destroying the H1 catalog on every failed probe.
- [x] **Transient "held by" responses don't clobber a good state.** Both the `performRefreshProbes` and `runAutoConnectProbes` success-but-not-connected paths now detect `error.contains("held by")` and skip the render if the device was previously Connected.
- [x] **Honest hung-timeout message** — when we kill the process at the `extractorProcessTimeout` boundary (regardless of whether `terminationReason` is `.uncaughtSignal` or `.exit`), the error now reads: "Device hung for 30s — the H1 firmware has stalled its USB endpoint. Unplug the HiDock and plug it back in to reset it." The Python extractor installs a SIGTERM handler, so the old `terminationReason == .uncaughtSignal` check was missing this case.
- [x] **Hung-device backoff (180s).** Once a device hits the full-timeout kill, `syncDeviceHungUntil[deviceId]` is set and subsequent auto-refresh / auto-connect paths skip it, preserving its last-known "Connected" state on the card until the backoff expires. Manual Reconnect (↻) always clears the backoff and retries — that's the explicit user signal that "I've reseated, try again". Successful status also clears it.
- [x] **Redundant "Connected — 🔊 P1 · 🎙 H1" status line removed.** The cards already communicate per-device state. Both `performRefreshProbes.group.notify` and `renderSyncStatus` now clear `viewModel.syncStatus` on success instead of writing the emoji summary; `SyncHeaderSection` hides the status row entirely when both `syncStatus` and `syncSummary` are empty. Pipeline messages (Transcribing, Downloading, errors) still surface because they set non-empty strings.

### Verified in running build

- Launch with both devices paired, trigger running: `autoConnectSyncIfPaired` fires its pause-trigger path, stops ffmpeg, waits 800ms, probes, and restarts the trigger.
- P1 returns correctly (156 recordings).
- H1 hangs the full 30s and the new message surfaces: _"Device hung for 30s — the H1 firmware has stalled its USB endpoint..."_.
- Backoff registered: _"Hung backoff: hidock:45068 suppressed from auto-probes for 180s — manual Reconnect will still try"_.
- Next refresh skips H1 instead of hanging again.

### Still outstanding

- The H1 itself needs a physical reseat. That's on the hardware, not the app.
- Windows port does not have the USB-cooldown / hung-backoff / last-known-entries preservation logic — should be ported when Windows work resumes.

## Third pass (same day, 2026-04-22 — user feedback: "stop pausing the trigger")

After the second pass, the H1 was still hanging even when ffmpeg was paused via our pause-probe-restart mechanism. User asked "are we causing this to hang?" and "don't restart the mic trigger just because we're doing app-side work". Both were right on the money.

### Root cause — we were causing the hang

`usb-extractor/extractor.py:prepare_device` called `dev.reset()` unconditionally at the start of every command. That sends a USB bus reset. Side effects:
- yanks any other process holding the device (ffmpeg on the audio-class interface) mid-read
- forces the HiDock firmware to re-initialise its USB stack

The HiDock H1's firmware doesn't gracefully survive a bus reset during active audio streaming. On repeated resets (every `status` call does one), the firmware eventually wedges — opens hang for 30+ seconds and only a physical reseat clears it. So even though we'd gone to the trouble of pausing ffmpeg before each probe (second-pass fix), the damage was already done from earlier resets during the session.

### Third-pass fixes

- [x] **Conditional USB reset in the extractor.** `prepare_device` now calls `dev.set_configuration()` + `claim_interface` on the fast path. Only if `claim_interface` raises `USBError` (i.e. the device is genuinely stuck) do we fall back to `dev.reset()` + retry. This matches the shape pyusb recommends for shared devices. All 103 extractor tests pass.
- [x] **Stop pausing the mic trigger for background refreshes.** Reverted both `refreshSyncStatus` and `autoConnectSyncIfPaired`'s pause-probe-restart dance. If the trigger is running and we want a HiDock status refresh, we simply skip the probe and keep the card's last-known state. Volumes always probe (separate interface). Manual Reconnect (↻) remains the only path that pauses the trigger — that's the explicit user signal "yes, take my recording down for a moment to refresh this device".
- [x] **Reordered launch.** `applicationDidFinishLaunching` used to run `startTrigger()` before `autoConnectSyncIfPaired()`. Now it calls `autoConnectSyncIfPaired(startTriggerOnCompletion: true)` and the trigger starts in the auto-connect completion (after all probes resolve). No more race — probes run on a quiet USB bus at startup. Verified in a live launch: `Auto-connect: launch probe complete — starting mic trigger` fires after both HiDock probes return.
- [x] **Preserve transcribed state across refresh rebuilds.** `renderSyncStatus` was constructing fresh `HiDockSyncRecordingEntry` objects with `transcribed: false` defaulted, then relying on `refreshTranscriptionState` to patch them asynchronously. That caused the Transcribed column to flicker empty on every refresh. Now we carry forward `transcribed` / `transcriptPath` / `speakersTagged` / `summaryPath` / `transcriptionSkipped` from the prior entry keyed by recording name.

### On the missing transcription state.json

`/Users/jameswhiting/_git/hidock-tools/transcription-pipeline/state.json` doesn't exist on this machine. `transcribe.py status` falls back to scanning `RECORDINGS_DIR` and `RAW_TRANSCRIPTS_DIR` for MP3/MD pairs (95 matched on disk right now). That's working — the column was flickering because of the rebuild, not because state was genuinely lost. The preservation fix above makes this a non-issue. If we want belt-and-braces, a follow-up could add an `init-state` subcommand to `transcribe.py` that rebuilds `state.json` from the disk scan so it's authoritative again.

### What the user will see now

- Launch: cards populate cleanly (P1 Connected, H1 Unreachable if hung) before the trigger starts. No race.
- During recording: any refresh that would have touched H1 just skips it. Trigger keeps running. Cards hold last-known state.
- Transcribed column: stable — no more flicker to blank.
- H1 hung: the honest "unplug and replug" message shows. 180s backoff stops us retrying. User taps ↻ after reseat; that clears the backoff and pauses the trigger only for the duration of one probe.
