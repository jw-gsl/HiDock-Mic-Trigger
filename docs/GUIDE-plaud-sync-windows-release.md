# Plaud Sync — Windows Release & Install Guide
Last updated: 2026-06-08

Plaud Sync is a single **Tauri 2** app (SvelteKit UI + Rust backend) that builds for
**macOS and Windows from the same codebase** — there is no separate Windows project. This guide
covers how to **produce** a Windows build and how an end user **installs and runs** it.

---

## Part A — Producing the Windows build (for you, the releaser)

You have two options. **Option 1 (CI) is recommended** — you don't need a Windows machine.

### Option 1 — GitHub Actions (recommended, no Windows PC needed)

The workflow `.github/workflows/build-plaud-sync.yml` has a `build-windows` job that runs on a
GitHub-hosted Windows runner and produces the installer as a downloadable artifact.

1. Push your changes (the workflow triggers on pushes touching `plaud-sync/**`, on PRs, and via
   manual **workflow_dispatch**).
2. On GitHub → **Actions** → **Build Plaud Sync** → open the latest run.
3. Download the **`Plaud-Sync-Windows`** artifact (a `.zip`). Inside is the Windows installer
   (a `.msi` and/or a `*-setup.exe`).
4. Send that installer file to the Windows user (see Part B).

To trigger manually without a code change: Actions → Build Plaud Sync → **Run workflow**.

### Option 2 — Build locally on a Windows machine

Prerequisites on the Windows PC:
- **Node.js 18+** (https://nodejs.org/)
- **Rust** (https://rustup.rs/)
- **Microsoft C++ Build Tools** (Desktop development with C++)
- **WebView2 Runtime** — preinstalled on Windows 11 and current Windows 10; the installer also
  bootstraps it if missing.

Then, from a terminal (Git Bash or PowerShell):

```bash
cd plaud-sync
./build-installer.sh          # or: npm run tauri build
```

Output appears in:
- `plaud-sync/dist/` (the `build-installer.sh` copy), and/or
- `plaud-sync/src-tauri/target/release/bundle/` — `msi/*.msi` (WiX) and `nsis/*-setup.exe` (NSIS).

Either the `.msi` or the `-setup.exe` can be sent to the user; the NSIS `-setup.exe` is usually
the friendliest.

---

## Part B — Installing & running (for the Windows end user)

### Install

1. Double-click the **`Plaud Sync ...-setup.exe`** (or the `.msi`).
2. **SmartScreen warning** — because the build is **not code-signed**, Windows may show
   *"Windows protected your PC"*. Click **More info → Run anyway**. (This is expected for an
   unsigned internal app; it's safe to proceed with a build you trust.)
3. Follow the installer prompts. If WebView2 isn't already present, the installer fetches it.
4. Launch **Plaud Sync** from the Start menu (or desktop shortcut).

### First run

1. **Sign in** — click **Continue with Plaud** and sign in through the Plaud website (Google,
   Apple, email, etc.), or use the email / paste-token options.
   > Note: the in-app Google sign-in is verified on macOS; on Windows it runs in the Edge
   > **WebView2** engine and hasn't yet been tested end-to-end. If it misbehaves, use the
   > **Token** option (paste a token from `web.plaud.ai` DevTools) or **email/password**.
2. **Choose a save folder** when prompted (or in **Settings**).
3. Click **Sync** to refresh the recordings list, then **Download** to save the audio (+ optional
   transcript `.txt`). Click a downloaded row to reveal the file in **File Explorer**.

### Run it as a background sync tool (optional)

In **Settings**:
- **Auto-sync new recordings** + interval (e.g. 15 min) — downloads new recordings automatically.
- **Start at login** — launches Plaud Sync automatically when you sign in to Windows.
- **Start minimized** — opens minimized so it runs quietly in the background.

### Work on the transcripts in Claude (the point of syncing)

The goal of syncing is to get your recordings **and their transcripts** into a local folder Claude
can read directly — no more Plaud → email → upload → Claude round-trip. Plaud Sync saves each
transcript as a `.txt` right next to the audio (enable **Download transcript** in Settings; turn
off **Create info .txt** if you only want the transcript text).

**Set it up once:**
1. In **Settings**, set the **save folder** to a dedicated location you'll point Claude at, e.g.
   `C:\Users\<you>\PlaudTranscripts` (Windows) or `~/PlaudTranscripts` (macOS).
2. Enable **Download transcript** and **Auto-sync** so new transcripts land in that folder
   automatically as you record.

**Then work on them in Claude:**
- **Claude Code (recommended):** open a terminal in the sync folder and run `claude`. It reads the
  `.txt` transcripts in place — ask it to summarise a meeting, extract action items, draft a
  follow-up email, compare two calls, etc. Because the files are local, there's no upload step.
  ```bash
  cd ~/PlaudTranscripts      # Windows: cd %USERPROFILE%\PlaudTranscripts
  claude
  # e.g. "Summarise today's transcripts and list any action items with owners."
  ```
- **Add it as a working folder:** point your editor's Claude integration / Claude Code at that
  folder so it's always available alongside your other work.
- **Tip:** the **By Date** folder layout keeps each day's transcripts together, which makes
  "summarise this week's calls" style prompts easy to scope.

Net effect: record on your Plaud device → it auto-syncs to the folder → the transcript is already
sitting where Claude can use it.

### Update

Download the newer installer and run it over the top (it upgrades in place). Your settings,
sign-in, and downloaded files are preserved.

### Uninstall

**Settings → Apps → Installed apps → Plaud Sync → Uninstall** (or via Control Panel). Downloaded
recordings in your chosen folder are **not** removed.

---

## Notes & caveats
- **Unsigned build:** no Authenticode signature, so SmartScreen/antivirus may warn. For wider
  distribution, sign the installer with a code-signing certificate (configure in
  `src-tauri/tauri.conf.json` → `bundle.windows`).
- **WebView2 required:** present on modern Windows; the installer bootstraps it otherwise.
- **Data locations (Windows):** config/cache under
  `%APPDATA%\com.jameswhiting.plaud-sync\` (tokens + cached list); password/refresh token in
  **Windows Credential Manager**; recordings in your chosen folder.
- **Same features as macOS:** one codebase → parity by construction. The only platform-specific
  bit is "reveal file" (File Explorer on Windows, Finder on macOS).
