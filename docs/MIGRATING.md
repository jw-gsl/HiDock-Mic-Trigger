# Moving HiDock to a new Mac, or setting it up fresh

Written 2026-10-15, after a migration where every item below failed silently on
the new Mac. The first sign was a worse transcript: a 2-hour meeting came out
with one unnamed speaker, no voice names and no Refine suggestions.

The app now checks all of this itself. About a minute after launch, and every
6 hours, a **Setup needs attention** banner lists anything missing and how to
fix it. You can run the same check in Terminal:

```bash
cd ~/_git/hidock-tools/transcription-pipeline
.venv/bin/python3 transcribe.py doctor
```

For the move itself (what lives where, copy order over SSH, and checks before the old Mac is touched), see [MIGRATION-new-mac.md](MIGRATION-new-mac.md).

## What doesn't come across, and how to fix it

| What | Why it's missing | Symptom | Fix |
|---|---|---|---|
| **Nemotron token** | It's in the Keychain. Items saved by the app don't reliably survive Migration Assistant. | Speaker detection gets HTTP 401. (Now: it falls back to on-Mac detection, which is worse. Before: one speaker for the whole meeting.) | Models → Nemotron → paste the token from `~/.nemo-diar-token` on the Spark. |
| **Hugging Face token** | Keychain, as above. | Slow model downloads; pyannote unavailable. | Models → Hugging Face access. |
| **GitHub sign-in** | `gh`'s token is in the Keychain and becomes invalid. | "Transcripts aren't reaching GitHub". | Terminal: `gh auth login -h github.com`, then `gh auth setup-git`. |
| **Model files** in `~/HiDock/Speech-to-Text` | Not copied, or only partly. | TitaNet and ReDimNet show as not installed; naming and Refine stop. | Models → download each one. ReDimNet2-B6 now fetches its code too. |
| **ReDimNet2 code** (`Speech-to-Text/redimnet2-repo`) | It's a git clone beside the weights and is easy to miss. | "Could not load the redimnet2_b6 voice model". | Download ReDimNet2-B6 again from Models. |
| **Code-signing access** (developers only) | The Developer ID key arrives, but `codesign` isn't allowed to use it without a prompt. | Deploy fails: `errSecInternalComponent`, "User interaction is not allowed". | `security unlock-keychain ~/Library/Keychains/login.keychain-db`, then `security set-key-partition-list -S apple-tool:,apple:,codesign: -s ~/Library/Keychains/login.keychain-db` (asks for your login password once). |
| **Python packages** | The pipeline's `.venv` is rebuilt for the new Mac's Python. `onnxruntime` wasn't in `requirements.txt` until 2026-10-15. | TitaNet quietly falls back to crude MFCC voice matching. | `transcription-pipeline/setup-venv.sh`. It now ends with the setup check. |

## What does come across

Copy the whole of `~/HiDock`:
- the voice libraries (`Voice Library`, `Voice Library Candidates`);
- the review-feedback logs, recordings and transcripts;
- the publishing clone (`.transcripts-git`).

The app's preferences come across too. The old Mac's are kept in
`~/HiDock/migration-backup-*` if you need them.

## Only one Mac should run HiDock

**Quit HiDock on the old Mac before using the new one,** or at least turn off
its auto-download, auto-transcribe and GitHub publishing. Each Mac keeps its
own record of which recordings it has downloaded. While both run, both
transcribe every new recording and both publish it, and the two versions
clash on GitHub ("pull --rebase failed … CONFLICT (add/add)").

Each publish now records which Mac made it (a `HiDock-Machine:` trailer), and
the setup check warns when another Mac has published in the last 14 days. This
only spots a Mac running an app new enough to add the trailer.

If a clash has already happened: GitHub's copy is usually the one to keep.
Reset the clone with `git -C ~/HiDock/.transcripts-git reset --hard origin/main`,
then re-transcribe the recording on the new Mac once the checks pass.

## Checklist for a new Mac

1. Copy `~/HiDock` and the `hidock-tools` repo across, or let Migration Assistant do it.
2. Run `transcription-pipeline/setup-venv.sh`.
3. Build and install the app (see CLAUDE.md → Deploy). If signing fails, see code-signing access above.
4. In the app, go to **Models**: download any model shown as missing, and paste the Nemotron and Hugging Face tokens.
5. In Terminal: `gh auth login -h github.com` and `gh auth setup-git`.
6. Quit HiDock on the old Mac.
7. Click **Check again** on the setup banner, or wait for it to clear.

## Keeping a Mac up to date

A build installed from the code (this repo) updates itself from GitHub: **Check for Updates** shows your version, the latest release and the changes waiting on `main`, and **Update now** pulls them, installs any new Python packages and rebuilds (you approve the restart). The log is at `~/Library/Logs/hidock-self-update.log`. It won't update a checkout that isn't on `main`, has its own commits, or has uncommitted edits.

The download on the Releases page is a separate, standalone app with its own Python; it updates from the Releases page.

