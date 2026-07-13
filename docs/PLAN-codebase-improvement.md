# Codebase Improvement Plan — Full Bug Audit
Research date: 2026-07-02
Sources: four parallel code-audit agents over `hidock-mic-trigger/Sources/`, `usb-extractor/`, `shared/` + `transcription-pipeline/` + `mcp-server/`, and `Windows-App/` + `Windows-Script/` + `mic-trigger/`; findings verified against the code before fixing. Branches: `fix/audit-bugfixes` (first pass, PR #49) and `fix/audit-deferred` (second pass, stacked).

## Current State
A whole-codebase audit (2026-07-02) surfaced ~80 findings across all components.
Two fix passes ran the same day: the first landed the highest-impact confirmed
bugs (PR #49); the second (`fix/audit-deferred`) cleared the deferred P1/P2/P3
backlog — everything below except the Structural section and two
device/design-gated items. Test count grew 628 → 739 (157 usb-extractor,
74 Windows-App, 415 shared, 24 mcp-server, 69 transcription-pipeline), all
passing; Swift app and mic-trigger CLI build clean.

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

## Completed (2026-07-02, second pass — branch `fix/audit-deferred`)
Seven parallel fix agents cleared the deferred backlog; all diffs reviewed,
739 tests green, both Swift targets build.

### P1 — data integrity / correctness (macOS pipeline)
- [x] **Silence-strip timestamp remapping**: `strip_silence_with_map()` returns a
  piecewise-linear (stripped→original) knot map; all three ASR backends remap
  segment + word timestamps back to the original timeline before diarization /
  sidecars / SRT. 24 new tests incl. legacy-output equivalence.
- [x] **Sortformer cross-window stitching**: greedy max-overlap label remap in the
  30 s overlap region, fresh labels for unseen speakers, overlap deduped at the
  midpoint. 7 new tests.
- [x] **state.json cross-process locking** (extractor): `flock` on a sibling
  lockfile held across load→mutate→save for every state writer; long transfers
  re-load fresh state under the lock before saving (merge, not clobber); unique
  `mkstemp` temp names. Plaud persistence overlays only plaud-owned keys.
- [x] **state.json locking** (transcription-pipeline): transparent lock in
  `save_state` + `update_state` locked-RMW helper; cmd_status prune re-verifies
  under the lock and skips on contention.
- [x] **Volume duplicate-basename collisions**: state keys are now
  `vol:<volume>/<relpath>` with read-time legacy-key migration (unique basenames
  honored, ambiguous ones refused with candidates listed); duplicate basenames
  get flattened output names (`FOLDER01_REC0001.wav`) so both files survive;
  round-trips through the Swift app with no Swift change.
- [x] **whisper_guard actually fires**: single-line Whisper text is split on
  sentence boundaries (opt-in `segments=` kwarg too); output re-joined with the
  input's own separator.
- [x] **transcribe_cpp parity ports**: SIGTERM `_IN_FLIGHT` handler (no more
  permanently-stuck `in_progress`) and the stale-transcript status guard.
- [x] **llm_cli stream timeout**: watchdog timer kills a hung claude CLI at the
  deadline in both streaming paths; timeout reported distinctly.
- [x] **models.py**: `managed_externally` honored (Parakeet installed = module
  present; download refuses instead of saving an HTML page); short downloads
  deleted + raised instead of renamed into place.
- [x] **diarize_lite whisper-boundary fallback**: misindexing branch removed —
  overlap-based assignment used unconditionally.

### P2 — robustness / UX (macOS)
- [x] Extractor JSON contract: top-level guard prints `{"error", "connected":
  false}` JSON for any unexpected exception (e.g. `NoBackendError`).
- [x] `mark-downloaded`/`mark-removed`/`unmark-*`: correct keys for volume and
  Plaud rows (`--volume-name` added; no more `validate_filename` crashes on
  spaces/parens or bogus `.mp3` suffixes); `set-output` skips prefixed keys.
- [x] `download_new`/`volume_import_new` disconnected branches include `errors`;
  `_safe_resolve` uses `is_relative_to`.
- [x] Swift lows: trim swap uses `replaceItemAt` (original can't be lost, errors
  surfaced); import copy + duration probe off the main thread;
  `reclassifySummary` serialised through the summarise queue; `log()` writes
  serialised on a dedicated queue.
- [x] `typed_summarize`: prompt-injection guard prepended; prior-summary cleanup
  is glob-safe and delete-after-write.
- [x] `summarize.py` `{{ }}` braces un-doubled.
- [x] `transcript_writer` YAML round-trip: quotes escaped, newlines collapsed,
  quote-aware list parsing (7 new tests).
- [x] `merge_finder`: word-boundary greeting/farewell vetoes; atomic sidecar save.
- [x] MCP/knowledge rebuild transactional (rollback on failure; global assigned
  only on success — a failed rebuild keeps serving the previous index).
- [x] Remaining atomic writes: `migrate.py`, `config_store.py`,
  `recluster_with_anchors.py`, `voice_training.py`.
- [x] `event_log`: per-call connections, cache keyed correctly, thread-safe.
- [x] `audio_utils.extract_embedding`: explicit failure instead of silent 40-dim
  MFCC dim-mixing; recluster skips failed segments (diarize() falls back to
  transcript-without-speakers via transcribe.py's existing catch).
- [x] `pipeline_dispatch` import fixed (path-insert of hyphenated dir).
- [x] `intelligence.py`: unknown dates excluded from staleness / losing-touch /
  recency scoring instead of scoring as ancient.
- [x] Misc lows: temp-WAV leak (unlink in finally), SRT empty-segments gate,
  `transcript_stats` stem matching, Parakeet/Cohere PROGRESS/STAGE emitted in
  the Swift parser's format on stderr, Cohere transformers-v5 tokenizer call.

### P3 — Windows app + mic-trigger CLI
- [x] `--product-id` threaded through every USB command path (state records
  stamped, state-only entries filtered per device, mirroring macOS).
- [x] Download completion triggers a real `_refresh_status()` (table no longer
  wipes; download-then-transcribe works) via a `_download_done` sentinel.
- [x] `_transcribe_selected` + context-menu Download/Mark share per-device
  command building (volume/Plaud/HiDock branching, one command per file).
- [x] QSettings confined to the GUI thread (accounts loaded before spawning;
  rotated tokens persisted via signal).
- [x] All worker-thread widget access routed through signals (re-diarize,
  re-cluster, merge, trim, feedback, update checks).
- [x] "Restart && Update" installs from the GUI thread, guards `sys.frozen`,
  quits the Qt loop inside the bat's copy window.
- [x] Mic trigger: COM initialised on the poll thread; capture-endpoint peak
  meter used when available (falls back to the old session heuristic);
  stop() joins the poll thread before killing ffmpeg.
- [x] Ctrl+R ambiguity removed; win32 quoting for Ask-Claude; concurrent
  transcription batches refused while one runs.
- [x] mic-trigger CLI: poll timer moved to the main queue, serialising
  FFmpegHolder with the signal handlers.
- 21 new Windows tests (74 total).

## Planned — remaining items
- [ ] **CMD_TRANSFER frame sequencing** (extractor): duplicate seq frames would be
  written twice, gaps aren't detected. Gated on a real device capture to
  confirm firmware retransmit behaviour before tightening (`req ==
  last_seq(+1)`, abort on gaps).
- [ ] **Plaud pre-fix partial files**: `downloaded = stored or existing` still
  trusts files downloaded before atomic writes landed; optional one-off length
  reconciliation against the API if truncated Plaud files ever surface.
- [ ] `transcribe_parakeet`/`transcribe_cohere`: gate `_diarized.json` writes on
  non-empty segments (writes an empty-but-valid JSON today; cosmetic).

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
