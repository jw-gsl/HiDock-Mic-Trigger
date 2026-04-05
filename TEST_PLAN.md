# HiDock Tools — Test Plan

## Current Test Coverage

| Component | Tests | Coverage |
|-----------|-------|----------|
| USB Extractor | 61 | Protocol parsing, filenames, recording status, validation |
| Transcription Pipeline | 28 | State management, CLI parsing, voice library I/O |
| Windows App | 17 | Config, state, USB sync parsing |
| macOS App (Swift) | 1 | Helpers (string formatting) |
| Shared — Transcript Writer | 22 | Frontmatter build/parse, auto-title, diarized format, write roundtrip |
| Shared — LLM CLI | 12 | Engine detection, priority, query, JSON extraction |
| Shared — Summarize | 8 | Normalization, empty summary, engine fallback |
| Shared — Knowledge Graph | 16 | Rebuild, index, search (FTS5), people, action items, stats |
| Shared — Config Store | 16 | TOML parse/serialize, roundtrip, defaults, save/load |
| Shared — Migrate | 9 | Find unmigrated, dry-run, apply, state metadata |
| Shared — Obsidian | 12 | Copy/symlink sync, wikilinks, person notes, daily notes |
| Shared — Hooks | 9 | Hook execution, env variables, pipeline orchestration |
| MCP Server | 18 | Protocol (init, tools, resources, ping), all 10 tool calls |
| **UI (both platforms)** | **0** | **No tests** |
| **Total** | **229** | |

## Gaps and New Tests Needed

### Priority 1: Audio/Diarization Modules (require `soundfile` dependency)

| Test File | Tests | What it covers |
|-----------|-------|---------------|
| `shared/tests/test_audio_utils.py` | MFCC extraction, audio loading, neural embedding shape, segment extraction | Core signal processing correctness |
| `shared/tests/test_diarize_lite.py` | VAD segment detection, embedding clustering, speaker assignment, diarize() output format | Diarization pipeline end-to-end |
| `shared/tests/test_voice_library_lite.py` | Enroll, identify, rename, delete, cosine similarity, growing library, cross-model compat | Voice library CRUD and matching |
| `shared/tests/test_models.py` | Model registry, status check, download path resolution, delete | Model management without network |

### Priority 2: Integration Tests

| Test | What it covers |
|------|---------------|
| Transcribe + diarize end-to-end | Audio file in → diarized JSON out (requires test audio fixture) |
| Voice enrollment + re-identification | Enroll from one file, identify in another |
| Model download + fallback | Neural embed available vs MFCC fallback |
| Transcript viewer JSON round-trip | Load → rename speaker → save → reload |
| Summarize + write_transcript roundtrip | LLM output → frontmatter → knowledge graph index |
| Knowledge graph + MCP server end-to-end | Index transcripts → query via MCP protocol |

### Priority 3: macOS App (Swift)

| Test File | Tests |
|-----------|-------|
| `Tests/UpdateCheckerTests.swift` | Version comparison, API response parsing |
| `Tests/ModelsTests.swift` | Model status detection, path resolution |
| `Tests/HelpersTests.swift` | Already exists — extend with device name, error descriptions |

### Priority 4: Windows App

| Test File | Tests |
|-----------|-------|
| `tests/test_transcription.py` | Transcribe function, diarize parameter, model ready check |
| `tests/test_update_checker.py` | Version comparison, release parsing |
| `tests/test_model_manager.py` | Model registry, status, download path |

## Manual Test Plan

### 1. First Run / Onboarding
- [ ] Fresh install: onboarding wizard appears
- [ ] Skip all steps: app opens, settings preserved
- [ ] Connect HiDock during step 2: auto-detected, auto-advance
- [ ] Select mic in step 3: persisted after onboarding
- [ ] Download model in step 4: progress shown, cancel works
- [ ] Complete onboarding: doesn't show again on next launch
- [ ] Back button works on all steps including final

### 2. USB Sync
- [ ] Pair new device: device appears in status
- [ ] Refresh: recordings listed correctly
- [ ] Download selected: file saved, status updates
- [ ] Download new: only un-downloaded files fetched
- [ ] Auto-download: triggers after recording detected
- [ ] Device not found: shows which app holds it (e.g. "held by Microsoft Edge")
- [ ] Multiple devices: only connected devices shown in menu bar

### 3. Mic Trigger
- [ ] Start trigger: status shows Running, green dot, uptime counting
- [ ] Stop trigger: status shows Stopped, gray dot
- [ ] Auto-start on launch: trigger starts automatically
- [ ] Mic disconnect: falls back to preferred/MacBook/any
- [ ] Mic reconnect: auto-switches to preferred mic

### 4. Transcription
- [ ] Transcribe single file: progress shown, transcript saved
- [ ] Transcribe all: batch progress, all files processed
- [ ] Model not downloaded: shows message, blocks transcription
- [ ] Model download: progress bar with MB/s, cancel works

### 5. Speaker Diarization
- [ ] Enable "Speaker Labels" toggle
- [ ] Transcribe with diarization: segments assigned speaker IDs
- [ ] Open transcript viewer: colored speaker blocks displayed
- [ ] Rename speaker: name persists in JSON, all instances updated
- [ ] Auto-enrollment: voice saved to library on rename
- [ ] Next recording: known speakers auto-identified
- [ ] Voice library grows: sample count increases on re-identification

### 6. Voice Library
- [ ] Open Voice Library: enrolled speakers listed
- [ ] Delete speaker: removed from library
- [ ] Rename speaker: updated across library
- [ ] Empty state: helpful message shown

### 7. Model Manager
- [ ] Open Models: all 3 models listed with correct status
- [ ] Download model: progress bar, completes successfully
- [ ] Delete model: removed, status updates to "Not installed"
- [ ] Whisper model: marked as required

### 8. Feedback
- [ ] Send Feedback: structured form with categories/severities
- [ ] Submit: creates GitHub issue via API
- [ ] My Feedback: history shows with filter/sort/search
- [ ] View on GitHub: opens correct issue URL

### 9. Auto-Update
- [ ] New version available: dialog appears on launch
- [ ] Restart & Update: downloads, installs, relaunches
- [ ] Update on Quit: downloads in background, installs on quit
- [ ] Skip this version: doesn't ask again for same version
- [ ] Check for Updates (manual): shows result either way
- [ ] No update: "You're up to date" message

### 10. Appearance
- [ ] Auto: follows system dark/light
- [ ] Dark: forces dark mode
- [ ] Light: forces light mode
- [ ] macOS: takes effect immediately
- [ ] Windows: takes effect after restart

### 11. Summarization
- [ ] Install `claude` CLI: `which claude` returns a path
- [ ] Transcribe with `--summarize`: title, action items, decisions extracted
- [ ] No LLM installed: transcription completes without summarization
- [ ] Transcript has YAML frontmatter with structured metadata
- [ ] Action items have task, assignee, due, status fields
- [ ] Summary text appended as `## Summary` section

### 12. Knowledge Graph
- [ ] `python -m shared.knowledge rebuild`: indexes all transcripts
- [ ] `python -m shared.knowledge search "topic"`: returns matching transcripts
- [ ] `python -m shared.knowledge person "Name"`: shows meeting history and action items
- [ ] `python -m shared.knowledge actions`: lists open action items
- [ ] `python -m shared.knowledge stats`: shows counts
- [ ] People indexed from both speakers (diarization) and action item assignees

### 13. Obsidian Sync
- [ ] Set vault path in config: `obsidian.vault_path = "/path/to/vault"`
- [ ] Enable: `obsidian.enabled = true`
- [ ] Sync creates files in `Vault/Meetings/`
- [ ] Speaker names converted to `[[wikilinks]]` in copied transcripts
- [ ] Person notes generated in `Vault/Meetings/People/`
- [ ] Disabled by default: no errors when vault not configured

### 14. MCP Server
- [ ] `python mcp-server/server.py` starts and responds to JSON-RPC
- [ ] `search_meetings` tool returns matching transcripts
- [ ] `list_action_items` returns open action items
- [ ] `get_person_profile` returns person's meeting history
- [ ] Works with Claude Desktop when added to config

### 15. Post-Transcription Hooks
- [ ] Set `hooks.post_transcription = "echo done"` in config
- [ ] After transcription: hook command executed
- [ ] Environment variables set: TRANSCRIPT_PATH, TRANSCRIPT_TITLE, etc.
- [ ] Hook failure is non-fatal: transcription still succeeds

### 16. Configuration
- [ ] Config created at `~/.config/hidock/config.toml` on first access
- [ ] Editing config values takes effect on next transcription
- [ ] Missing config file: defaults used without error
- [ ] All settings work on both macOS and Windows

### 17. Migration
- [ ] `python -m shared.migrate`: shows files that need migration (dry-run)
- [ ] `python -m shared.migrate --apply`: adds frontmatter to old transcripts
- [ ] Already-migrated files skipped
- [ ] `--rebuild-index` rebuilds knowledge graph after migration

### 18. Cross-Platform Parity
- [ ] All menu items present on both platforms
- [ ] Footer bar buttons match: Appearance, Models, Voice Library, Check for Updates, My Feedback, Send Feedback
- [ ] Onboarding has same 5 steps
- [ ] Transcript viewer has same features
- [ ] Voice library has same features
- [ ] Model manager has same 3 models
- [ ] Feedback form has same categories/severities

## Automated Test Execution

```bash
# All Python tests (excluding audio tests that need soundfile)
python3 -m pytest transcription-pipeline/tests/ usb-extractor/tests/ Windows-App/tests/ \
  shared/tests/test_transcript_writer.py shared/tests/test_llm_cli.py \
  shared/tests/test_summarize.py shared/tests/test_knowledge.py \
  shared/tests/test_config_store.py shared/tests/test_migrate.py \
  shared/tests/test_obsidian.py shared/tests/test_hooks.py \
  mcp-server/tests/ -v

# Shared module tests only (fast, no heavy dependencies)
python3 -m pytest shared/tests/test_transcript_writer.py shared/tests/test_llm_cli.py \
  shared/tests/test_summarize.py shared/tests/test_knowledge.py \
  shared/tests/test_config_store.py shared/tests/test_migrate.py \
  shared/tests/test_obsidian.py shared/tests/test_hooks.py \
  mcp-server/tests/ -v

# Swift tests
cd hidock-mic-trigger && xcodebuild test -scheme hidock-mic-trigger -quiet

# CI runs shared module tests on push to main and PRs (.github/workflows/test.yml)
```
