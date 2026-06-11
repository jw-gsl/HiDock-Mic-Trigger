# Plaud Sync — self-update (macOS + Windows)
Research date: 2026-06-10
Branch: `fix/plaud-sync-login-hang-and-autosync`
Stack: Tauri 2.11.2, `tauri-plugin-updater` 2.10.1, `tauri-plugin-process` 2.3.1

## Goal
Let the installed Plaud Sync app detect new releases and update itself, on both
macOS and Windows, from the one Tauri codebase.

## How it works
- App checks the updater endpoint on launch (silent) and via a **Check for
  updates** button in Settings → Updates. Found updates show a banner with
  **Download & install**, then **Restart now**.
- Endpoint (`src-tauri/tauri.conf.json` → `plugins.updater.endpoints`):
  `https://github.com/jw-gsl/HiDock-Mic-Trigger/releases/download/plaud-sync-latest/latest.json`
  — a **fixed rolling tag** so it never collides with the repo's HiDock releases
  (GitHub's repo-wide "latest" would be ambiguous here).
- `release-plaud-sync.yml` (manual `workflow_dispatch`) builds + signs mac/windows
  bundles via `tauri-action`, generates `latest.json`, and publishes to the
  `plaud-sync-latest` release. Updates are verified with a minisign signature
  (Tauri's own, separate from Apple/Windows code signing).

## IMPORTANT — first release is manual
v0.1.0 has **no** updater, so it cannot self-update. v0.2.0 (this branch) is the
first updater-enabled build and must be **installed manually**. From v0.2.0 →
v0.3.0 onward the app updates itself.

## Owner one-time setup (required before the release works)
You opted to generate + manage the key yourself. Steps:
1. Generate a keypair (keep the private key safe, never commit it):
   ```
   cd plaud-sync && npm run tauri signer generate -- -w ~/.tauri/plaud-sync.key
   ```
2. Put the **public** key into `src-tauri/tauri.conf.json` →
   `plugins.updater.pubkey` (replaces `REPLACE_WITH_TAURI_UPDATER_PUBLIC_KEY`).
   Commit that change.
3. Add repo secrets (Settings → Secrets and variables → Actions):
   - `TAURI_SIGNING_PRIVATE_KEY` — contents of `~/.tauri/plaud-sync.key`
   - `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` — its password (or empty)
4. Bump `version` in `src-tauri/tauri.conf.json`, `src-tauri/Cargo.toml`, and
   `package.json` for each release.
5. Run the **Release Plaud Sync** workflow (Actions tab → Run workflow).
   - `preflight` fails fast if the pubkey is still the placeholder or the secret
     is missing, so it can't ship un-signed.

## Completed (this branch)
- [x] Added `tauri-plugin-updater` + `tauri-plugin-process` (Cargo + npm), registered in `lib.rs`.
- [x] Capabilities: `updater:default`, `process:allow-restart`.
- [x] `tauri.conf.json`: `plugins.updater` endpoint + placeholder pubkey; version → 0.2.0.
- [x] `src/lib/updater.ts` wrapper; launch check + banner in `+page.svelte`; manual button in Settings.
- [x] `release-plaud-sync.yml` (tauri-action, signed, rolling `plaud-sync-latest` tag, merged latest.json, `max-parallel: 1`).
- [x] Version bumped to 0.2.0 across Cargo.toml / tauri.conf.json / package.json.
- [x] Verified: `cargo check`, `cargo test` (10 pass), `npm run check` (0 errors), `npm run build`.

## To verify after secrets are set
- [ ] Run the release workflow; confirm `plaud-sync-latest` has `.dmg`, `-setup.exe`,
  the updater bundles, `*.sig`, and `latest.json` with **both** platforms.
- [ ] Install v0.2.0 manually, then release a v0.2.1 and confirm the app offers + applies it.

## Notes / not done
- `build-plaud-sync.yml` (push/PR artifact build) is unchanged: base config has
  `createUpdaterArtifacts` off, so it needs no signing key and still passes. The
  release workflow flips it on per-build.
- macOS bundles are ad-hoc signed (no Apple notarization) — Gatekeeper friction
  on first manual install is unchanged by this work; the updater's minisign
  signature is independent of that.
