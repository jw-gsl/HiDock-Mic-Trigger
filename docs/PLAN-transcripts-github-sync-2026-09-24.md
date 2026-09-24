# Publish transcript .md files to github.com/jw-gsl/Transcripts
Research date: 2026-09-24
Sources: `shared/transcript_history.py`, `hidock-mic-trigger/Sources/Views/TranscriptViewerView.swift`, `hidock-mic-trigger/Sources/AppDelegate.swift`

## Current State

- Transcripts live flat in `~/HiDock/Raw Transcripts/` (~1778 `.md` files, plus `_diarized.json`, `.srt`, raw ASR json).
- Versioning already exists but is **local-rollback only**: a bare repo `.hidock-transcript-history` beside the transcripts, committed-to *before* each speaker-mutating operation. Used by both Swift (`TranscriptHistory`, called from `TranscriptViewerView.swift` / `AppDelegate.swift`) and Python (`shared/transcript_history.snapshot()`, called from `transcribe.py` CLI paths).
- That history repo is deliberately "never audio, never voice-library data, **never a remote**" — it stores pre-change states including un-reviewed junk, and its commit messages are "Before …" snapshots.
- Target: a separate workflow that publishes only the `.md` files to `https://github.com/jw-gsl/Transcripts`, committing whenever a transcript changes (speaker names edited/added, rows reassigned, rewrites).

## Key decision: extend history repo, or separate mechanism?

**Separate mechanism, same trigger points.** Reject extending `.hidock-transcript-history`:

1. It's bare and history-only by design — its remote would become a backup of *pre-change* states, not published transcripts. Wrong semantics.
2. It commits json + srt + md; the publish repo wants md only.
3. Its commit identity (`HiDock local history <history@hidock.local>`) and "Before X" messages are rollback-flavoured.
4. Mixing a network remote into the rollback path couples user-facing edits to auth/network failures — the exact silent-failure class this module has already been bitten by (Rec79 Part 2, the non-bare init bug).

But it *is* an extension in the reuse sense: same git-resolution hardening (`git_path()` probing for a working git), same best-effort "never block the user's operation" philosophy, and the same call sites that already trigger snapshots become the triggers for publish-sync (fired *after* the write instead of before).

## Design

### 1. Local publish clone
- `~/HiDock/.transcripts-git/` — a normal (non-bare) clone of `github.com/jw-gsl/Transcripts`, holding **only** `.md` files in its working tree.
- Transcripts are *copied* in (not symlinked/hardlinked) so stray non-md files, the history dir, and raw json can never leak into the publish repo by accident.
- Alternative considered: `--git-dir/--work-tree` trick directly over `Raw Transcripts` with a `.gitignore` deny-all/allow-`*.md`. Rejected — one missed pattern publishes sensitive data, and the real working dir would contain the unrelated history repo.

### 2. Sync function (single shared implementation)
Python `shared/transcript_publish.py` with a CLI entry, mirroring `transcript_history.py`'s hardening:
```
sync(md_paths, reason) ->
  ensure clone exists + remote correct + git works   (reuse git_path())
  copy each md into clone (flush per-file, atomic write)
  git add -A; if clean -> done
  commit -m "<reason>" (identity: user's gh identity or "HiDock <transcripts@hidock.local>")
  git pull --rebase (remote is effectively single-author; rebase keeps it linear)
  git push
  on any failure: leave the commit local, record pending, never raise
```
- All state (pending count, last error, last success) in `~/HiDock/.transcripts-git/.hidock-publish-state.json` or simply derived from `git status`.
- Pull-rebase failure (real conflict — e.g. edited on GitHub web): keep local commit, set a pending flag the app surfaces; do not force-push.

### 3. Triggers (fire-after-write, debounced)
- **macOS app**: call the sync after each transcript rewrite that already calls `TranscriptHistory` — i.e. rename speaker, assign/reassign row, merge/split, confirm-meeting rewrite. Debounce ~30–60 s (or on window close / app terminate) so a burst of edits lands as one commit rather than 15.
- **CLI**: `transcript_publish.sync()` invoked from the same places `transcript_history.snapshot()` is called in `transcribe.py` (post-rewrite), plus a manual `python -m shared.transcript_publish --all` for backfill.
- **Menu command**: "Sync transcripts to GitHub now" in the menubar app for immediate/manual push.
- Commit message carries the same reason string the snapshot uses ("Renamed speaker X→Y", "Before reassigning…" becomes "Reassigned row to X" wording), so the GitHub history reads as an edit log.

### 4. Initial backfill
One-off run: copy all `.md`, commit as "Initial import", push. After that, per-change commits only.

### 5. Auth
- Prefer SSH remote (`git@github.com:jw-gsl/Transcripts.git`) using the existing key — no token handling in-app.
- Fallback: HTTPS + `credential.helper osxkeychain` with a `repo`-scoped PAT; app prompts once and stores via keychain, same pattern as other tokens in this repo.
- No token is ever written into app config/plist.

### 6. Safety rails
- Repo must be **private** (transcripts are company meetings). Verify `gh repo view` visibility before the first push; refuse to push to a public remote without explicit opt-in.
- Non-`.md` files are never copied — enforced by the sync function only ever taking `*.md` inputs.
- Kill-switch setting (`publishToGitHub = false`, default **off** until the user enables it) so nothing pushes before the user is ready.
- No network at push time → commit stays local, retried on next trigger.

## Completed
- [x] Confirmed current history-repo design and its call sites (Swift + Python)
- [x] 2026-09-24: `shared/transcript_publish.py` — sync + CLI + state, 9 tests in
      `shared/tests/test_transcript_publish.py` (file:// remote: first push,
      changed-only, no-op, offline-pending-retry, divergence-no-force, non-md
      rejection, --all sweep, status, unreachable remote)
- [x] 2026-09-24: Swift bridge `Sources/TranscriptPublish.swift` — 45 s debounced
      scheduler; hooked at `snapshotTranscriptArtifacts` (covers all viewer
      speaker edits via `saveTranscript`, recluster, rediarize, rematch,
      restore), re-scheduled on verified `rewrite-md` completion and
      rediarize success so slow pipeline writes are caught; flush on app
      terminate. Snapshot reasons "Before renaming X" → publish messages
      "Renaming X" via `AppDelegate.publishReason`.
- [x] 2026-09-24: menu "Transcripts on GitHub" — Auto-publish toggle (default
      **off**; refuses to enable if `gh` reports the repo PUBLIC),
      "Sync to GitHub Now", "Show Sync Status..."
- [x] 2026-09-24: CLI hook — `transcribe.py _rewrite_and_verify_md` +
      anchor-sweep writer call `publishing_enabled()` (reads the app's
      UserDefaults kill-switch via `defaults`, env override
      `HIDOCK_PUBLISH_TO_GITHUB`) then best-effort `sync()`
- [x] 2026-09-24: visibility verified — `gh repo view` reports
      `jw-gsl/Transcripts` PRIVATE, and it is currently **empty** (first push
      creates `main`)
- [x] Swift tests `Tests/TranscriptPublishTests.swift` (md-path derivation +
      reason mapping); full Swift suite 110 green, shared Python suite 800 green

## In Progress
- [ ] Backfill run (`python -m shared.transcript_publish --all` equivalent) —
      needs James's explicit go, since it pushes all ~1778 transcripts

## Deviation from decision 3 (2026-09-24, implementation)
- **Remote is HTTPS, not SSH.** `git@github.com` auth fails on this machine:
  no GitHub SSH key is provisioned (only a 1Password-sealed key for the
  `mini` host, and BatchMode clone was denied), while the repo itself pushes
  over HTTPS using gh's keyring token. HTTPS clone of Transcripts verified
  working. SSH remains available via `--remote git@github.com:jw-gsl/Transcripts.git`.

## Planned
- [ ] Deploy + live test of the menu flow (opt-in deploy per CLAUDE.md)
- [ ] Onboarding copy for the git-absent case (reuses `git_availability()` messaging)
- [ ] Windows/other machines publishing — still open (macOS-only for now)
- [ ] Possible future hook: fresh-transcribe writes intentionally not auto-published
      (un-reviewed until named); the --all sweep covers them

## Rejected / Not Applicable
- **Extending `.hidock-transcript-history` with a remote** — wrong repo semantics (pre-change states, json+srt, bare, rollback identity); see key decision.
- **git-lfs** — plain files; 1778 markdown files are tiny.
- **Pushing json/srt** — user asked for markdown only; json sidecars contain internal review/diarisation state.
- **Force-push / hard reset sync** — silently destroys any web-side edits; rebase-and-hold instead.
- **launchd background poller** — defer; app-triggered + menu command covers it. Revisit if CLI-only workflows need guaranteed pushes.

## Decisions (confirmed with James, 2026-09-24)
1. **Scope**: all transcript `.md` files (~1778, everything in Raw Transcripts).
2. **Cadence**: debounced auto-push after each edit burst (~30–60 s, one commit per burst), plus manual menu item.
3. **Auth**: SSH remote `git@github.com:jw-gsl/Transcripts.git`, existing key.
4. **Identity**: generic `HiDock <transcripts@hidock.local>` commit author.
5. **Contents**: markdown only — never json/srt/audio.

Still open: Windows app / other machines publishing, or macOS-only for now?
