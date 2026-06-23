# Plaud login — port learnings from the standalone Plaud Sync app

Research date: 2026-06-23
Sources:
- `jw-gsl/Plaud-Sync` (Tauri app), `src-tauri/src/browser_login.rs`, versions 0.3.1–0.3.4
- This repo: `hidock-mic-trigger/Sources/PlaudAuth.swift`, `Windows-App/ui/plaud_signin_dialog.py`, `Windows-App/core/plaud.py`

## Current State

Both platforms already implement Plaud sign-in by loading `https://web.plaud.ai`
in an embedded webview and capturing the session cookies (`pld_ut` access token,
`pld_urt` refresh token) once the user signs in (email code or Google/Apple/MS
SSO):

- **macOS** — `PlaudAuth.swift` / `PlaudLoginWindowController`: `WKWebView` + a
  0.5s repeating timer (`pollCookies`) that reads `getAllCookies` and filters by
  cookie name. Plus a Google-specific fast path (`exchangeGoogleSSO`) driven by
  an injected capture script.
- **Windows** — `plaud_signin_dialog.py`: `QWebEngineView` on a **dedicated
  off-the-record `QWebEngineProfile`** ("fresh login each time"), polling the
  profile cookie store + `cookieAdded` for the same cookies. Falls back to a
  manual token-paste form when QtWebEngine isn't installed.

## Findings

Cross-checking against the four bugs found and fixed in the standalone Plaud Sync
app:

1. **Cookie domain matching (wry `cookies_for_url` exact-host match).** Plaud
   sets `pld_ut`/`pld_urt` on `.plaud.ai`; an exact host match against
   `api.plaud.ai` drops them. **Not a problem here** — both platforms read *all*
   cookies and filter by name, so the parent-domain cookie is found regardless
   of host. No change needed.

2. **SPA-redirect capture gap.** Plaud's post-login redirect is an in-page route
   change that fires no native navigation event; an event-driven capture misses
   it. **Not a problem here** — both platforms poll the cookie store on a timer
   (macOS 0.5s; Windows timer + `cookieAdded`), so the cookie is caught whenever
   it lands. No change needed.

3. **Logout / session not cleared → stale session reused.** This is the real
   gap, and it manifests **only on macOS**:
   - macOS sets `config.websiteDataStore = .default()` — the app-wide
     *persistent* store. Nothing ever clears it (`grep` for `removeData` =
     none). `forgetDevice` deletes only the Keychain session, not the webview
     cookies.
   - Because `pollCookies` reads `getAllCookies` every 0.5s, a leftover `pld_ut`
     from a previous pairing is captured **immediately on opening the login
     window** — the user can't sign in as a different account, and re-pairing
     silently re-adopts the old (possibly stale) session. This is a worse variant
     of the bug we fixed in Plaud Sync 0.3.3.
   - **Windows is already correct**: the off-the-record profile means every
     sign-in starts clean.
   - **Fix:** give the macOS login webview an **ephemeral** data store
     (`WKWebsiteDataStore.nonPersistent()`), mirroring Windows' off-the-record
     profile, and poll *that* store. Fresh login every time; account switching
     works; the app's shared default store is left untouched. The session token
     still persists where it should — in the Keychain (`PlaudAuthStore`).

4. **SSO body replay for any provider.** Plaud Sync learned to replay the exact
   `/auth/sso-callback` body so Apple/MS SSO work, not just Google. **Largely
   covered here** by the universal cookie poll: any sign-in method that ends with
   web.plaud.ai setting `pld_ut` is captured by the poll, independent of the
   Google-only `exchangeGoogleSSO` fast path. Low priority; noted below.

### Minor / optional
- **Region coverage.** macOS `PlaudAPI.baseURL` only maps `eu` → `api-euc1`,
  else US. No APAC (`api-apse1`). Region is user-picked (us/eu) in the Device
  Manager. Low priority — only matters for APAC accounts.

## Completed
- [x] Reviewed both platforms' Plaud sign-in against the four Plaud Sync fixes.
- [x] Identified the one real gap: macOS login uses the persistent shared
      `WKWebsiteDataStore.default()` (stale-session / can't-switch-account),
      where Windows already uses an off-the-record profile.

## Planned
- [ ] **macOS:** switch `PlaudLoginWindowController` to
      `WKWebsiteDataStore.nonPersistent()` for the login webview and poll that
      store (not `.default()`). Brings it to parity with Windows.
- [ ] Update `PARITY.md` (Plaud sign-in: both use a fresh/ephemeral webview
      session).
- [ ] (Optional, separate change) add APAC region (`api-apse1`) to
      `PlaudAPI.baseURL` and the region picker.

## Rejected / Not Applicable
- Porting the wry `cookies()` workaround (#1) — N/A, native cookie APIs on both
  platforms already read all cookies by name.
- Porting the `plaudsync://recheck` SPA ping (#2) — N/A, both platforms poll.
- Clearing `WKWebsiteDataStore.default()` on logout — rejected in favour of an
  ephemeral store, which is cleaner (no need to clear, never pollutes the shared
  store, matches Windows).
