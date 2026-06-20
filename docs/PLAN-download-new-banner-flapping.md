# "Downloading new recordings…" banner fires when nothing is downloading

Investigation date: 2026-06-20
Trigger: user noticed the app "suddenly says downloading new recordings" when no
new recordings are actually being downloaded.

## Root cause
Two layers:

1. **Auto-download trigger #3 — the fresh-connect catch-all** (`AppDelegate.swift`
   `renderSyncStatus`, ~line 5264). On every disconnected→connected transition,
   if the device has any recordings, the app runs a `download-new` sweep as a
   catch-all for never-downloaded files. `downloadNewSyncRecordings()` set the
   banner to **"Downloading new recordings…"** + showed the progress bar
   *before* knowing whether anything was new. `download-new` is a no-op when
   everything's already downloaded → nothing downloads, but the UI already
   flashed.

2. **Connection flapping makes it fire repeatedly.** `renderSyncStatus` stores
   `syncDeviceConnected[id] = status.connected` (5158) and trigger #3 fires on
   `connected && !wasConnected`. The status payload has **no cached/live flag**
   (`HiDockSyncStatusResponse`), so a *cached* probe reporting `connected:false`
   clobbers the stored flag — and the next *live* probe then looks "freshly
   connected" and re-fires. Logs (`~/Library/Logs/hidock-menubar.log`) showed H1
   "freshly connected with **355** recording(s)" at 22:11, 22:30, 22:45, 22:57,
   22:58, 22:59, 23:00, 23:01… count stuck at 355 (P1 198, Plaud 15) — i.e. a
   no-op catch-all firing ~once a minute.

## Fix (this PR)
- **Idempotent catch-all** — new `syncDeviceCatchAllSweptCount[deviceId]`.
  Trigger #3 is skipped when the catalog size is unchanged from the last sweep,
  so flapping on an unchanged catalog can't re-fire it. A genuine new recording
  (count change) re-enables it. Recorded when the sweep actually runs (not at
  schedule time) so a timer skipped on `syncBusy` can't permanently consume it.
- **Quiet by default, reveal on real download** — `downloadNewSyncRecordings()`
  no longer flashes the banner / progress bar up front. `download-new` streams
  per-file (`onFile`/`onProgress`), so `beginDownloadProgressIfNeeded()` reveals
  the progress UI only once a file actually starts. No-op sweeps stay silent;
  genuine downloads still show full progress. Trigger-agnostic (works for the
  count-rise trigger #2 too).
- **No misleading "Downloaded 0"** — the completion banner + notification only
  show when `totalDownloaded > 0`; otherwise the status line stays quiet
  (`refreshSyncStatus` restores connected/blank).

## Not done / future
- The deeper flapping itself (cached probe clobbering the connected flag) is
  only *mitigated*, not eliminated. A proper fix would add a `cached`/`live`
  field to `HiDockSyncStatusResponse` (or a separate "last live connection"
  tracker) so cached probes don't reset the connection baseline. Left out to
  keep this change contained; the idempotency guard removes the user-visible
  symptom. Revisit if the flapping causes other issues.
