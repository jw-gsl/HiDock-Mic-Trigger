# Platform Parity Checklist

Cross-platform feature tracking for macOS (Swift/AppKit) and Windows (Python/PyQt6).
**Update this file whenever a feature is added, changed, or removed on either platform.**

Last reviewed: 2026-06-18

---

## 2026-06-18 — Windows parity pass (branch `windows-parity`)

A full audit (see `docs/PLAN-windows-parity-2026-06.md`) found Windows had
stalled ~April while macOS gained the entire June feature wave. This pass
ported that work. Status of the previously macOS-only subsystems:

| Subsystem | Windows status now | Notes |
|-----------|--------------------|-------|
| Summarisation suite | **Both** | Summarise/All, auto-summarise, Summary column, in-app viewer, classify + reclassify, type filter, Templates manager, Provider menu, engine gating. Cowork removed (matches macOS). Shared `typed_summarize`/`llm_cli` backend. |
| Plaud cloud sync | **Both** | Extractor backend ported to Windows-Script; account/token store (`core/plaud.py`), Pair Plaud, async merge + download routing + token refresh. SSO via QtWebEngine **or** manual token paste (WebEngine optional). |
| Embedded CLI / terminal | **Both** | `ui/terminal_pane.py` (pywinpty PTY + QProcess fallback), CLI toggle, Terminal menu, Ask Claude Code, summarise activity feed. |
| Model Manager rethink | **Both** | Two-tier Pipeline Stages / Supporting + per-stage backend picker → `pipeline_backends.json`. |
| Voice Training window | **Both** | `ui/voice_training_dialog.py` — cluster review, sample playback/reassign, confirm-enrollment. |
| Word-range split + recluster | **Both** | In `transcript_viewer.py` (+ speaker-stats header, Copy All). |
| Transcription queue window | **Both** | Previously orphaned dialog now wired (live status, pause/resume/cancel/remove). |
| Device card strip | **Both** | `ui/device_strip.py` — per-device card grid, state chip, counts, reconnect, click-to-filter. Storage bar omitted (Windows extractor doesn't report capacity — see Partial below). |
| Skip/Unskip + opt-out | **Both** | Rename + transcription opt-out. |
| Firmware menu, Delete Local Copy, unmark-downloaded | **Both** | |
| Import Audio File + Imported device | **Both** | `core/imports.py` + virtual device. |
| Speaker-count on initial transcribe | **Both** | `n_speakers` hint through `transcribe_file`. |
| Download-complete toast | **Both** | Was status-bar only. |
| Cache-paint on launch | **Both** (imported + Plaud) | HiDock cached-paint-before-probe not yet (see Partial). |
| Trigger health (Stopped/Waiting/Active) | **Both** | From `mic_trigger.is_running()/is_holding()`. |
| Merge-candidate auto-detection | **Both** | Detection + row tint via `merge_finder`, AND expandable parent/child merge rows (merge writes `merge_groups.json`; ▸/▾ disclosure + indented pieces; click-to-toggle). |
| Transcribed-session badge | **Partial (by design)** | Windows shows it in the tray tooltip — PyQt6 has no dock-badge equivalent (QtWinExtras removed in Qt6). |
| Per-device storage bar | **Both** | Computed client-side (capacity constants + summed bytes), exactly like macOS. |
| Plaud SSO | **Both** | `PyQt6-WebEngine` is now a real requirement (default WebEngine SSO); manual token paste remains a guarded fallback. |
| H1e idle glyph | **Both** | Idle uses the H1e product photo (no bespoke line-art), distinct from H1. |
| HiDock cache-paint-before-probe | **Both** | New no-probe `cached-status` extractor command painted on launch before the live probe. |
| Toolbar (selection-driven verbs) | **Both** | Merge enabled for 2+, Trim for exactly 1, Download Selected shows count (device-card strip already replaced the scattered status/storage rows). |

### Known remaining (by platform constraint, not omission)
- **Transcribed-session indicator**: surfaced in the tray tooltip rather than a
  dock/taskbar badge — PyQt6 on Windows has no first-class badge API.
- All other previously-deferred items are now closed (see the table above);
  details in `docs/PLAN-windows-parity-final-four-2026-06-18.md`.

Verification on the build machine (macOS) is limited to import + ruff + unit
tests (53 Windows-App + 127 usb-extractor pass); the PyQt GUI must be exercised
on a real Windows build/CI run.

## How to use this file

- Before merging any PR that touches UI or features, check the relevant rows below
- Mark status as: `Both` | `macOS only` | `Windows only` | `N/A` (platform-specific by nature)
- If a feature is intentionally platform-only, add a note explaining why
- The PR template includes a parity checkbox as a reminder

---

## Mic Trigger

| Feature | macOS | Windows | Status | Notes |
|---------|-------|---------|--------|-------|
| Start / Stop buttons | `MicTriggerSection.swift` | `main_window.py` | Both | |
| Status indicator (running/stopped) | Green/gray pulsing dot | Status dot + label | Both | |
| PID display | `MicTriggerSection.swift:31` | — | macOS only | Low priority — diagnostic info |
| Uptime display | `MicTriggerSection.swift:36` | `main_window.py:703` | Both | |
| Microphone picker dropdown | `MicTriggerSection.swift` | `main_window.py` | Both | |
| Preferred mic (persisted default) | `AppDelegate.swift:156` | — | macOS only | CoreAudio-specific concept |
| Fallback mic | `AppDelegate.swift:162` | — | macOS only | CoreAudio-specific concept |
| Auto-start trigger on launch | Checkbox + UserDefaults | Checkbox + QSettings | Both | |
| Menu bar / tray Start & Stop | NSMenu items | QSystemTrayIcon menu | Both | |

## Device Management

| Feature | macOS | Windows | Status | Notes |
|---------|-------|---------|--------|-------|
| Device Manager dialog | `DeviceManagerView.swift` | `device_manager_dialog.py` | Both | |
| Device search field | Search bar in dialog | Search bar in dialog | Both | |
| Filter by type (HiDock/Volume) | Segmented control | Combo box | Both | |
| Sort devices (Name/Type/Paired) | Segmented control | Combo box | Both | |
| Pair HiDock button | Dialog + main UI | Dialog + main UI | Both | |
| Pair Volume (scan-volumes) | Popover with auto-scan | Widget with scan button | Both | |
| Connection status badge | "Connected" badge | "Connected" badge | Both | |
| Device type badge | Type label | Type label | Both | |
| Forget device button | Per-device button | Per-device button | Both | |
| Device icons | Product-photo PNGs (`DeviceRecording*`) for H1/H1E/P1; Finder icon via `NSWorkspace` for mounted volumes | Unicode emoji + P1/H1 glyph SVGs + H1 for H1e | macOS ahead | macOS switched 2026-04-22 from monochrome SVG glyphs (template-rendered) to colour product-photo PNGs because they visually differentiate H1 vs H1E vs P1 at a glance. Windows port still on shared SVG — follow-up to port behaviour |
| Connected badge icon | `DeviceGlyphConnected` asset | `connected_glyph.svg` via QPixmap | Both | Small green tick + "Connected" text |

## Recording Table

| Feature | macOS | Windows | Status | Notes |
|---------|-------|---------|--------|-------|
| Columns: Device, Status, Transcribed, Name, DateTime, Duration, Size, Path | All present | All present | Both | |
| Row selection: checkboxes | `RecordingsTableView.swift:8` | — | macOS only | Windows uses native row highlight selection |
| Reveal-in-Finder column button | Folder button in row | — | macOS only | Available via context menu on Windows |
| Sorting (all columns) | Click header | Click header | Both | |
| Context menu: Download | Right-click | Right-click | Both | |
| Context menu: Mark as Downloaded | Right-click | Right-click | Both | |
| Context menu: Transcribe | Right-click | Right-click | Both | |
| Context menu: Show in Finder / Open File Location | Right-click | Right-click | Both | |
| Context menu: Open Transcript | Right-click | Right-click | Both | |
| Context menu: Export as SRT... | Right-click | Right-click | Both | Copies paired `.srt` or regenerates via `shared.srt_writer` CLI |
| Double-click to open file | Opens in Finder | Opens file location | Both | |

## Recording Toolbar

| Feature | macOS | Windows | Status | Notes |
|---------|-------|---------|--------|-------|
| Select All / None / New buttons | `SyncToolbarSection.swift` | `main_window.py` | Both | |
| Device filter | Filter buttons per device | Combo box dropdown | Both | |
| Hide Downloaded checkbox | Toggle | Checkbox | Both | |
| Auto-download checkbox | Toggle | Checkbox | Both | |
| Summary display (count/downloaded/transcribed) | Footer text | Summary label | Both | |

## Download Operations

| Feature | macOS | Windows | Status | Notes |
|---------|-------|---------|--------|-------|
| Download Selected button | Header bar | Header bar | Both | |
| Download New button | Header bar | Header bar | Both | |
| Mark Done button | Header bar | Header bar | Both | |
| Progress bar with percentage | Linear progress | Linear progress | Both | |
| Stop download button | In progress bar | In progress bar | Both | |
| Auto-download on refresh | Configurable | Configurable | Both | |
| Volume device downloads (volume-import) | `AppDelegate.swift` | `main_window.py` | Both | |
| Download complete notification | User notification | Tray notification | Both | 2026-06-18 — Windows now posts a download-complete tray toast |
| Plaud cloud sync (account login + cloud recordings) | `PlaudAuth.swift` + `plaud_client.py` | `core/plaud.py` + `ui/plaud_signin_dialog.py` + `Windows-Script/plaud_client.py` | Both | 2026-06-18 — Windows ported: sign-in (QtWebEngine or manual token paste), Pair Plaud, async cloud merge + download routing |
| Plaud sign-in uses a fresh/ephemeral webview session | `WKWebsiteDataStore.nonPersistent()` per login | Off-the-record `QWebEngineProfile` per dialog | Both | 2026-06-23 — macOS switched off the shared persistent `.default()` store (was reusing a stale `pld_ut` on re-pair / blocked account switching); now matches Windows' fresh-each-time behaviour |
| Plaud cloud token auto-refresh | `plaud_client.py` refreshes the short-lived `pld_ut`; rotated tokens persisted to Keychain via `PlaudSession.applyingRefreshedTokens` | `core/plaud.apply_refreshed_tokens` (QSettings) | Both | Windows persists rotated tokens from the extractor's `refreshedTokens` payload |

## Transcription

| Feature | macOS | Windows | Status | Notes |
|---------|-------|---------|--------|-------|
| Transcribe Selected | Toolbar button | Menu action | Both | |
| Transcribe All | Toolbar button | Menu + toolbar | Both | |
| Speaker labels (diarize) toggle | Checkbox | Checkbox (in model mgr) | Both | |
| Progress bar with file count | "Transcribing X/Y" | Status text + progress | Both | |
| Cancel transcription button | Red Cancel button | Cancel button | Both | |
| Transcript viewer dialog | Speaker view + colors | Speaker view + colors | Both | |
| Rename speakers in transcript | Click to edit | Click to rename | Both | |
| Speaker enrollment on rename | Automatic | Subprocess call | Both | |
| Mid-segment word-range split | `WordTokensView` drag-select + inline speaker bar (`TranscriptViewerView.swift`) | `transcript_viewer.py` (select words → assign speaker) | Both | 2026-06-18 — Windows ported; sub-segment timing approximated by word-count interpolation (no per-word timestamps in the diarized JSON) |
| Re-cluster transcript using user labels | "Re-cluster from my labels" toolbar button → `transcribe.py recluster-with-anchors` | `transcript_viewer.py` "Re-cluster from my labels" | Both | 2026-06-18 — Windows ported; runs `recluster-with-anchors` on the diarized JSON |
| Transcription complete notification | User notification with actions | Tray notification | Both | macOS has "Open Transcript" / "Show in Finder" actions |
| Auto-emit `.srt` beside `.md` on transcription | `transcribe.py` (shared pipeline) | `transcribe.py` (shared pipeline) | Both | Shared `shared/srt_writer.py`. Speaker labels included when diarized. |
| Export as SRT (context menu) | `onExportSRT` → `NSSavePanel` → copy/regenerate | `_ctx_export_srt` → `QFileDialog` → copy/regenerate | Both | Regenerates from `_diarized.json` / `_whisper.json` for legacy transcripts that predate auto-emit. |

## Voice Library

| Feature | macOS | Windows | Status | Notes |
|---------|-------|---------|--------|-------|
| Voice Library dialog | Full window | Full dialog | Both | |
| Speaker list with sample count | List with count | Table with count | Both | |
| Last updated timestamp | Formatted date | Date only | Both | |
| Rename speaker | Click to edit | Double-click or button | Both | |
| Delete speaker | Trash button | Delete button | Both | |
| Empty state message | Guidance text | Guidance text | Both | |

## Model Management

| Feature | macOS | Windows | Status | Notes |
|---------|-------|---------|--------|-------|
| Models dialog | Full window | Full dialog | Both | |
| Model list with status/size | Rows with icons | Rows with icons | Both | |
| Download / Delete buttons | Per-model | Per-model | Both | |
| Download progress bar | Linear + percentage | Linear progress | Both | |

## Onboarding

| Feature | macOS | Windows | Status | Notes |
|---------|-------|---------|--------|-------|
| Five-step wizard | Welcome, Connect, Mic, Model, AllSet | Same five steps | Both | |
| Step progress dots | Dot indicators | Dot indicators | Both | |
| Step completion badges | Checkmark/Skip | Checkmark/Skip | Both | |
| Auto-detect HiDock + auto-advance | Polling | Polling | Both | |
| Skip / Back / Next buttons | Context-aware | Context-aware | Both | |

## Notifications

| Feature | macOS | Windows | Status | Notes |
|---------|-------|---------|--------|-------|
| Transcription complete | UNNotification with actions | Tray showMessage | Both | macOS has richer actions |
| Download complete | UNNotification | — | macOS only | Windows shows status bar text only |
| Mic change | UNNotification | — | macOS only | CoreAudio callback, not available on Windows |
| Model download complete | — | Tray showMessage | Windows only | |
| Notification preferences toggle | Bell menu | Preferences menu | Both | |

## Menus & Keyboard Shortcuts

| Feature | macOS | Windows | Status | Notes |
|---------|-------|---------|--------|-------|
| File > Open Recordings/Transcripts | Buttons in window | File menu actions | Both | |
| File > Quit | Cmd+Q | Ctrl+Q | Both | |
| Refresh | Cmd+R (menu bar) | Ctrl+R / F5 | Both | |
| Start trigger | Cmd+S | Ctrl+S | Both | |
| Download selected | — | Ctrl+D | Windows only | |
| Transcribe selected | — | Ctrl+T | Windows only | |
| Toggle trigger | — | Ctrl+Shift+S | Windows only | |
| Select all rows | — | Ctrl+A | Windows only | |
| Show Logs | Cmd+L | — | macOS only | Opens log files |
| Show Status | Cmd+I | — | macOS only | Shows sync window |
| Send Feedback | Cmd+F | — | macOS only | Via menu |
| Terminal... | Cmd+Shift+T | — | macOS only | Embedded PTY (SwiftTerm) for CLI auth (e.g. `claude auth login`) |
| Appearance menu | Menu bar submenu | Help menu submenu | Both | |
| Help > About | macOS standard | QMessageBox | Both | |
| Help > Check for Updates | Menu bar | Help menu | Both | |

## System Integration

| Feature | macOS | Windows | Status | Notes |
|---------|-------|---------|--------|-------|
| System tray / menu bar | NSStatusItem (menu bar) | QSystemTrayIcon (tray) | Both | Platform-appropriate |
| Minimize to tray | Standard behavior | Hide on minimize | Both | |
| Double-click tray to restore | N/A | Tray activation | Windows only | Platform-appropriate |
| Launch on login | LaunchAgent plist | — | macOS only | Could add Windows registry/Task Scheduler |
| Dark/Light/Auto theme | System colors | QSS stylesheets | Both | |

## Update Checker

| Feature | macOS | Windows | Status | Notes |
|---------|-------|---------|--------|-------|
| Auto-check on launch | Once per version | On startup | Both | |
| Manual check (menu) | Menu action | Menu action | Both | |
| Update alert (Restart/Later/Skip) | NSAlert | QMessageBox | Both | |
| Download progress | Status bar | Dialog | Both | |
| Update on quit | Script at shutdown | Update process | Both | |

---

## Sync Header — Device Cards (Phase 1 + Phase 2)

| Feature | macOS | Windows | Status | Notes |
|---------|-------|---------|--------|-------|
| Per-device card (state chip + storage + reconnect + filter) | `DeviceCardView.swift` | — | macOS only | Phase 1 shipped 2026-04-21; Windows still has scattered status/storage/filter rows |
| Adaptive grid (2+ cards side-by-side) | `LazyVGrid` in `DeviceStripView` | — | macOS only | 2026-04-22 |
| Hide disconnected volumes from card strip | Filter in `DeviceStripView.visibleDevices` | — | macOS only | 2026-04-22 |
| Imported files virtual card | Removed 2026-04-22 — not rendered | — | Removed on macOS | Imports remain accessible via File menu + table filter |
| H1/H1E/P1 distinct device artwork | Product-photo PNGs per SKU | H1 asset shared with H1E | macOS ahead | 2026-04-22 |
| Launch order: probe first, then start trigger | `applicationDidFinishLaunching` → `autoConnectSyncIfPaired(startTriggerOnCompletion:)` | — | macOS only | 2026-04-22 (second pass) — probes run while no ffmpeg holds the device, then trigger starts. Avoids the USB race that used to stall the H1 |
| Auto-refresh / auto-connect skip HiDocks while trigger is active | `refreshSyncStatus` + `autoConnectSyncIfPaired` filter out HiDocks when `process != nil` | — | macOS only | 2026-04-22 (second pass) — trigger is never stopped for background refreshes. Volumes always probe. Manual Reconnect (↻) is the only path that pauses the trigger, and only for that one device |
| Preserve transcribed state across refresh rebuild | `renderSyncStatus` carries forward `transcribed` / `transcriptPath` / `speakersTagged` / `summaryPath` from old entries when rebuilding | — | macOS only | 2026-04-22 — prevents the Transcribed column flickering empty between `renderSyncStatus` and `refreshTranscriptionState` |
| Hung-device backoff | `syncDeviceHungUntil` + 180s suppression from auto-probes; cleared by manual ↻ or successful status | — | macOS only | 2026-04-22 |
| Timeout escalation suggests unplug/replug | `runExtractor` timeout path (catches both `.uncaughtSignal` and `.exit`) | — | macOS only | 2026-04-22 |
| Extractor: conditional USB reset | `prepare_device` claims first; only calls `dev.reset()` if claim fails | `usb-extractor/extractor.py` | macOS (via shared extractor); Windows uses same extractor via `Windows-Script` | 2026-04-22 — root-cause fix: removes the bus reset we used to send on every status query, which was wedging the H1 when ffmpeg held the audio interface |

## Known Intentional Differences

These differ by design due to platform conventions:

| Area | macOS | Windows | Reason |
|------|-------|---------|--------|
| Row selection UI | Checkboxes per row | Row highlight multi-select | Platform convention |
| Device filter UI | Inline buttons | Combo box dropdown | Space constraints |
| Icons | SF Symbols | Unicode emoji | Availability |
| Theme system | Native SwiftUI | QSS stylesheets | Framework difference |
| Audio backend | CoreAudio | WASAPI via pycaw | OS API difference |
| Preferred/Fallback mic | Supported | N/A | CoreAudio concept |
