# HiDock — session activity badge + toast notifications

Date: 2026-06-16
Status: **Implemented — badge only, pending build.** Decisions (2026-06-16): badge counts
**transcribed only**, **clears on main-window focus**, **no toast** (rely on the existing system
notifications). The in-app toast section below is therefore deferred/unused. App was
mid-transcription, so not yet compiled/deployed.

## Implemented (badge-only)
- `HiDockViewModel.sessionTranscribedCount` (session-scoped).
- Increment at the per-recording completion (`AppDelegate.swift:~6116`) **only when the main window
  isn't key** — so the badge is an "unseen completions" count.
- `updateTranscribedBadge()` sets `NSApp.dockTile.badgeLabel` + a `· ✓N` menu-bar fallback (for
  `.accessory` mode with no Dock tile) via `updateMenuState()`.
- `windowDidBecomeKey` clears the count when the main window (`syncWindow`) is focused.
- No toast, no settings toggle added (existing notify prefs unchanged).

## Goal
Show how many items have been **transcribed** / **downloaded** this session:
1. A **badge** with a count on the app icon.
2. A **toast** when a transcription (or download) finishes.

## What already exists (verified)
- **Dock icon:** the app is `LSUIElement` but flips `setActivationPolicy(.regular)` when the
  main window opens (`AppDelegate.swift:3004`) and `.accessory` otherwise (264/381). So a real
  **Dock badge** (`NSApp.dockTile.badgeLabel`) works while the window/Dock icon is showing; when
  in `.accessory` (menu-bar only) there's no Dock tile, so a menu-bar fallback is needed.
- **Menu bar status item** with a composed title (`AppDelegate.swift:1399–1418`) — a count can be
  appended here for the accessory-mode case.
- **System notifications already fire on completion** via `postTranscriptionNotification`
  (`:662`) and `postSyncDownloadNotification` (`:657`), gated by the existing
  `notifyTranscriptionComplete` / notify-download prefs. So a *system* toast already happens;
  the new ask is an **in-app** toast + a persistent badge count.
- **Completion hook points** (where to increment counters / fire the in-app toast):
  - Transcription: `AppDelegate.swift:6111` (per recording) and `:6327` (batch — "N transcribed").
  - Download: `:5531`, `:5536`, `:5644`.

## Design

### 1. Session counters (`HiDockViewModel`)
```swift
@Published var sessionTranscribedCount: Int = 0
@Published var sessionDownloadedCount: Int = 0
```
Reset to 0 on launch (fresh process). Incremented from the completion hooks above (on the main
thread). A `sessionActivityCount` computed value feeds the badge (see decision below).

### 2. Badge (`AppDelegate`)
`updateActivityBadge()`:
- `NSApp.dockTile.badgeLabel = count > 0 ? String(count) : nil` — shows on the Dock icon in
  `.regular` mode.
- Accessory-mode fallback: append e.g. `✓3` to the menu-bar status title in the existing
  title-composition block (`:1418`), so the count is visible even with no Dock icon.
- Called whenever the counters change (via `didSet`) and on activation-policy changes.
- **Clear** when the user views the result (main window becomes key) — standard badge UX — plus a
  menu item "Clear activity count".

### 3. In-app toast (SwiftUI overlay)
- `@Published var toast: ToastMessage?` on the view model (`{text, kind, id}`).
- An overlay in `MainWindowView` (top-trailing), auto-dismiss after ~3s (task tied to `toast.id`),
  tap to dismiss. Slide/fade transition.
- Fired from the same completion hooks: `"✓ Transcribed \(name)"`, `"⬇︎ Downloaded N recordings"`.
- Complements the system notification: toast shows when the window is foreground; the system
  notification covers background. (Optionally suppress the system one while the window is key to
  avoid double-notifying.)

### 4. Settings
Reuse the existing `notifyTranscriptionComplete` / notify-download toggles to gate the toast.
Add (optional) a "Show activity badge" toggle. Keep minimal.

## Implementation blocks
1. View-model counters + `ToastMessage` type; reset on launch.
2. Increment counters + post in-app toast at the 5 completion hook points.
3. `updateActivityBadge()` — Dock badge + accessory-mode menu-bar fallback; clear-on-window-focus + menu item.
4. Toast overlay view in `MainWindowView` + auto-dismiss.
5. Settings toggle wiring.
6. Build + verify (when not mid-transcription).

## Decisions needed (see chat)
- **Badge counts what?** Transcribed only / downloaded only / combined "new this session".
- **Clear when?** On main-window focus (recommended) / manual menu item / timed.
- **Toast scope:** in-app only, or keep system notifications too (and de-dupe when window is foreground)?

## Notes
- Pure macOS/SwiftUI + view-model work; no Python/extractor changes. Needs a build to land (which
  restarts the app — deferred until transcription is done).
- Counts are session-scoped (reset on relaunch); not persisted.
