# Plaud Sync — Refinements
Date: 2026-06-08
Status after auth work: Google SSO login works end-to-end (id_token → /auth/sso-callback →
pld_ut bearer + pld_urt refresh), downloads work, dark mode follows system, login-window/popup
close fixed. See PLAN-plaud-sync-code-review-2026-06-08.md for the auth details.

## Requested refinements (from JW, 2026-06-08)
1. **UI density/polish** — currently "too big / AI-generated". Make it tighter, less generic.
2. **Match a real design** — JW mentioned BOTH the repo's HiDock mac app AND the logged-in
   Plaud web UI. These are different languages — NEEDS A DECISION (see Open Questions).
   - HiDock app = native macOS, dense table (caption fonts, `.secondary` meta, 8/4 padding,
     plain `List` with columns: Device/Status/Tagged/Recording/Created/Length/Size/actions).
   - Plaud web = modern SaaS (rounded, brand blue/teal, card/list, larger imagery).
3. **Auto-sync toggle** — when on, download new recordings automatically on a regular interval.
   Recommended interval: **15 min** (recordings aren't time-critical; balances freshness vs API
   load/battery). Implementation: a Tokio interval task in the backend gated by a setting, or a
   frontend `setInterval` calling `sync_now`. Backend task survives window focus changes — preferred.
4. **Click a recording → open its folder** with the mp3 + txt revealed. Use `open` crate
   (`open::that`) on the file's parent dir, or reveal-in-Finder. Need the per-recording path:
   reuse `build_audio_path` to compute it; add a `reveal_recording(id)` command.
5. **Search + filter recordings**; pagination question. Test account has 5 recordings
   (`data_file_total`). Recommendation: client-side search (filename) + filter (downloaded/new,
   has-transcript); virtualize or "show more" only if a user has hundreds. No server pagination
   for v1 — `/file/simple/web` returns the full list already.
6. **"Signed in as google-1032…@plaud.ai" is ugly.** `/user/me` returns a synthetic SSO email
   plus a real `nickname` ("James Whiting") and avatar. Fix: show nickname (+ avatar) instead of
   the synthetic email, or hide the line entirely for SSO accounts. EASY WIN.

## Open questions (gating)
- **Q1 Style direction:** HiDock-native-compact vs Plaud-web-look vs Hybrid (compact native
  density + Plaud brand accent). Determines the CSS rewrite scope.
- **Q2 Auto-sync interval default** (15 / 30 / 60 min / manual).

## Decisions (answered)
- Q1 Style: **Hybrid** (compact native density + Plaud blue accent).
- Q2 Auto-sync interval: **15 min** default (options 15/30/60/180).

## Plan — DONE 2026-06-08 (compiles clean; dev server hot-reloaded; awaiting JW test)
- [x] Hybrid compact restyle: `app.css` — native-first font stack, base 13.5px, tighter
      paddings/radii/shadows, smaller buttons (+`.btn-sm`), `.segmented.sm`, lighter shadow.
- [x] Recording list (`SyncView`): search box + All/New/Saved filter, compact rows, row click →
      `reveal_recording`, listens for `auto-sync-complete` to refresh, shows name not email.
- [x] Backend `reveal_recording(recording)` — computes path via `build_audio_path`, reveals the
      mp3/opus in Finder (`open -R`) / Explorer (`/select,`) cross-platform; opens folder if not
      downloaded yet.
- [x] Backend auto-sync: `sync::auto_sync_loop` spawned in `lib.rs` setup; gated by `auto_sync` +
      `auto_sync_minutes`; re-reads settings each tick; emits `auto-sync-complete`/`-error`.
- [x] Settings UI: auto-sync toggle + interval selector; **2-column grid layout** (JW request).
- [x] Name: `display_name` stored on login (nickname from `/user/me`); `AuthStatus.name`;
      `get_auth_status` now async and **backfills** the name for pre-existing sessions.
- [x] Dark mode preserved (all new styles use the CSS vars).

## Answered questions
- Downloaded-state tracking: filesystem is source of truth (`mark_downloaded_status` checks
  mp3/opus existence) — no separate local store needed. Caveat: changing folder/filename
  settings changes the computed path so old files won't be recognised.
- Windows parity: plaud-sync is ONE Tauri codebase → both macOS+Windows from same source (unlike
  hidock-mic-trigger/Windows-App). PARITY.md doesn't apply. Only OS-integration (reveal) is
  cfg-gated. Google-login webview flow is platform-agnostic but untested on WebView2 (Windows).

## Plaud web UI design analysis (from JW screenshots plaud1–4, 2026-06-08)
Reference for matching the logged-in Plaud aesthetic:
- **Layout:** left sidebar (light grey) with logo + workspace + "+ Add audio" + line-icon nav
  (Search/Home/Ask/Explore) + "All files/Unfiled/Trash" with counts + upsell at bottom; white
  content pane; airy.
- **Flat, not boxy:** rows separated by whitespace/thin dividers — NO drop-shadow cards, minimal
  borders. This is the antidote to the "AI-generated" feel.
- **Primary button is BLACK** (e.g. "Generate"), white text. Secondary = white + subtle border.
  Brand colour is understated (a purple "Go Unlimited" pill is the only strong colour).
- **Type:** clean system/Inter; titles near-black normal weight; metadata small grey,
  right-aligned (duration · datetime) on the Home "Recent files" list.
- **Rounded** ~8–10px, subtle. Thin lucide-style line icons.
- **Tokens (light):** bg #fff, sidebar ~#f6f6f7, text ~#1a1a1a, meta ~#8a8a8a, border ~#ececec,
  hover/selected ~#f0f0f2, primary btn ~#0b0b0c. **Dark = inverted** (dark bg, light text,
  near-white primary button with dark text).

### Restyle plan
- [x] Adopt Plaud visual language on current layout: black primary button (`--accent`), flatten
      cards (drop shadow/heavy borders), airy borderless recording rows with right-aligned
      meta+status, lighter surfaces, dark-mode inverted accent. (this pass)
- [ ] Optional follow-up: full left-sidebar layout (the signature Plaud structure) — bigger
      restructure; offer to JW separately.

## Icon (final) + restyle status (2026-06-08)
- App icon: Plaud's real mark is a white angular rounded-triangle on a near-BLACK tile (per JW's
  plaudicon5 dock screenshot). Final icon = **two overlapping angular rounded triangles in our
  blue→purple gradient tile** — Plaud's *shape* language, our *own* colour (deliberately distinct,
  not a copy). Generated via ImageMagick → `tauri icon` (icns/ico/pngs); in-app top-left logo (SVG)
  matches.
- Restyle: applied Plaud-flat tokens — **black primary button** (`--accent`, inverts to near-white
  in dark), removed card shadows (`--shadow: none`), airier borderless recording rows with subtle
  dividers + hover. Dark mode inverted via `--accent`.

## NEW request (2026-06-08): background-tool behaviour
- [ ] **Auto-start at login** (macOS + Windows) — use `tauri-plugin-autostart` (handles macOS
  LaunchAgent + Windows registry Run key). Setting toggle "Start at login".
- [ ] **Start minimized/hidden** — window starts hidden so it runs as a background sync tool.
  Needs a **system tray icon** (show/quit) so a hidden window can be reopened — recommend adding
  the tray as part of this. Setting toggle "Start minimized".

## Still queued (next focused tasks)
- [x] App icon: two-cards-in-gradient mark via ImageMagick → `tauri icon` (icns/ico/pngs);
      matched as top-left logo.
- [ ] CI/CD: match HiDock's GitHub build/deploy sequence + tests (research → plan → execute).

## Notes
- Playwright screenshot of the live Plaud web UI timed out (5s cap, heavy SPA). Will retry /
  use accessibility snapshot when building the Plaud-styled option.
