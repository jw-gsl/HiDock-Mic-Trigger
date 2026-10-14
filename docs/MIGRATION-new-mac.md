# Installing / migrating HiDock tools to a new Mac

Pathway, order of operations and validation checks. Drafted 2026-10-14 from a real migration (old Mac -> new Mac, same username `jameswhiting`). Figures below are from that Mac and will differ elsewhere.

Nothing here deletes anything on the old Mac. Keep the old Mac intact until section 6 passes.

## 0. Principles
- **Same username on both Macs.** `~/.config/hidock/config.toml` and the import state hard-code `/Users/<name>/HiDock/...` absolute paths. A different username means rewriting them.
- **Move data, not models.** Speech and diarisation models are downloaded by the app (Models UI, `models.py download <key>`). Do not copy `Speech-to-Text`, `~/Library/Application Support/SpeechToText`, `FluidAudio` or `~/.cache/huggingface`.
- **Gated models need a Hugging Face token** (e.g. pyannote). The token lives in the macOS Keychain (`com.hidock.tools.huggingface`) and does not migrate. Re-enter it in the app.
- **Copy by content, not by name.** Check duplicates by hash before copying audio.
- macOS permissions (microphone, accessibility, etc.) are never migrated. Grant them again.

## 1. What lives where
| Data | Location | Migrate? |
|---|---|---|
| Audio | `~/HiDock/Recordings` (mp3 + `_recording_time.json`) | Yes |
| Older audio and transcripts | `~/Downloads/HiDock Files` | Only the unique mp3s (see 4). The old `.txt` transcripts and other files are optional: the app never reads them |
| Transcripts and pipeline outputs | `~/HiDock/Raw Transcripts` (`.md`, `.srt`, `_diarized.json`, `_asr.json`) | Yes. The app reads `_diarized.json` / `_asr.json` directly |
| Voice identity | `~/HiDock/Voice Library`, `Voice Library Candidates` | Yes, only copy |
| App state | `imported_recordings.json`, `skipped_transcriptions.json`, `feedback_history.json`, `Summaries`, `Summary Templates`, `.review-feedback` | Yes. Losing import state can trigger a full re-import |
| Config | `~/.config/hidock/config.toml` | Yes |
| Summaries (OneDrive) | `~/Documents/HiDock-Library` | Via OneDrive, not rsync |
| Models | `HiDock/Speech-to-Text`, `SpeechToText/models`, `FluidAudio/Models`, HF cache | **No. App downloads** |
| Transcript git mirror | `HiDock/.transcripts-git` (-> private `jw-gsl/Transcripts`) | No. Re-clone if wanted |

## 2. Install the app on the new Mac
1. Install prerequisites: Xcode, Homebrew, git, Tailscale, 1Password.
2. Clone the repo: `git clone https://github.com/jw-gsl/HiDock-Mic-Trigger.git ~/_git/hidock-tools`.
3. Build `hidock-mic-trigger` (see `hidock-mic-trigger/README.md`) and place the app in `/Applications`.
4. Set up the Python environment the app uses for its scripts.
5. Launch, grant permissions, enter the Hugging Face token, then download models from the Models UI.

## 3. Connect the Macs
Preferred: SSH + rsync from the new Mac or old Mac.
- Remote Login must be on for the receiving Mac (System Settings -> General -> Sharing). Managed Macs (Intune/CrowdStrike) may block it.
- `ssh-keygen -t ed25519` on the sending Mac, then `ssh-copy-id <user>@<new-mac-ip>`.
- Run `caffeinate -s` on the sending Mac for the duration.
- macOS ships `openrsync` (protocol 29). Use only basic flags (`-a --partial -n --stats`), or install GNU rsync via Homebrew on both.
- Fallbacks: external SSD (needs no admin rights), or a direct Thunderbolt/USB-C cable for a faster link (still needs Remote Login for rsync).
- Always dry-run first: add `-n --stats`.

Lessons from the reference run:
- **Address the receiving Mac by its Tailscale IP, not its LAN IP.** The LAN address changed after an OS update and every retry timed out until the IP was corrected.
- **`caffeinate -s` does nothing on battery.** Use `caffeinate -dimu -t <seconds>` on the receiving Mac, and plug it in. Drops show up as `unexpected end of file` / `poll: hangup on nonblocking write`, and rsync resumes cleanly with `--partial`.
- **Add `-o ServerAliveInterval=15` to the ssh used by rsync, and wrap each stage in a retry loop.**
- **Remote paths with spaces need escaping on the destination**, e.g. `host:"Downloads/HiDock\ Files/"`. Otherwise openrsync fails with `server receiver mode requires two argument`.
- **When hashing in a shell loop over ssh, use `ssh -n`.** Without it ssh swallows the loop's stdin and the loop silently stops after one file. macOS has no `shuf`; use `sort -R | head`.
- **Keep the OS update out of the copy window.** The new Mac rebooted for an update mid-copy.

## 4. Order of operations
Run from the old Mac, from `~`.

**Step 1: transcripts, voice libraries, state (about 1.5G, 10,909 files on the reference Mac)**
```
caffeinate -s rsync -a --partial --stats --exclude='.DS_Store' \
  --exclude=/Recordings --exclude=/Speech-to-Text --exclude=/.transcripts-git \
  HiDock/ <user>@<new-mac>:HiDock/
```

**Step 2: audio**
- `~/HiDock/Recordings` in full.
- From `~/Downloads/HiDock Files`: only mp3s whose content is not already in Recordings. On the reference Mac 1,387 of 1,407 mp3 (22.6G) were byte-identical to Recordings; 20 were unique (17 plus 3 name clashes, below).
- The app only reads `recordings_folder` from `config.toml` (`~/HiDock/Recordings`). It never reads `~/Downloads/HiDock Files`, so the unique mp3s must be copied **into Recordings** to be visible (17 on the reference Mac).
- Three Downloads mp3 shared a filename with a Recordings file but differed in content. Copying them into Recordings would overwrite or collide, so they go to `~/HiDock/Review-Name-Clashes/` for review by ear. Keep both versions.
- The remaining non-mp3 files (1,507 on the reference Mac, mostly older `.txt` transcripts, about 50MB) are **not migrated by default**. They have no counterpart in Raw Transcripts, so they are unique, but the app does not read them. Copy them only if you want that history kept (they were copied once on the reference run and then removed again, since they weren't wanted).
- Plaud duplicates: 67 mp3 in `PlaudRecordings` were identical to Recordings files.

**Step 3: config**
- Copy `~/.config/hidock/config.toml` and confirm its paths resolve on the new Mac.

## 5. Not part of this migration
IT-managed software (Intune, Company Portal, Automox, CrowdStrike, WARP) arrives through IT enrolment. Tokens (`~/.aws`, M365) are re-authenticated, not copied.

## 6. Validation (before the old Mac is touched)
- [ ] File counts match per folder, e.g. `find <dir> -type f ! -name .DS_Store | wc -l` on both Macs. Step 1 reference: 10,909 on both.
- [ ] Sizes match (`du -sh`).
- [ ] Spot-check hashes on a sample of audio (`shasum -a 256`) on both sides.
- [ ] App launches, grants permissions, token entered, each model shows installed in the Models UI.
- [ ] The app lists the migrated recordings and does **not** re-import them (import state honoured).
- [ ] Open several transcripts in the viewer; speaker labels render (confirms `_diarized.json` and the voice library).
- [ ] Voice Check works against the migrated Voice Library.
- [ ] Process one new recording end to end.
- [ ] Run the new Mac for a full working day before returning or wiping the old one.
