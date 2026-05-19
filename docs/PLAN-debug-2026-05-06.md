# Debug session: audio trigger + auto-download/transcribe
Date: 2026-05-06
Branch: feature/voice-training
Symptom (user report):
1. Audio trigger doesn't fire when a Teams call starts → HiDock recording not initiated.
2. 4 new meetings on H1 since last download (Apr 30) but auto-download + auto-transcribe haven't run.

## Evidence (from `~/Library/Logs/hidock-menubar.log` + live process state)

- `mic-trigger` CLI is alive (pid 1806, 6m+ uptime), watching `Samson Q2U Microphone`.
- It logged `Using HiDock audio device: HiDock H1` once on startup, then nothing.
- **No `IN USE` / `NOT IN USE` event has been emitted in any session this morning** — i.e. `kAudioDevicePropertyDeviceIsRunningSomewhere` for Samson Q2U has not flipped true.
- At 10:41:51 the extractor reported `device held by Microsoft Edge (pid 6775)` against HiDock H1 (USB-level hold). Strongly implies Teams (running in Edge) had selected **HiDock H1** as its mic input — not Samson.
- Device record count went 290 (Apr 30) → 292 → 293 → 294 (today) on H1. Local Recordings folder's newest file is `2026Apr30-074702-Rec75.mp3`. So 4 files are marooned on the device.
- `defaults read`: `hidockSyncAutoDownload = 1`, `hidockSyncAutoTranscribe = 1`, `preferredMicName = Samson Q2U Microphone` — both flags ON, mic preference correct.
- One historical auto-download fire on 2026-04-29 21:05:42 — preceded by a manual `reconnectDevice: P1` at 21:05:10 (the user pressed ↻ Reconnect). The auto-connect code path has *never* fired triggers #2/#3 in the log.

## Root causes

### Issue 1 — audio trigger not firing on Teams call

`MicTrigger.swift` watches **only** the Samson Q2U input. The trigger flips when *that specific device* is in-use somewhere. If Teams' microphone setting points at HiDock H1 (or any device other than Samson), the watcher will never see `IsRunningSomewhere=true` for Samson and will never start the ffmpeg holder.

Today's log + the Edge-holds-HiDock observation are consistent with Teams using HiDock directly. Need to confirm Teams' current input device.

### Issue 2 — auto-download not firing on auto-connect

`renderSyncStatus` in `AppDelegate.swift` has three auto-download triggers. Triggers #2 (count-rise, line 4434) and #3 (disconnected→connected, line 4453) both gate on `!syncBusy`:

```swift
if status.connected,
   let prev = previousCount,
   currentCount > prev,
   syncAutoDownload, !syncBusy, !syncDownloading {           // <-- !syncBusy
    scheduleAutoDownloadNewRecordings()
}
```

But `renderSyncStatus` is invoked from `runAutoConnectProbes` (line 1620 sets `syncBusy = true`) and `performRefreshProbes` (line 4632 same). The `syncBusy` flag is only cleared in the dispatch-group's `.notify` block AFTER the entire probe batch finishes. So at the moment renderSyncStatus runs, `syncBusy` is *necessarily* true and both triggers are silently skipped.

`scheduleAutoDownloadNewRecordings` itself (line 4585) early-returns on `!syncBusy`, so even if the inline gates were relaxed it would still no-op when called mid-refresh.

The 2-second `syncAutoDownloadTimer` inside the schedule function was *designed* to provide the debounce / busy-state deferral — its post-fire check at line 4588 re-evaluates `!syncBusy` after the batch has completed. The early-return + inline gates are redundant and actively defeat that mechanism.

The reason the trigger ever worked (April 29 P1 case): manual `reconnectDevice` → `runReconnectProbe` does NOT set `syncBusy = true`, so renderSyncStatus saw `syncBusy=false` and the gates passed. The auto-connect path has been broken since trigger #2 was introduced in commit `b78191b` (Apr 25).

## Fix

`hidock-mic-trigger/Sources/AppDelegate.swift` — three line changes:

1. Line 4434, drop `!syncBusy` from trigger #2's inline gate.
2. Line 4453, drop `!syncBusy` from trigger #3's inline gate.
3. Line 4585, drop `!syncBusy` from `scheduleAutoDownloadNewRecordings`'s early-return.

Keep `!syncBusy` inside the 2.0s timer's post-check (line 4588) — that's the correct deferred check; the schedule debounces multi-device batch fires via `syncAutoDownloadTimer?.invalidate()`.

Net effect: count-rise / first-connect triggers schedule a deferred download; 2s later, when the batch has settled, the download fires. Auto-transcribe chains off the download as before.

## Diagnostic for Issue 1

Need user input: what is Teams' current "Microphone" setting? Expected fix is to set it to Samson Q2U so the watch fires. Code-side alternative would be to also watch HiDock H1 itself, but that's redundant — when something reads from HiDock H1, the device records anyway via its hardware path.

## Status

- [x] Root-cause auto-download
- [ ] Apply fix
- [ ] Confirm Teams mic setting with user
- [ ] Rebuild + verify (requires ASK before xcodebuild)
