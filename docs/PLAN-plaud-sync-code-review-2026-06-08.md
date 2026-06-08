# Plaud Sync — Code Review
Review date: 2026-06-08
Reviewer: Claude (code check requested by JW)
Scope: `plaud-sync/` (Tauri 2 + SvelteKit app, branch `feature/diarization-v2`, untracked)
Sources: full read of `src-tauri/src/**`, `src/**`, config, CI workflow, build script

## Current State
New, well-structured Tauri 2 + Svelte 5 (runes) app for downloading Plaud recordings.
Clean module split (`plaud/auth`, `plaud/client`, `storage`, `sync`, `browser_login`,
`commands`). Three login paths (browser SSO capture, email/password, paste-JWT).
Passwords in system keychain, token + settings in `config.json`. Overall quality is good;
the issues below are correctness edge-cases and repo hygiene, not architectural problems.

## Status 2026-06-08 (later) — fixes applied
Branch: `feature/diarization-v2` (working tree). `cargo check` clean, no warnings.
- [x] **B1** — opus files now saved with the served extension (`audio_path.with_extension(ext)`),
  skip-check looks for both `.mp3`/`.opus`. `sync.rs`.
- [x] **B2** — `get_token` only refreshes via stored password; browser/JWT logins keep using
  their valid token instead of erroring "No password stored." `plaud/auth.rs`.
- [x] **B3** — replaced hand-rolled `urlencoding_helper` with reqwest `.form()` (correct UTF-8);
  deleted the helper. `plaud/auth.rs`.
- [x] **H1** — `/dist/` added to `plaud-sync/.gitignore` (keeps the 4 MB DMG/.app out of git).
- [x] **H2** — removed unused `thiserror` dep. `Cargo.toml`.
- [x] **LOG1 (new, severe)** — fixed a `CloseRequested` feedback loop that wrote **557,369
  lines / 32 MB** to `login-debug.log`: the close handler called `close_login_window` →
  `window.close()` → `CloseRequested` → … Now guarded with an `AtomicBool` and no longer
  re-closes the window. Removed the dead `cancel_login` and `navigate_login_window` fns.
- [ ] Deferred (not requested this round): S1 (CSP), S2 (third-party storage scan), C1
  (Storage clone-out-of-mutex), C2 (region not persisted).

## Google sign-in failure — diagnosis (from login-debug.log)
**Symptom:** browser login with Google never completes; window times out / is closed.

**Evidence (two attempts, 16:01 and 16:06):** user reaches Google and authenticates
(password + TOTP, then account-picker). The OAuth call is Google Identity Services (GIS)
*button* flow: `response_type=id_token`, `redirect_uri=gis_transform`,
`response_mode=form_post`, `display=popup`, launched via
`window.open(…, "g_credential_picker_…", "popup,width=500,height=550")`. After auth the flow
dead-ends on an `accounts.google.com` page with `js error: Script error.` and never returns to
`web.plaud.ai`. No Plaud JWT is ever produced, so the token-watcher never fires.

**Root cause (mechanism, invariant across builds):** GIS popup flow hands the result back by
having the popup call `window.opener.postMessage(credential)` → `web.plaud.ai` receives the
Google `id_token`, exchanges it with Plaud's backend for a Plaud JWT (which the watcher *would*
catch). In an embedded webview that handoff breaks:
- Older build (in the log): the JS `window.open` hook *redirected same-window*, so there was no
  opener at all → GIS errored ("Script error").
- Current build: opens a real popup via `on_new_window: Allow`, but (a) the popup webview's
  `window.opener` is not a working reference to the main window, so `postMessage` can't reach
  `web.plaud.ai`; and (b) the watcher/relay is only injected into the main window (label
  `plaud-login`), never the popup — and the `gsi/transform` relay writes to the *popup's* own
  `sessionStorage` and `postMessage`s within the popup, none of which reaches the main window.

So the only broken link is **delivering the Google `id_token` back to web.plaud.ai**; everything
downstream (exchange → Plaud JWT → watcher) already works. Email/password and paste-token logins
work because they hit `/auth/access-token` directly with no popup/opener handoff.

### Option 2 investigation (2026-06-08) — BLOCKED on an undocumented endpoint
Goal was to capture the Google `id_token` in the popup and call Plaud's Google→token exchange
endpoint ourselves, bypassing the web.plaud.ai popup/opener handoff. Researched the public
ecosystem:
- **plaud-toolkit** `packages/core/src/auth.ts`: only `/auth/access-token` (POST, form
  `username`+`password`). No Google/OAuth/id_token endpoint at all.
- **Riffado / openplaud**: authenticates only via a **user-pasted bearer token** from
  web.plaud.ai DevTools; Google users are told to set a password via "Forgot Password."
  Documented endpoints are their *own* (`/api/plaud/connect|connection|sync`), not Plaud's.
- **Plaud's official OAuth API** is private-beta / waitlist — not usable.

Conclusion: there is **no publicly-known programmatic Google→JWT exchange endpoint**. The whole
ecosystem works around Google sign-in exactly via this app's existing fallbacks (paste token /
set password). Implementing option 2 would require reverse-engineering the exact POST
web.plaud.ai makes *after* GIS returns the id_token — which can only be captured from a
**working** Google login (i.e. the user's real system browser with DevTools), since our embedded
flow never delivers the id_token to web.plaud.ai in the first place (chicken-and-egg).

**Next step to unblock option 2:** user captures, in a normal browser, the request to
`api*.plaud.ai` that returns an `access_token` right after Google sign-in (path + payload shape,
token redacted). Then implement: popup grabs the `id_token` → Rust POSTs to that endpoint →
store JWT. Not coded yet — would be fabrication without the real endpoint.

Sources: [plaud-toolkit](https://github.com/sergivalverde/plaud-toolkit),
[openplaud/riffado](https://github.com/openplaud/openplaud),
[openplaud Plaud Connection APIs (DeepWiki)](https://deepwiki.com/openplaud/openplaud/6.4-plaud-connection-apis),
[Plaud OAuth API FAQ](https://support.plaud.ai/hc/en-us/articles/56061278749209-FAQs-for-Plaud-OAuth-API).

### Option 2 — UNBLOCKED (2026-06-08, captured via Playwright + curl against live API)
Captured the real Google-SSO flow by signing in through a Playwright browser and inspecting
network traffic, then verified each step with `curl` against the live API. **Full contract:**

**Step 1 — exchange Google id_token for a Plaud session.**
`POST https://api.plaud.ai/auth/sso-callback` (or `api-euc1` for EU), `Content-Type: application/json`:
```json
{"sso_from":"web","sso_type":"google","id_token":"<google id_token>","user_area":"GB"}
```
Response body has an **empty `access_token`** + a `token_id`, BUT the useful bits are in
`Set-Cookie`:
- `pld_ut=<JWT>` — HttpOnly, `Max-Age=86400` (24h). This is the **user token**.
- `pld_urt=<JWT>` — HttpOnly, `Max-Age=25920000` (~300 days), `Path=/auth/refresh-user-token`. Refresh token.

**Step 2 — use `pld_ut` as a normal Bearer.** VERIFIED: `Authorization: Bearer <pld_ut>` is
accepted by `/file/simple/web` and `/user/me`. So the existing bearer-based `PlaudClient` works
unchanged — we just need to source the token from the cookie instead of `/auth/access-token`.
(`pld_ut` is a standard HS256 JWT with `iat`/`exp`, so the existing `decode_jwt_expiry` works.)

**Step 3 — refresh.** `POST /auth/refresh-user-token` with header `Cookie: pld_urt=<pld_urt>`
returns a fresh `pld_ut` in `Set-Cookie` (verified). Lets us avoid daily re-login.

Notes:
- The `/auth/access-token-other-web` `{client_id:"frill"}` call returns a **Frill-scoped** token
  that is REJECTED by the main API ("invalid auth header") — red herring, do not use it.
- SSO email identity is `google-<sub>-<ts>@plaud.ai` (synthetic); `/user/me` returns the real
  display name + avatar. `user_area` came through as `GB`.
- Still need the Google `id_token` itself — captured in-app from the GIS flow (see below). The
  embedded-webview popup capture is the one piece that still needs an interactive GUI test.

### Option 2 — implementation (2026-06-08, `cargo check` clean)
Backend (fully verified against live API contract):
- `storage.rs`: `pld_urt` refresh token stored in keychain (`plaud-refresh-token`); cleared on logout.
- `plaud/auth.rs`: `login_with_google_sso(id_token, user_area, region)` → POST `/auth/sso-callback`,
  extracts `pld_ut`/`pld_urt` from Set-Cookie (helper `extract_set_cookie` takes last non-empty).
  `refresh_with_user_token()` → POST `/auth/refresh-user-token` with `Cookie: pld_urt`.
  `get_token()` now refreshes SSO sessions via the refresh token (falls back to existing token).
- `types.rs`: refresh buffer cut 30d → 5min (the 24h `pld_ut` would otherwise read as always-expiring).
- `state.rs`: `BrowserLogin` enum (`Jwt` vs `GoogleSso`) carried over the login oneshot channel.
- `browser_login.rs`: two id_token capture points in the watcher — (1) intercept the GIS
  credential on `gsi/transform` (`opener.postMessage`), (2) read the `id_token` from web.plaud.ai's
  own `/auth/sso-callback` fetch body. Either fires `plaudsync://sso?id_token=…&user_area=…`;
  `complete_google_sso` then runs the exchange. Window now closes after capture.

**PENDING interactive test:** whether either capture point actually fires inside the embedded
WKWebView (the GIS popup/opener behaviour is the unknown). Test via `npm run tauri dev`, attempt
Google login, then read `~/Library/Application Support/com.jameswhiting.plaud-sync/login-debug.log`
for `captured Google id_token` / `google sign-in complete`. If neither capture fires, the popup
isn't delivering the credential to a frame our script controls — fall back to reading the
`pld_ut` cookie from the webview, or the token-paste path.

**Original fix options (superseded by Option 2 above):**
1. *Most robust:* do Google sign-in in the real system browser and return via a `plaudsync://`
   OS deep link, where popups/opener/FedCM all work. Needs scheme registration (+ maybe a tiny
   hosted redirect page).
2. *Self-contained:* intercept the `id_token` at the popup's `form_post`/GIS credential and call
   Plaud's Google-auth backend endpoint ourselves to mint the JWT, bypassing web.plaud.ai's JS.
   Needs Plaud's google-login endpoint contract (check plaud-toolkit).
3. *In-app bridge:* inject the watcher into the popup and relay the credential popup→Rust→main
   window via `plaudsync://`. Most code, most fragile.
4. *Pragmatic:* keep Google as best-effort; steer users to the working email / paste-token paths.

## Findings

### Bugs (should fix)

- [x] **B1 — Opus downloads saved with `.mp3` extension.** `sync.rs:build_audio_path`
  always appends `.with_extension("mp3")`. In `sync_recordings` (sync.rs:80) the guard
  `if audio_path.extension().is_none()` is therefore always false, so the `ext` ("opus")
  returned by `download_audio_bytes` (client.rs:180) is discarded and opus bytes are written
  to a `.mp3` file. Files are mislabeled; players may still cope but the extension lies.
  Fix: decide the extension from the actual download (`ext`) rather than hard-coding `.mp3`
  in `build_audio_path`, or strip/replace the extension after the bytes+ext are known.

- [ ] **B2 — Browser/token logins break in the last 30 days before token expiry.**
  `auth.rs:login_with_jwt` stores fake credentials (`"jwt-user"`), so `get_credentials()`
  is always `Some`. Once `is_expiring_soon` becomes true (buffer is **30 days** —
  `TOKEN_REFRESH_BUFFER_MS`), `get_token` (auth.rs:23-28) takes the credential-refresh
  branch, `login_with_stored_credentials` finds no stored password, and returns
  `Err("No password stored.")` — *even though the token is still valid*. The valid-token
  fallback at auth.rs:30 is unreachable because the credential branch `return`s the error.
  Fix: don't treat JWT/browser logins as having refreshable credentials (e.g. skip the
  refresh branch when no password is stored, and fall through to returning the existing
  token), and reconsider the 30-day buffer (very large).

- [ ] **B3 — Manual URL-encoding corrupts non-ASCII credentials.**
  `auth.rs:urlencoding_helper` does `format!("%{:02X}", c as u8)` per `char`, which truncates
  any codepoint > U+00FF and emits Latin-1 bytes (e.g. `é` → `%E9`) instead of UTF-8
  (`%C3%A9`). Email/password with accents or non-Latin chars will fail to authenticate.
  Fix: use reqwest's form support — `.form(&[("username", &email), ("password", &password)])`
  — which encodes correctly and lets us delete the hand-rolled encoder.

### Repo hygiene (fix before committing)

- [ ] **H1 — 4.2 MB DMG + `.app` bundle would be committed.** `plaud-sync/dist/` holds
  `Plaud Sync_0.1.0_aarch64.dmg` (4.2 MB), `Plaud Sync.app/`, and `dist/.DS_Store`.
  `plaud-sync/.gitignore` only ignores `/*.dmg` and `/Plaud Sync.app` at the **project root**,
  not under `dist/`. The folder is fully untracked, so `git add plaud-sync` stages the binary.
  Fix: add `/dist/` (or `dist/*.dmg`, `dist/*.app`, `dist/*.msi`) to `plaud-sync/.gitignore`.
  CI rebuilds these anyway. Also confirm `**/.DS_Store` is ignored.

- [ ] **H2 — Unused dependency.** `thiserror = "2"` is declared in `Cargo.toml` but never
  used (no `#[derive(Error)]` anywhere). Remove it.

### Hardening / lower priority

- [ ] **S1 — CSP disabled.** `tauri.conf.json` sets `"csp": null`. Acceptable for a
  local-only frontend but a free hardening win — set a restrictive CSP for the main window.

- [ ] **S2 — Token-watcher injected into third-party auth domains.**
  `browser_login.rs:reinject_script` re-injects `TOKEN_WATCHER_SCRIPT` on every allowed
  domain (google.com, apple.com, facebook.com, microsoft.com, github.com, …). The script
  scans *all* localStorage/sessionStorage for any JWT-shaped string (`looksLikeJwt` = 3 dot-
  parts, len > 80). On a Google page this can grab a Google `id_token` rather than the Plaud
  token and ship it to `plaudsync://auth`. The token only ever goes to Plaud's own API (not
  exfiltrated externally), so the risk is *wrong-token fragility*, not data leak. Consider
  restricting storage-scanning to `*.plaud.ai` and only wrapping fetch/XHR elsewhere.

- [ ] **C1 — `Storage` cloned out of its Mutex defeats the lock.** Async commands do
  `state.storage.lock()?.clone()` then release the mutex; each clone holds the same
  `config_path` and does read-modify-write on the file. Concurrent ops (e.g. a running sync
  + `save_settings`) can clobber `config.json`. Low likelihood in a single-user app, but the
  Mutex is giving a false sense of safety. Also every getter (`get_token`, `get_settings`,
  `get_credentials`) re-reads and re-parses the file — minor.

- [ ] **C2 — `request()` region switch isn't persisted.** On a `-302` response, client.rs
  flips `self.region` for that client instance only; stored region is untouched, so every new
  client re-runs the redirect dance. Persist the corrected region to storage.

### Notes / not bugs
- `build_audio_path` "custom_prefix" arm is byte-identical to the default ("by_date") arm;
  they differ only via the editable prefix field. Redundant but harmless.
- `decode_jwt_expiry` default `exp = iat + 86400*300` produces a near-epoch timestamp when
  `iat` is also missing → token reads as perpetually expiring. Only bites malformed JWTs.
- Possible duration unit ambiguity (ms vs s) between API and `format_duration` — unverified,
  no sample data on hand.

## Recommended order
1. H1 (don't commit the 4 MB DMG) — do before any `git add`.
2. B1, B2, B3 (correctness).
3. H2, S1, S2, C1, C2 (cleanup / hardening).

## Cross-platform / parity
This is a standalone Tauri app, separate from the HiDock mic-trigger macOS/Windows apps, so
the BOTH-PLATFORMS PARITY.md rule does not apply here (Tauri builds both targets from one
codebase; CI already covers macOS + Windows).
