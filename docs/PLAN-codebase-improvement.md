# Codebase Improvement Plan — Full Bug Audit
Research date: 2026-07-02
Sources: four parallel code-audit agents over `hidock-mic-trigger/Sources/`, `usb-extractor/`, `shared/` + `transcription-pipeline/` + `mcp-server/`, and `Windows-App/` + `Windows-Script/` + `mic-trigger/`; findings verified against the code before fixing. Branch: `fix/audit-bugfixes` (off `feature/formatted-cli-view`).

## Current State
A whole-codebase audit (2026-07-02) surfaced ~80 findings across all components. The
highest-impact confirmed bugs were fixed on `fix/audit-bugfixes` (see Completed).
The remainder are catalogued below by priority so nothing is lost. Test baseline
before and after fixes: 132 usb-extractor tests + 53 Windows-App tests passing;
Swift app builds clean.

## Findings (summary)
The audit's most important theme: **partial-transfer handling**. Several download
paths (HiDock USB, Plaud HTTP, volume copy) could leave a truncated file at the
final output path, where existence checks then treated it as a complete
recording forever. The worst chain: a USB timeout mid-download promoted a
partial MP3 to the final path; the next `download-new` cycle then used the
*local file's size* as the expected transfer length, re-downloaded only that
many bytes, and marked the recording fully downloaded — silently and permanently
truncating the audio. All links of that chain are now fixed.

Secondary themes:
- **Process lifecycle races** in the Swift app (trigger termination handler vs
  restart/stop, transcription cancel vs completion handler).
- **Pipe deadlocks** — reading subprocess output after `waitUntilExit` at four
  Swift call sites (>64 KB output hangs both processes).
- **Non-atomic state writes** across many Python modules (a crash mid-save can
  corrupt or wipe `embeddings.json`, `corrections.json`, transcripts, config).
- **Qt threading violations** throughout the Windows app (widgets and QSettings
  touched from worker threads).
- **JSON-contract breaks** — extractor commands that could print a traceback and
  no JSON, breaking the desktop apps' parsers.

## Completed (2026-07-02, branch `fix/audit-bugfixes`)

### usb-extractor (fixed by hand, verified)
- [x] `transfer_file_stream_to_path` no longer promotes partial temp files to
  the final path on stream timeout or early device terminator — it deletes the
  temp file and raises, so `download_one` records `last_error` and the file
  retries next cycle.
- [x] Status items now carry `deviceLength` (authoritative catalog size)
  separately from the display `length` (which prefers local file size for trim
  support). `download_new` passes `deviceLength` — never the local size — as the
  expected transfer length.
- [x] Plaud downloads stream to a `.downloading` temp file and rename only on
  success; short bodies (early EOF vs Content-Length) now fail instead of being
  marked downloaded.
- [x] Plaud `download-new` isolates per-file failures (mirrors the HiDock path):
  one failed recording no longer aborts the batch, kills the JSON output, or
  skips state saving. Failures land in an `errors` list and `last_error`.
- [x] Volume import: honors the `removed` flag (no more re-importing files the
  user removed); per-file try/except with an `errors` list; copies via temp
  file + rename; recursive-scan fallback so files in subdirectories can actually
  be imported (status listed them but import said "not found"); mtime rendered
  in local time, not UTC (same fix as Plaud `_date_parts`); scan no longer
  crashes if a file vanishes mid-scan (volume ejected).

### hidock-mic-trigger (Swift, fixed via agent, diff-reviewed)
- [x] Trigger process termination handler: identity guard + stop/restart race fix
  (orphaned trigger / phantom auto-restart after user stop).
- [x] Re-transcribe: `.completed` queue items reset to `.queued` (was a silent
  no-op for files transcribed in the same session).
- [x] Summary lookup matches `stem + " - "` prefix instead of `contains` (child
  rows of a merged recording no longer show the merged summary, and Remove no
  longer deletes the merged recording's summary).
- [x] Four pipe-deadlock sites (`loadVoiceTrainingData`, `refreshModelStatuses`,
  `openVoiceLibrary`, `buildTriggerBinary`) now read before/alongside wait, per
  the existing `refreshMeetingExtraStats` pattern.
- [x] Cancelled transcriptions keep their `.cancelled` status (were overwritten
  to `.failed` "timed out"); timeout kills are distinguished from crashes.
- [x] Transcription timeout escalates to SIGKILL after a grace period (a
  signal-ignoring child can no longer wedge the serial transcription queue
  forever).
- [x] PlaudAuth windows set `isReleasedWhenClosed = false` (over-release crash
  pattern already documented/fixed elsewhere in the codebase).
- [x] Segment audio playback uses `URL(fileURLWithPath:)` (play buttons did
  nothing if the recordings path contained a space).
- [x] UpdateChecker retains its progress observations (progress UI was frozen at
  0% for the whole download).
- [x] `visibleEntries` descending sort comparator preserves strict weak ordering
  (undefined behaviour / nondeterministic row shuffling with equal keys).
- [x] Auto-transcribe backlog sweeps read all entries, not the filtered
  `visibleEntries` (an active device/day filter silently suppressed queueing).
- [x] Trigger CLI stdout parsed with a persistent line buffer (markers split
  across pipe chunks could stick `hidockRecordingActive` on forever).

### shared / transcription-pipeline / mcp-server (fixed via agent, diff-reviewed)
- [x] `llm_cli`: errored/partial claude streams no longer returned as valid
  summaries (was deleting good summaries and writing truncated ones); complete
  responses no longer discarded when the child exits slowly.
- [x] `recluster_with_anchors`: `seg["speaker"]` updated alongside `speaker_id`
  (regenerated .md showed stale speakers).
- [x] `corrections.py`: word-boundary matching (was corrupting words containing
  the correction key) + atomic save.
- [x] Atomic temp-file + `os.replace` saves for the three worst state files:
  `voice_library_lite` (crash mid-save could permanently wipe all enrolled
  speakers), `corrections.json`, transcript writes.
- [x] `knowledge.py` FTS join robust to non-str speaker entries (one odd
  frontmatter entry aborted the whole index rebuild).
- [x] MCP server path containment via `is_relative_to` (prefix check accepted
  sibling directories).
- [x] `summaries_index.py`: `since` comparison normalizes `T`/space ISO formats.
- [x] `obsidian.py`: wikilinks process names longest-first (substring names broke
  linking).
- [x] `hooks.py`: explicit-null summary titles no longer crash every hook.
- [x] `bench_backends.py`: literal `%` escaped in argparse help (`--help` crashed).
- [x] `transcription-pipeline/state.py`: fresh default state per call (aliased
  inner dict leaked mutations across files in a batch).

### Windows (fixed via agent, diff-reviewed — criticals only, per macOS-primary policy)
- [x] `Windows-Script/extractor.py` status/cached-status no longer crash once any
  `vol:`/`plaud:` state key exists (was unrecoverable without hand-editing
  state.json).
- [x] Tray tooltip called `is_running`/`is_holding` properties as methods →
  `TypeError` in a Qt slot.
- [x] "Pair Plaud" persisted (the `plaudPaired` signal had no listener, so Plaud
  sync never ran).

## Planned — deferred findings, by priority

### P1 — data integrity / correctness (macOS pipeline)
- [ ] **Silence-strip timestamp remapping** (`transcribe.py`, `transcribe_parakeet.py`,
  `transcribe_cohere.py`, `diarize_lite.py`): when >5% silence is stripped, ASR
  runs on the shortened WAV but diarization + sidecars + SRT use the original
  timeline — timestamps drift by the stripped amount (wrong speakers, wrong
  seeks). Needs a per-chunk offset map and one remap pass. The biggest remaining
  correctness bug in the pipeline; needs its own focused change + tests.
- [ ] **Sortformer cross-window speaker stitching** (`diarize_sortformer.py`):
  per-window `speaker_N` labels are pooled without remapping, so labels collide
  after the first 300 s window; overlap turns are also emitted twice. Fix via
  majority-overlap label remap in the 30 s overlap region.
- [ ] **state.json cross-process locking** (`usb-extractor/extractor.py`, also
  `transcription-pipeline/state.py` status prune): concurrent `status` +
  `download` invocations do unlocked read-modify-write of the same state file —
  whichever saves last clobbers the other (lost download records re-trigger
  auto-downloads; lost `removed` flags resurrect user-suppressed files). Fix:
  `flock` around load→mutate→save + unique temp names (`mkstemp`).
- [ ] **Volume duplicate-basename collisions** (`extractor.py` volume path): state
  keys and output paths use basename only, so `FOLDER01/REC0001.wav` and
  `FOLDER02/REC0001.wav` share one state record and one destination — last one
  wins silently. Fix needs a state-key schema change (relative path) with
  migration for existing `vol:<name>/<basename>` records.
- [ ] **`whisper_guard` inert on real Whisper output**: `clean_transcript` splits
  on newlines but `result["text"]` has none, so the hallucination filters never
  fire. Fix by feeding segment-joined text; needs care because the cleaned text
  is what gets written (formatting changes).
- [ ] **Parakeet model-manager entry is fake** (`shared/models.py`): "Download"
  fetches an HTML page and reports installed; honor `managed_externally`, derive
  installed via `_python_module_available("parakeet_mlx")`.
- [ ] **`transcribe_cpp.py` parity ports**: SIGTERM handler (timeout kill leaves
  state stuck `in_progress` forever) and the stale-transcript status guard —
  both already exist in `transcribe.py`; this is the backend the bundled app
  actually runs.
- [ ] **`llm_cli` timeout enforcement on the streaming path**: the `timeout`
  parameter is ignored while consuming the claude stream — a hung CLI blocks
  summarization/chat indefinitely. Needs a wall-clock deadline + kill.
- [ ] **`models.download_model_if_needed`**: verify `downloaded == Content-Length`
  before renaming into place (short/HTML bodies currently accepted forever).

### P2 — robustness / UX (macOS)
- [ ] Extractor: top-level try/except in `main()` for JSON-emitting commands so
  e.g. `usb.core.NoBackendError` (libusb missing) yields `{"error": ...}` JSON
  instead of a bare traceback the Swift parser chokes on.
- [ ] Extractor: `mark-downloaded --volume-name` breaks for filenames with
  spaces/parens (`validate_filename`) and wrongly appends `.mp3` to volume
  files; skip `output_path_for` for volume/plaud keys.
- [ ] Extractor: `mark-removed`/`unmark-removed` need `--volume-name` support so
  volume rows can be keyed correctly (`vol:<name>/<file>`).
- [ ] Extractor: CMD_TRANSFER frame sequencing — duplicate seq frames are written
  twice, gaps aren't detected; require `req == last_seq(+1)` and abort on gaps
  (needs a device-capture to confirm firmware behaviour first).
- [ ] Extractor: `download_new` disconnected branch should include the `errors`
  key for schema consistency; `_safe_resolve` prefix check → `is_relative_to`.
- [ ] Plaud: `downloaded = stored or existing` still trusts pre-fix partial files
  at the final path; consider a one-off length reconciliation against the API.
- [ ] Swift: remaining low-severity notes from the audit — overwrite-trim swap
  ordering can lose the original on a failed move; `importSingleFile`/
  `probeDuration` beachball the main thread on large files; `reclassifySummary`
  bypasses the serial summarise queue; `log()` writes from multiple queues with
  independent FileHandles.
- [ ] `typed_summarize.py`: add `summarize.py`'s prompt-injection system
  instruction; fix `_delete_prior_summaries` glob-escaping and delete-after-write
  ordering.
- [ ] `summarize.py`: un-double the `{{ }}` braces in prompt templates (model
  mimics them → JSON extraction fails → silent empty summaries).
- [ ] `transcript_writer.py`: YAML frontmatter round-trip (quotes in titles,
  comma-splitting quoted speaker lists, newlines in titles).
- [ ] `merge_finder.py`: greeting/farewell veto uses substring matching ("they"
  contains "hey ") — tokenize.
- [ ] MCP server: rebuild wipes the index before re-indexing and serves the
  partial index after a mid-rebuild exception — build into a local and swap on
  success, or make rebuild transactional.
- [ ] Remaining non-atomic writes: `migrate.py`, `config_store.py`,
  `recluster_with_anchors.py`, `voice_training.py`, `merge_finder.py`.
- [ ] `event_log.py`: connection cache ignores `db_path` after first call; not
  thread-safe.
- [ ] `audio_utils.extract_embedding`: silent MFCC fallback mixes 40-dim and
  192-dim vectors → ragged array crash downstream; drop mismatched embeddings.
- [ ] `pipeline_dispatch.py`: `from transcription_pipeline import config` can
  never import (hyphenated dir); path-insert like `transcribe.py` does.
- [ ] `intelligence.py`: empty/unparseable dates always classify meetings stale /
  people "losing touch".
- [ ] Misc lows: temp-WAV leak on failed transcriptions, SRT skipped when
  diarization returns empty segments, `transcript_stats.py` keys action items as
  `stem + ".mp3"` regardless of extension, `diarize.py` legacy overlap logic.

### P3 — Windows app (secondary platform; batch these if/when Windows gets attention)
- [ ] `--product-id` parsed but never threaded into `find_device()` — multi-device
  routing is dead code (paired non-45068 devices probe the wrong dock).
- [ ] `_on_sync_complete` treats download output as a status payload — table wipes
  to "0 recordings" after every download; download-then-transcribe never
  transcribes. Should re-run `_refresh_status()` after downloads.
- [ ] `_transcribe_selected` passes multiple filenames to the single-filename
  `download` command (argparse exit 2) and doesn't branch by device type.
- [ ] Context-menu Download / Mark-as-Downloaded ignore device type (violates the
  documented device-identity rules; contaminates HiDock state with volume keys).
- [ ] QSettings read/written from Plaud worker threads (documented constraint
  violation) — marshal token refresh back to the GUI thread.
- [ ] Worker threads touch widgets: transcript viewer re-diarize/re-cluster,
  merge/trim/status-bar updates, feedback submission → route through signals.
- [ ] `QTimer.singleShot` from plain threads (update checks never deliver);
  "Restart && Update" runs `sys.exit` on a worker thread so the exe stays locked
  and the update silently fails.
- [ ] Mic trigger: COM not initialised on the poll thread (pycaw raises →
  permanently inactive) and session-based detection counts playback as mic
  activity; `stop()` races `_start_ffmpeg` leaving orphan ffmpeg.
- [ ] Ctrl+R bound twice (ambiguous → dead hotkey); `shlex.quote` used for
  cmd.exe in Ask-Claude; concurrent transcription batches via context menu race
  `_txq_items`.
- [ ] mic-trigger Swift CLI: serialise FFmpegHolder access between the poll timer
  queue and the signal handler.

### Structural improvements (beyond bug fixes)
- [ ] **Split `AppDelegate.swift` (7,800 lines)** into focused controllers
  (trigger lifecycle, sync engine, transcription queue, summaries, UI glue).
  Most Swift bugs found live in hand-rolled process/state plumbing that would
  shrink dramatically with a `SubprocessRunner` helper (pipe-safe reads,
  timeout+SIGKILL escalation, line buffering) used everywhere.
- [ ] **One shared "atomic JSON store" helper** for Python (temp file + replace +
  optional flock) replacing the ~10 hand-rolled save functions; most of the
  medium-severity Python findings disappear into it.
- [ ] **Extractor JSON contract tests**: golden tests asserting every CLI command
  emits parseable JSON on stdout for success *and* failure paths (device
  missing, libusb missing, partial transfer). Would have caught several of
  today's findings.
- [ ] **Deduplicate `usb-extractor/` vs `Windows-Script/`** extractor + plaud
  clients (~2,700 duplicated lines that have already drifted — several bugs
  fixed on one side historically never reached the other).
- [ ] **Swift tests for the sync/queue state machines** — the trigger lifecycle
  and transcription queue races found today are testable with a process-spawner
  seam.
- [ ] Wire `test.yml` to also run on feature branches / PRs for the Swift build
  (currently only Python tests gate PRs).

## Rejected / Not Applicable
- Windows/macOS feature parity work — explicitly out of scope per project
  policy (macOS primary).
- `diarize.py` (legacy pyannote path) deep fixes — nothing imports it; candidate
  for deletion instead.
- `voice_library.py` (transcription-pipeline) — legacy, unused; delete rather
  than fix.
- Rewriting the Jensen USB protocol layer — the warm-up/drain workarounds are
  empirically derived and load-bearing; only the seq-gap check is worth adding,
  and only with a capture to validate against.
