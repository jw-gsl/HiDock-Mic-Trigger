# HiDock app: Plaud shows "connected" but no recordings

Investigation date: 2026-06-15
Area: `usb-extractor/plaud_client.py` (Plaud cloud backend for the macOS app)
Related: the same region bug was fixed in `plaud-sync` (PR #15-era work).

## Symptom
In the HiDock desktop app, a paired Plaud account shows **Connected** but lists
**0 recordings** on every refresh. `~/Library/Logs/hidock-menubar.log`:
```
Auto-connect: Plaud connected (0 recordings)
renderSyncStatus[Plaud]: empty recordings (connected=true), preserving last-known 0 rows
```
The same account shows its recordings fine in the standalone `plaud-sync` app.

## Root cause (confirmed with evidence)
The stored session is **dead**, but the extractor reports it as an empty account:

1. The stored access token (`pld_ut`) **expired ~6 days ago** (verified by decoding
   the JWT `exp` from the Keychain session; region was correctly `us`).
2. A refresh token is present, so `_ensure_fresh_token` attempts a refresh — but
   `/auth/refresh-user-token` returns **HTTP 401** (the refresh token is no longer
   valid; likely rotated-but-not-persisted, or invalidated by repeated fresh logins
   to the same account elsewhere).
3. On refresh failure the code **fell back to the expired access token** and called
   `/file/simple/web`. Plaud answers an expired token with an **empty HTTP 200**
   (not a 401), so `list_recordings` returned `[]` *without raising*.
4. Because nothing raised, `status_payload` set **`connected = true`** with 0
   recordings — masking a signed-out session as an empty account.

`plaud-sync` works for the same account simply because it holds fresh tokens.

## Fixes (this PR — `usb-extractor/plaud_client.py`)
- **Masking bug (primary):** `_ensure_fresh_token` now raises a signed-out
  `PlaudError` when the access token is expired *and* it couldn't be refreshed
  (no refresh token, or refresh failed). The message contains **"not signed in"**,
  which `AppDelegate.swift` (line ~4652) maps to the signed-out state → the app
  prompts re-login instead of showing "connected, 0 recordings".
- **401 → signed-out:** any HTTP 401 from the API is now surfaced as the signed-out
  message rather than a raw "HTTP 401".
- **Region robustness (ported from plaud-sync):** added APAC (`api-apse1`), a
  `*.plaud.ai` host guard (`_is_valid_plaud_api_url`), and `_region_from_redirect`
  so a `-302` follows the host Plaud returns (any region, validated) with a
  redirect-loop guard. Previously only us/eu were handled and an unrecognised
  redirect could fall through and return an empty `-302` body as `[]`.

## Verification
- Re-ran the live dead-session scenario: now raises `Plaud is not signed in: your
  session expired, please sign in again` (was: count = 0). Confirmed it maps to the
  app's signed-out path.
- Added `tests/test_plaud_client.py` (19 tests: host validation, region resolution,
  expiry detection, signed-out-on-expired). Full suite: **122 passed**.

## User remedy (immediate)
Sign out / sign back in to Plaud in the HiDock app to capture fresh tokens. The code
fix ensures future token death surfaces as a re-login prompt rather than silently
showing 0 recordings.

## Platform note
Plaud cloud is a **macOS-app-only** feature (`usb-extractor/plaud_client.py`); the
Windows app has no Plaud integration, so there is no Windows parity change.

## Deploy
Python-only change, but it ships inside the app bundle — reaching the installed app
requires an `xcodebuild` of `hidock-mic-trigger` (deferred; owner to trigger, since
the deploy script restarts the running app).
