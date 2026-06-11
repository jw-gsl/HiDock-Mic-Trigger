# Plaud Sync — login-window hang & auto-download fixes
Research date: 2026-06-10
Sources:
- macOS hang reports: `/Library/Logs/DiagnosticReports/plaud-sync_2026-06-10-071305_GSL-007.hang`, `…-071922_GSL-007.hang`
- Force-quit record: `~/Library/Application Support/CrashReporter/plaud-sync_545FCA49-…plist` (`ForceQuitDate 2026-06-10 14:19:17 +0000`)
- App log: `~/Library/Application Support/com.jameswhiting.plaud-sync/login-debug.log`
- Versions: tauri 2.11.2, wry 0.55.1, tao 0.35.3
- wry cookie impl: `wry-0.55.1/src/wkwebview/mod.rs` (`cookies()`, `wait_for_blocking_operation`)

## Current State
Standalone Tauri app at `plaud-sync/`. Two reported problems, both now addressed on branch
`fix/plaud-sync-login-hang-and-autosync`:
1. Login window doesn't close / app hangs.
2. Auto-downloads don't start when new recordings arrive.

## Findings

### 1. Login window not closing = main-thread hang in `cookies_for_url`
Both `.hang` reports show the **main thread** wedged in:
```
handle_user_message → wry::…::cookies_for_url → cookies → _CFRunLoopRunSpecificWithOptions (nested run loop)
```
Mechanism:
- `read_session_cookie()` (`browser_login.rs`) calls `window.cookies_for_url("https://api.plaud.ai")`.
- wry 0.55.1's `cookies()` calls WebKit `getAllCookies` and **spins a nested run loop on the
  main thread** (`wait_for_blocking_operation`, ~1s cap) waiting for the callback.
- This was driven from a **400ms background poll loop**, so during the OAuth flow the main
  thread was almost continuously pumping that nested run loop → UI frozen → `close_login_window`
  (which runs on the main thread) never executes → window stays open.
- Timeline confirms it: hang report end `07:19:18 -0700` == `ForceQuitDate 14:19:17 +0000` —
  the user force-quit the frozen app. Two hangs (108s, 367s) both in the cookie path.
- After force-quit + relaunch (14:25, login-debug.log), the stored token was still valid, so no
  login was needed and the app worked — auto-sync ran fine (`1 downloaded, 9 skipped` at 14:29).

### 2. Auto-downloads = timer-only, no "new recording" trigger
- `auto_sync_loop` woke every 60s but only listed+downloaded when `auto_sync_minutes` (user: 15)
  had elapsed. No push from Plaud, no between-interval check → a new recording could sit up to
  15 min before the app even looked. Also `last_sync_epoch` reset on every manual download,
  pushing the auto timer out further.
- The in-app **"Sync" button only refreshes the list, never downloads** — so new items showed as
  "New" but didn't save until the timer fired or "Download" was pressed. (Behaviour unchanged;
  noted for clarity.)

## Completed
- [x] Root-caused the hang to wry `cookies_for_url` nested run loop driven by a 400ms poll.
- [x] **Hang fix** (`browser_login.rs`): cookie reads are now **navigation-gated**. A shared
  `cookie_check: Arc<AtomicBool>` is set by `on_navigation` (non-`plaudsync://` URLs only) and
  `on_page_load`; the background loop flushes JS logs every 500ms (cheap, non-blocking) but only
  calls `read_session_cookie` once a navigation/page-load has flagged a possible session change,
  after a 400ms settle. Bounds main-thread cookie reads from ~2.5/s to a handful, each when the
  page is idle. JS-interception capture paths (`plaudsync://auth` / `plaudsync://sso`) unchanged.
- [x] **Auto-download fix** (`sync.rs`): `auto_sync_loop` now checks Plaud and downloads new
  recordings **every tick (`AUTO_SYNC_TICK_SECS = 60`)** regardless of the legacy interval, so
  new recordings land within ~1 min. Emits `auto-sync-complete` (and logs) only when something
  actually downloaded/failed, so a quiet tick doesn't spam the log or force a UI re-list.
- [x] `get_sync_info` countdown now reflects the 60s cadence, not `auto_sync_minutes`.
- [x] UI: SyncView refreshes the schedule every ~15s to keep the countdown live; SettingsView's
  misleading "Check every 15 min/30 min/1 h/3 h" dropdown replaced with honest text.
- [x] `cargo check`, `cargo test` (10 passed), `npm run check` (0 errors) all green.

## In Progress / To verify
- [ ] **End-to-end OAuth login not tested here** — the cookie-capture fix is verified by reasoning
  + compile/tests, but a real Google/Apple sign-in on macOS 26.5 should be run to confirm the
  window closes and no hang recurs. Suggested: launch a dev build, sign in via Google, watch
  `login-debug.log` and Activity Monitor.
- [ ] Confirm auto-download picks up a brand-new recording within ~1 min in a live session.

## Rejected / Not Applicable
- Bumping wry to "a fixed version" — no verified upstream fix identified; not relied upon.
- Reading cookies via `run_on_main_thread` — would still spin the same nested run loop, and
  calling `cookies_for_url` from the main thread deadlocks on its own user-message round-trip.
- Dropping native cookie capture entirely — needed for the already-authenticated / arbitrary-SSO
  fast path (pld_ut is HttpOnly; JS can't read it).
- Keeping the per-minutes interval setting — user opted for a fixed ~60s cadence; the knob would
  contradict actual behaviour.
