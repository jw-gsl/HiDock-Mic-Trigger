# Unimplemented Ideas — Collected from April 2026 Sessions

Ideas raised during development that haven't been implemented yet.
Grouped by area with priority assessment.

> See also: `PLAN-hinotes-web-feature-mining-2026-04-21.md` — features discovered by mining the hinotes.hidock.com web app bundle (2026-04-21). Several ideas below are re-surfaced and cross-referenced there.

---

## Voice & Speaker Recognition

### Voice Training Window (feature branch exists)
**Status:** `feature/voice-training` branch — backend + UI built, not merged
- [x] Smart sample selection (picks best 5-15s clips)
- [x] Per-sample reassignment dropdown
- [x] Review state persistence (confirmed/unconfirmed)
- [x] Voice library auto-matching wired into diarization
- [ ] Windows parity (PyQt equivalent)
- [ ] Tests for voice_training.py
- [ ] Auto-identification of known speakers on new transcriptions (pipeline wired but untested)
- [ ] Background re-processing of old transcripts with new voice data

### Voice Enrollment Blending (from minutes)
**Priority: MEDIUM**
Running-average of speaker embeddings across sessions. Our library already does this on manual enrollment. Needs testing that the blending actually improves matching over time.
- [ ] Test: enroll same person from 5 meetings, verify confidence increases
- [ ] Test: similar voices (e.g. two male speakers) don't cross-contaminate

### CAM++ Embedding Model Evaluation
**Priority: MEDIUM** | From: minutes v0.10.0
12% lower error reported. Different model family from our TitaNet.
- [ ] Download CAM++ ONNX model
- [ ] Benchmark against TitaNet on worst transcripts
- [ ] If better, add as option

---

## Transcription Quality

### Silence-to-Padding Replacement
**Priority: LOW** | From: minutes
Replace silence >500ms with 300ms zeros before Whisper to prevent hallucination loops at source. Code exists in `_replace_silence_with_padding()` but not wired into the pipeline.
- [ ] Wire into transcribe.py before Whisper call
- [ ] Test on recordings that had hallucination issues

### No-Speech-Prob Filtering
**Priority: LOW** | From: minutes
Whisper provides `no_speech_prob` per segment. Filter segments with >0.8 probability.
- [ ] Check if Whisper result includes no_speech_prob
- [ ] Filter before diarization
- [ ] Test impact on quality

### Foreign-Script Filter
**Priority: LOW** | From: minutes
Remove segments in unexpected languages (Whisper sometimes hallucinates Chinese/Arabic).
- [ ] Detect non-Latin script in segments
- [ ] Remove or flag

### Non-Speech Event Anonymization
**Priority: LOW** | From: minutes v0.11.0
`[laughter]`, `[cough]` shouldn't get speaker labels.
- [ ] Detect markers in Whisper output
- [ ] Strip speaker assignment

---

## UI / UX

### Corrections Dictionary UI
**Priority: MEDIUM**
Currently `corrections.json` must be edited manually. Needs an in-app editor.
- [ ] UI to add/remove/edit word corrections
- [ ] Show corrections applied count after transcription
- [ ] Import/export corrections

### Post-Meeting Workflow Nudges
**Priority: MEDIUM** | From: minutes v0.11.2
After transcription completes, suggest next steps.
- [ ] "Tag speakers" nudge when speakers untagged
- [ ] "Open Voice Training" after 5+ meetings with unconfirmed voices
- [ ] Weekly summary suggestion

### Transcript Search
**Priority: MEDIUM**
Search across all transcripts for keywords/phrases.
- [ ] Full-text search across all .md transcripts
- [ ] Search from the main recordings table
- [ ] Highlight matches in transcript viewer

### Recording Notes / Tags
**Priority: LOW**
Add user notes or tags to recordings (e.g. "1:1 with Dave", "Team standup").
- [ ] Note field per recording in the table
- [ ] Tag autocomplete from previous tags
- [ ] Filter by tag

---

## Platform & Infrastructure

### Windows Parity Gaps
**Priority: HIGH for merge**
Features that exist on macOS but not Windows:
- [ ] Voice Training window
- [ ] Transcription queue dialog (basic only on Windows)
- [ ] Mic preferred/fallback (CoreAudio concept — may not be possible)

### CI Build Verification
**Priority: MEDIUM**
Main branch has 94+ commits. CI status checks show as "expected" but may not be running.
- [ ] Verify macOS build succeeds in CI
- [ ] Verify Windows build succeeds in CI
- [ ] Verify all 485+ Python tests pass in CI
- [ ] Fix any CI failures

### Auto-Update System
**Priority: LOW**
Update checker exists but the actual download+install flow needs testing.
- [ ] Test check for updates
- [ ] Test download and install flow
- [ ] Test "Update on Quit" behaviour

---

## Knowledge & Intelligence

### Knowledge Graph Integration
**Priority: LOW** | Already scaffolded in intelligence layer
- [ ] Verify knowledge.db is being populated
- [ ] Test full-text search
- [ ] Wire into transcript viewer (search within meeting)

### LLM Summarization Testing
**Priority: LOW**
The summarize feature exists but hasn't been tested end-to-end.
- [ ] Test with Claude CLI
- [ ] Test with Ollama
- [ ] Verify action items, decisions, key points extraction

### Obsidian Sync Testing
**Priority: LOW**
Code exists but not verified.
- [ ] Test sync to vault
- [ ] Verify wikilinks
- [ ] Test daily notes integration

---

## Data & State

### Orphan State Cleanup
**Priority: LOW**
The extractor state file can accumulate orphan entries from deleted recordings.
- [ ] Add periodic cleanup of state entries where file doesn't exist
- [ ] Clean up on app startup

### Backup / Export
**Priority: LOW**
No way to export all transcripts, voice library, or settings.
- [ ] Export all transcripts as ZIP
- [ ] Export voice library
- [ ] Import from backup
