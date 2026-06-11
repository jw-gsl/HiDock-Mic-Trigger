# Plaud Sync — Apple ID SSO login
Research date: 2026-06-11
Branch: `feature/plaud-apple-sso`
Sources:
- Live Plaud web bundle: `https://web-static.plaud.ai/web3/js/app-initial-common.D-vZHs6u.chunk.js`
  (fetched 2026-06-11 via `curl --resolve web-static.plaud.ai:443:104.18.7.192`)
- `plaud-sync/src-tauri/src/browser_login.rs`, `src-tauri/src/plaud/auth.rs`
- Prior login-hang work: `docs/PLAN-plaud-sync-login-hang-and-autosync-2026-06-10.md`

## Goal
Apple ID SSO sign-in works end-to-end in the plaud-sync desktop app (embedded wry webview
loading `web.plaud.ai`).

## Current State
- The app opens `https://web.plaud.ai` in a wry WebviewWindow and lets the user sign in with
  whatever method Plaud's own page offers (Google, Apple, email, …).
- Token capture is **provider-agnostic via the `pld_ut`/`pld_urt` session cookies**, read from
  the webview after navigations settle (`read_session_cookie`). Plus a **Google-specific** bridge
  (synthetic `window.opener` + `gsi/transform` interception + XHR/fetch sniff of `/auth/sso-callback`)
  that exists because Google's popup→opener handoff is unreliable in the embedded webview.
- Apple domains (`apple.com`, `appleid.apple.com`, `idmsa.apple.com`) are allow-listed for
  navigation and popups. **There is no Apple-specific handling.**
- End-to-end SSO was never tested (per the 2026-06-10 plan).

## Findings (FACTS from Plaud's live bundle)
Plaud initialises Sign in with Apple as:
```js
window.AppleID.auth.init({
  clientId: "ai.plaud.services.plaud.note",
  scope: "name email",
  redirectURI: document.location.origin,   // https://web.plaud.ai
  usePopup: true
})
```
- **`usePopup: true`** ⇒ Apple opens a separate popup to `appleid.apple.com/auth/authorize`
  (`response_mode=web_message`) and returns the credential `{authorization:{code,id_token,state}, user}`
  to the **opener** via `postMessage`. Apple's JS SDK in the opener resolves `signIn()`, then Plaud
  posts the credential to its backend, which sets `pld_ut`/`pld_urt`.
- The native Google exchange (reference for the backend shape) posts to `/auth/sso-callback`:
  `{sso_from:"web", sso_type:"google", id_token, user_area}` and reads `pld_ut`/`pld_urt` from
  Set-Cookie (`auth.rs::login_with_google_sso`). Apple's `sso_type` is presumed `"apple"` and the
  exact field set (id_token vs code vs both) is **not yet confirmed** — to be captured live.

## Hypothesis
The single decisive unknown: **does wry/WKWebView deliver the Apple popup's `web_message`
postMessage to the opener (the main login window)?**
- If YES → Plaud's own SDK completes the exchange, `pld_ut` is set, and the existing cookie path
  already captures it. Apple SSO would already work (just needs verifying).
- If NO → the credential is trapped in the popup; needs a native bridge (capture in popup →
  native `/auth/sso-callback` with `sso_type:"apple"`), mirroring the Google fix.
Google's bridge was needed for GIS iframes (COOP-isolated), which is a different mechanism from a
plain `window.open` popup, so Apple's outcome is genuinely unknown until observed.

## Outcome — WORKING (verified live 2026-06-11 23:35)
Apple ID SSO login works end-to-end. Verified by `login-debug.log`:
`sso sign-in complete for apple-001080.…@plaud.ai (region=eu)` followed by
`auto-sync: 3 downloaded` — i.e. authenticated session actively syncing.

### What the live trace showed (the hypothesis question answered)
The Apple popup→opener handoff **works** in the wry webview: Apple's `web_message`
credential reached web.plaud.ai, which POSTed it to `/auth/sso-callback`. Our XHR/fetch
interceptor captured that request body. So the failure was never the popup — it was three
downstream gaps, each found from a real trace and fixed:

1. **Mislabelled provider.** The interceptor was Google-only and re-exchanged the Apple
   token with `sso_type:"google"` → backend rejected it.
   **Fix:** capture and **replay the exact JSON body** web.plaud.ai sends
   (`{id_token, sso_type:"apple", sso_from:"web", user_area}`) — provider-agnostic, no
   guessed fields. (`auth.rs::login_with_sso`/`sso_attempt`, watcher `finishSsoBody`,
   `BrowserLogin::Sso{body}`.)
2. **Region mismatch.** Account is EU; app posted to US → `200 {status:-302,"user region
   mismatch",data.domains.api}`. Code only checked HTTP status, not the in-body status.
   **Fix:** read in-body status and **auto-retry against the region the backend points to**
   (`region_from_api_host`, returns the effective region up the stack).
3. **New account (no link).** EU returned `200 {status:1, sso_id, email:null}` — recognised
   Apple identity, no linked Plaud account. The old flow closed the window immediately.
   **Fix:** treat as `SsoSession::NeedsRegistration`; **keep the webview open** so the user
   finishes sign-up, then capture the session via the cookie poll
   (`login_with_browser` registration loop + `complete_sso -> Ok(None)`).

### Completed
- [x] Branch `feature/plaud-apple-sso`; confirmed Plaud's Apple SDK config from live bundle.
- [x] Diagnostic instrumentation → one real sign-in → root-caused all three gaps from traces.
- [x] Implemented fixes 1–3 above (generalises Google/Microsoft SSO too — body replay is
      provider-agnostic).
- [x] Tightened logging (concise `sso-callback` summary, no token id in the shared debug log);
      removed investigation-only instrumentation.
- [x] `cargo check` clean, `cargo test` 10/10, `npm run check` 0 errors. Verified end-to-end live.

## Follow-up — removed the region dropdown (region now fully auto-detected)
Since the backend tells us the right region, the manual US/EU picker was redundant.
- **Email/password** (`password_attempt`): reads in-body status; on region mismatch retries
  against `data.domains.api` (or flips US↔EU), returns the effective region.
- **Token** (paste JWT): validates against US then EU and keeps whichever the API accepts.
- **SSO**: already auto-retries (above).
- Dropped the `region` arg from the three login commands / `api.ts`; removed the dropdown from
  `LoginView.svelte`. Each path starts at `us` and self-corrects. (`cargo`/`test`/`svelte-check`
  all green.) Note: email/password and token paths verified by compile + reasoning, not a live
  run (only Apple SSO was exercised end-to-end).

## Notes / Follow-ups
- `about:blank` and `js.stripe.com` sub-frame navigations are blocked by `is_allowed_login_url`
  during the flow; harmless here (sign-in still completes) but logged as WARN noise.
- New-account onboarding happens on web.plaud.ai inside the webview; the window stays open
  until the session cookie appears (10-min `LOGIN_TIMEOUT`).
- Windows app parity: this is the macOS Tauri `plaud-sync` app; no Windows equivalent of this
  webview login exists, so PARITY.md is unaffected.

## Rejected / Not Applicable
- Guessing the Apple sso-callback field set — replaced by replaying web.plaud.ai's exact body.
- Forcing `usePopup:false` / a native Apple bridge — unnecessary; the popup handoff works and
  the cookie path + body replay cover login and sign-up.
