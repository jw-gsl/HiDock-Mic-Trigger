# Plaud Sync — APAC region, robust region redirects, browser-shaped headers, shadow-account warning

Research date: 2026-06-11
Sources:
- Local app: `hidock-tools/plaud-sync` (Tauri 2 + Svelte, Rust backend in `src-tauri/src/plaud/`)
- Comparison repo: [riffado/riffado](https://github.com/riffado/riffado) — self-hosted Next.js Plaud companion (AGPL-3.0)
  - `src/lib/plaud/auth.ts`, `servers.ts`, `fetch.ts`; `src/app/api/plaud/auth/{send-code,verify,connect-token}/route.ts`

## Current State
After comparing our auth stack against riffado's, four improvements were adopted. Our auth was already
the more capable of the two for a desktop app (real WebView SSO + `pld_ut`/`pld_urt` refresh-token
rotation — things a server-side app can't do), so most of riffado's auth wasn't an upgrade. The four
items below were genuine gaps/hardening. All implemented on branch
`feature/plaud-sync-region-and-headers`.

## Findings (gaps vs riffado)
1. **APAC region missing.** We only handled `us` (api.plaud.ai) and `eu` (api-euc1.plaud.ai).
   Riffado also supports APAC `api-apse1.plaud.ai`. Our `region_from_api_host()` returned `None`
   for APAC, so an APAC account hitting a region redirect could not sign in at all.
2. **Region redirect collapsed to a 2-value enum.** We read `data.domains.api` from Plaud's `-302`
   response but mapped it through a us/eu enum, silently dropping anything unrecognised. Riffado
   trusts the redirect host directly (up to 3 hops). Our enum approach breaks on any new region.
3. **Bare API requests.** Our list/download/refresh calls sent only `app-platform: web`. Riffado
   sends a full Chrome fingerprint (UA, `sec-ch-ua*`, `origin`, `referer`, `accept-language`) on
   every Plaud request — friendlier to Plaud's anti-bot filtering. Our *login* is genuine (real
   WebView) but our *programmatic* calls were naked.
4. **Shadow-account gotcha.** Riffado's `connect-token` route documents (their issue #65) that
   OTP/email login can sign you into an email-only account *separate* from a Google/Apple-linked
   account with the same email — recordings appear empty. Our WebView SSO sidesteps this, but our
   email/password fallback can hit it.

## Completed (2026-06-11)
- [x] **#1+#2 — Robust region resolution** (`src-tauri/src/plaud/types.rs`)
  - `base_url()` now returns `String`, knows `apac` → `api-apse1.plaud.ai`, and will trust a stored
    full `https://…plaud.ai` base verbatim (future regions need no code change).
  - New `is_valid_plaud_api_url()` — HTTPS + `*.plaud.ai` host guard (SSRF-style, mirrors riffado).
  - New `region_from_redirect()` — resolves `data.domains.api` to a friendly key for the 3 known
    hosts, the full validated URL for unknown Plaud regions, or `None` for non-Plaud hosts.
  - Replaced `region_from_api_host()` (deleted from `auth.rs`) at all call sites: password mismatch,
    SSO mismatch (`auth.rs`), and the in-client `-302` handler (`client.rs`).
  - `client.rs` redirect handler now only retries when the base URL actually changes (loop guard).
  - JWT paste validation loop (`commands.rs`) now tries `["us", "eu", "apac"]`; error text updated.
  - Browser-login injected JS (`browser_login.rs`) now detects `apse1` → `apac`.
- [x] **#3 — Browser-shaped headers** (`types.rs::browser_headers()`)
  - Applied to all Plaud API calls: `request()` and `/file/download` fallback (`client.rs`), and the
    three auth POSTs — access-token, sso-callback, refresh-user-token (`auth.rs`).
  - Deliberately NOT applied to the presigned MP3 download URL (CDN/S3 signed URL — extra headers
    could break the signature). `Accept-Encoding` omitted so reqwest handles decompression.
- [x] **#4 — Shadow-account warning** (`src/lib/components/LoginView.svelte`)
  - Hint under the email/password form steering Google/Apple signups to Browser sign-in.
- [x] Unit tests added for `base_url`, `is_valid_plaud_api_url`, `region_from_redirect`
  (`types.rs`). `cargo test --lib` → 16 passed. `npm run check` → 0 errors/0 warnings.

## Rejected / Not Applicable
- **Proxy rotation (Webshare)** — riffado's `fetch.ts` rotates HTTP proxies on 403/407. Scale/anti-block
  concern for a multi-tenant host; irrelevant for a single-user desktop app.
- **Server-side SSRF allowlist as a security boundary** — riffado needs it because the *client* sends
  `apiBase` to *their server*. On desktop the user is the only party. We kept the cheap host check
  (`is_valid_plaud_api_url`) anyway, now that we trust redirect hosts.
- **OTP email-code login** (`otp-send-code`/`otp-login`) — a no-password fallback, but our WebView SSO
  is better UX and avoids the shadow-account trap. Not worth adding.

## Notes
- Both apps depend on the unofficial `api*.plaud.ai` surface — shared fragility if Plaud changes it.
- No region UI selector exists (region is auto-detected); APAC therefore needs no frontend change
  beyond the warning hint.
