# Testing Tier 1 & Tier 2 — Manual Smoke Test Guide

Branch: `claude/gap-analysis-minutes-O3Glk` (13 commits ahead of main)

---

## Step 1: Pull the branch locally

```bash
cd ~/path/to/HiDock-Mic-Trigger
git fetch origin claude/gap-analysis-minutes-O3Glk
git checkout claude/gap-analysis-minutes-O3Glk
```

---

## Step 2: Run automated tests

```bash
python -m pytest shared/tests/ mcp-server/tests/ \
  --ignore=shared/tests/test_audio_utils.py \
  --ignore=shared/tests/test_diarize_lite.py \
  --ignore=shared/tests/test_voice_library_lite.py \
  -v
```

- [ ] All 227 tests pass
- [ ] No import errors or warnings

---

## Step 3: Health check (no HiDock device needed)

```bash
python -m shared.health_check
```

- [ ] Command runs without crashing
- [ ] Shows a status report (directories, database, engines, disk space, etc.)
- [ ] LLM engines section lists at least one engine (or shows "warning" if none installed — both are fine)

```bash
python -m shared.health_check --json
```

- [ ] Outputs valid JSON

---

## Step 4: Schema migration (only if you have an existing knowledge.db)

> Skip this section if you've never run the transcription pipeline before.

```bash
python -c "
from shared.knowledge import KnowledgeGraph
kg = KnowledgeGraph()
print('Stats:', kg.get_stats())
# Verify confidence column exists
conn = kg._get_conn()
row = conn.execute('PRAGMA table_info(action_items)').fetchall()
cols = [r[1] for r in row]
print('action_items columns:', cols)
assert 'confidence' in cols, 'FAIL: confidence column missing!'
print('PASS: Schema migration OK')
kg.close()
"
```

- [ ] Prints stats without error
- [ ] `confidence` column present in `action_items`
- [ ] No "database is locked" or corruption errors

---

## Step 5: Transcription pipeline — the critical test

You need any short audio file (.mp3, .wav, .m4a). A 10-30 second clip is fine.

### 5a. Transcription only (no summarization)

```bash
cd transcription-pipeline
python transcribe.py transcribe /path/to/your/short-recording.mp3
```

- [ ] Transcription completes (prints JSON with `"transcribed": true`)
- [ ] No import errors for `shared.whisper_guard` or `shared.event_log`
- [ ] Output `.md` file created in `~/HiDock/Raw Transcripts/`
- [ ] Open the `.md` file — YAML frontmatter is valid (starts with `---`, has `title:`, `key_points:`, ends with `---`)
- [ ] Transcript text appears below the frontmatter

### 5b. Transcription with summarization (requires an LLM engine)

> Skip if no LLM CLI tool is installed (claude, codex, gemini, or ollama).

```bash
python transcribe.py transcribe /path/to/your/short-recording.mp3 --summarize
```

- [ ] Transcription and summarization complete
- [ ] Open the output `.md` file
- [ ] YAML frontmatter contains `action_items:`, `decisions:`, `key_points:`, `tags:`
- [ ] `key_points:` values are plain strings in YAML (NOT Python dict syntax like `{'text': ...}`)
- [ ] If any action items were extracted, they look normal (no `confidence` key visible in YAML — it's stored in DB only)

### 5c. Verify Whisper-Guard ran

Look at stderr output during transcription. You should see one of:
- `Whisper-Guard: filters triggered: [...]` (if it cleaned something)
- No Whisper-Guard message (if transcript was clean — also fine)

- [ ] No crash or traceback from Whisper-Guard

### 5d. Verify event log recorded

```bash
cd ..
python -m shared.event_log recent -n 10
```

- [ ] Shows recent events (transcription_started, transcription_completed, etc.)
- [ ] If summarization was used, shows summarization_started and summarization_completed
- [ ] Timestamps look correct

---

## Step 6: Knowledge graph indexing

```bash
python -c "
from shared.knowledge import KnowledgeGraph
kg = KnowledgeGraph()
count = kg.rebuild()
print(f'Indexed {count} transcripts')
stats = kg.get_stats()
print(stats)
kg.close()
"
```

- [ ] Rebuild completes without error
- [ ] Count matches number of `.md` files in `~/HiDock/Raw Transcripts/`
- [ ] Stats show correct meeting/action_item/decision counts

---

## Step 7: Intelligence layer

> Only meaningful if you have 2+ transcripts indexed.

```bash
python -m shared.intelligence relationships
python -m shared.intelligence consistency
python -m shared.intelligence topics
```

- [ ] Each command runs without crashing
- [ ] Output looks reasonable (people listed, topics listed, etc.)
- [ ] If you have few transcripts, empty results are fine — just no crashes

---

## Step 8: MCP server

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python mcp-server/server.py
```

- [ ] Returns JSON with a `tools` array
- [ ] Tools list includes: `research_topic`, `consistency_report`, `relationship_map`, `topic_trends`, `health_check`, `recent_events`

```bash
echo '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"health_check","arguments":{}}}' | python mcp-server/server.py
```

- [ ] Returns a health check report (not an error)

---

## Step 9: macOS app (full integration)

> Build with Debug configuration to avoid touching your production app.

```bash
cd hidock-mic-trigger
xcodebuild -project hidock-mic-trigger.xcodeproj \
  -scheme hidock-mic-trigger \
  -configuration Debug \
  -derivedDataPath /tmp/hidock-build
```

- [ ] Build succeeds

Launch the dev app:
```bash
open /tmp/hidock-build/Build/Products/Debug/hidock-mic-trigger.app
```

With the app running:

1. **Connect HiDock** (or wait for a recording to appear)
2. **Trigger a transcription** from the app UI
3. **Wait for it to complete**

- [ ] App doesn't crash during transcription
- [ ] Transcript appears in the app's transcript viewer
- [ ] If summarization is enabled in settings, summary fields are populated

---

## Step 10: Windows app (if applicable)

> Only needed if you test Windows builds.

- [ ] `Windows-App/core/transcription.py` changes don't break the Windows transcription flow
- [ ] App launches and basic UI works

---

## Summary

| Section | Tests | Risk Level |
|---------|-------|------------|
| Steps 2-3 | Automated tests + health check | Low |
| Step 4 | Schema migration | Medium (existing DB only) |
| Step 5 | Transcription pipeline | **High — most critical** |
| Steps 6-7 | Knowledge graph + intelligence | Low (new code, isolated) |
| Step 8 | MCP server | Low (new code, isolated) |
| Step 9 | macOS app integration | **High — full stack** |
| Step 10 | Windows app | Medium |

**Minimum viable test**: Steps 2, 3, 5a, and 5c. If those pass, the core pipeline is safe to merge.

**Full confidence test**: All steps through 9.
