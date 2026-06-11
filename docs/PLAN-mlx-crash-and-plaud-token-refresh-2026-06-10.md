# MLX transcription crash + Plaud cloud token refresh
Research date: 2026-06-10
Sources: crash report (Python 3.13, pid 32221), `~/Library/Logs/hidock-menubar.log`,
parakeet-mlx 0.5.1 / mlx 0.31.1, `plaud-sync/src-tauri/src/plaud/{client,auth}.rs`,
`usb-extractor/plaud_client.py`, `hidock-mic-trigger/Sources/PlaudAuth.swift` + `AppDelegate.swift`

## Current State
Two reported faults, investigated together:
1. **Python crashed (SIGABRT)** while transcribing — user suspected recording length.
2. **Plaud device connected but new 10-Jun recordings not showing.**
3. **Migrate any plaud-sync app fixes** into the hidock Plaud path.

## Findings

### 1. Crash = unchunked MLX eval on multi-hour audio  ✅ ROOT CAUSE + FIX VERIFIED
- Crash thread is `com.Metal.CompletionQueueDispatch` →
  `mlx::core::gpu::check_error(MTL::CommandBuffer*)` → `__cxa_throw` → `std::terminate`
  → `abort()`. A GPU command buffer failed at completion; MLX raises a C++ exception
  from a Metal completion-handler thread, which no Python frame can catch → process aborts.
- The faulting module is `core.cpython-313-darwin.so` + `libmlx.dylib` = **`mlx.core`**,
  i.e. the **Parakeet ASR** step (NOT sortformer diarization — that already windows to 300s).
- The transcribe queue at crash time held **3+ hour recordings**: Rec98 12,100s (3h22m),
  Rec97 11,763s, Rec94 11,099s. Each transcribe subprocess died within ~7s and the queue
  relaunched the next → crash-loop.
- `shared/asr_parakeet.py` called `model.transcribe(path)` with **no `chunk_duration`**, so a
  whole 3 h file became one giant MLX eval → Metal command-buffer overflow → the abort above.
  The user's "is it length of recording" hypothesis is **correct**.

**Fix (done, Python-only — no rebuild):** pass `chunk_duration`/`overlap_duration` to
`model.transcribe()`, defaulting to the parakeet-mlx CLI values (120 s / 15 s) and honouring
the same `PARAKEET_CHUNK_DURATION` / `PARAKEET_OVERLAP_DURATION` env vars (`0` disables).
parakeet-mlx stitches the chunks back into one `AlignedResult` with global timestamps.

**Verified:**
- Rec99 (628 s): 129 segments, last ts 628.8 s, 7.8 s.
- Rec98 (12,100 s — the file that crashed): **1008 segments, last ts 12100.8 s, 95 s, no SIGABRT.**
  Whole file processed, timestamps global and correct.

Windows parity: N/A — Windows ASR is whisper.cpp, not parakeet-mlx; this crash is Mac-only.

### 2/3. Plaud "0 recordings" = stale, never-refreshed cloud token  ⏳ ROOT CAUSE, FIX PENDING APPROVAL
These two asks are the **same issue**. Timeline in the log (UTC; crash was 22:20 PDT = 05:20 UTC):
- `05:15:02 Storage[Plaud]: 7 files … Sync: Plaud not connected: Plaud network error:
  [Errno 8] nodename nor servname provided, or not known` — a **transient DNS failure**
  during wake-from-sleep (crash report: "Time Since Wake: 411 s"). DNS resolves fine now
  (`api.plaud.ai` → 104.18.7.192, HTTP 301), so the DNS error itself is environmental, not a bug.
- `05:18:20 Storage[Plaud]: 0 files … renderSyncStatus[Plaud]: empty recordings
  (connected=true), preserving last-known 7 rows` — listing **succeeded** but returned **0
  recordings** (down from 7). A successful-but-empty `data_file_list` is the signature of an
  **expired Plaud `pld_ut` user token**, not a network problem.

Why the token goes stale:
- `pld_ut` is a short-lived JWT (has `exp`). The working **plaud-sync Rust app refreshes it**:
  `client.request` → `auth.get_token()` → `is_expiring_soon()` (decodes JWT `exp`) → if near
  expiry and a `pld_urt` refresh token exists, `refresh_with_user_token()` POSTs
  `/auth/refresh-user-token` for a fresh `pld_ut` (and saves the rotated `pld_urt`).
- The **hidock app never refreshes**:
  - `hidock-mic-trigger/Sources/PlaudAuth.swift` captures `pld_ut`+`pld_urt` at login, stores
    them, and passes them to the extractor as `PLAUD_ACCESS_TOKEN`/`PLAUD_REFRESH_TOKEN`.
    No expiry check, no `/auth/refresh-user-token` call anywhere in Swift.
  - `usb-extractor/plaud_client.py` receives the refresh token but **discards it**
    (`token, _refresh, region = _get_auth(...)`). `refresh_user_token()` exists at line 114 but
    is **dead code** — never called from `list_recordings`/download. `_request_json` only handles
    `status == -302` (region redirect), not expiry.
- Net: hidock uses the login-time `pld_ut` until it expires, after which the cloud returns an
  empty list → "connected, 0 recordings" → new 10-Jun recordings (and the old 7) disappear.
  (Note: 10-Jun recordings Rec97/Rec98 were downloaded from the **P1 USB device** fine — the
  gap is specifically the **Plaud cloud account** path.)

**Confidence:** high on the architectural defect (plaud-sync refreshes, hidock does not, and
the empty-success matches token expiry). Not 100% live-confirmed — a live authenticated probe
needs the Keychain token, which was intentionally not accessed.

## Plan (Plaud token refresh — the migration item)
Token store is owned by Swift (Keychain); Python runs stateless per subprocess and `pld_urt`
**rotates** on refresh, so a Python-only refresh that doesn't persist the new `pld_urt` would
break after one rotation. Faithful port of the Rust design, refresh in one place + persist:

- [ ] **Python (`plaud_client.py`)**: add `decode_jwt_expiry()` + `is_expiring_soon()`
      (mirror `auth.rs`); before list/download, if `pld_ut` is expiring and `pld_urt` present,
      call existing `refresh_user_token()` and use the fresh token. Emit the new
      `pld_ut`/`pld_urt` in the `plaud-status` JSON (e.g. `refreshedTokens`).
- [ ] **Swift (`AppDelegate.swift` + `PlaudAuth.swift`)**: when the extractor returns
      `refreshedTokens`, persist them to `PlaudAuthStore` so the next call uses the fresh token
      and the rotated refresh token isn't lost. **Requires xcodebuild — ask first.**
- [ ] **Windows parity** (`Windows-Script/extractor.py` + `Windows-App/`): same refresh wiring.
- [ ] Update `PARITY.md`.

NOT migrated from `2c0e206`: the login-hang fix (wry/WebKit cookie-poll — Tauri-only) and the
self-updater (Tauri plugin) don't apply to the hidock Swift app. The auto-download cadence
change is a separate, optional UX item.

## Completed
- [x] Diagnosed crash → unchunked MLX eval on long audio.
- [x] Fixed `shared/asr_parakeet.py` with chunking; verified on the 3h22m file that crashed.
- [x] Diagnosed Plaud "0 recordings" → missing token refresh vs plaud-sync.

## In Progress
- [ ] Plaud token-refresh implementation (pending user go-ahead; touches Swift + needs rebuild).
