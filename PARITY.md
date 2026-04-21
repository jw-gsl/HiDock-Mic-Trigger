# Platform Parity Checklist

Cross-platform feature tracking for macOS (Swift/AppKit) and Windows (Python/PyQt6).
**Update this file whenever a feature is added, changed, or removed on either platform.**

Last reviewed: 2026-04-21

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
| Device icons | SF Symbols + P1/H1 glyph SVGs + H1 for H1e | Unicode emoji + P1/H1 glyph SVGs + H1 for H1e | Both | Bespoke glyphs from `assets/device-images/`, emoji/SF Symbol fallback for unknown SKUs and volumes |
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
| Download complete notification | User notification | Status bar message only | Partial | Windows missing tray notification |

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
