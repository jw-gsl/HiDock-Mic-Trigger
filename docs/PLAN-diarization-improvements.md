# Diarization & Speaker Recognition Improvement Plan

Research date: April 2026
Sources: silverstein/minutes v0.10.0-v0.11.2, internal testing on 72 transcripts

---

## Current State (April 11, 2026)

Quality after full re-transcription:
- 74% Good (avg<30s, max<1min)
- 24% OK (avg<45s, max<2min)
- 0% Bad
- Avg segment: 19s, avg max: 51s

Pipeline: Whisper large-v3-turbo → Silero VAD → TitaNet embeddings → agglomerative clustering → 30s segment cap

---

## Completed Improvements

- [x] Audio normalization (RMS + peak retry) for quiet HiDock recordings
- [x] Whisper boundary fallback when VAD insufficient
- [x] Running-average speaker centroids (from minutes)
- [x] Post-clustering merge pass (from minutes)
- [x] Min 1.5s segment threshold for embeddings (from minutes)
- [x] Speech segment merging before embedding (from minutes)
- [x] 30s segment cap with sentence/comma/word-count splitting
- [x] Force min 2 speakers for >5min meetings
- [x] Hallucination filter (repeated end segments)
- [x] Save Whisper micro-segments (_whisper.json) for re-diarization
- [x] Stage-based progress (not %)
- [x] Corrections dictionary
- [x] Voice Training feature branch (UI + backend)

---

## In Progress

### Wire voice library into diarization pipeline
**Priority: HIGH** | Branch: feature/voice-training | Status: IMPLEMENTED

The voice library enrolls speakers but diarization doesn't check it.
Minutes had the same gap — fixed in v0.10.0.

Implementation:
- [x] After clustering in `diarize()`, compute centroid per cluster
- [x] Call `identify_speaker(centroid)` against voice library
- [x] If match found (confidence > 0.55), use enrolled name
- [x] Write matched names into `speaker_names` dict
- [x] Pre-fill names in the `_diarized.json` output
- [ ] Test: new transcriptions auto-name known speakers (needs voice library data)

### Preserve short interjections
**Priority: HIGH** | Status: VERIFIED OK

Minutes v0.11.0 found short responses ("yeah", "right") were being stripped.
Tested: our same-speaker merge only combines consecutive same-speaker segments,
so cross-speaker interjections stay separate. 48 segments <3s found in test
transcript, all correctly attributed.

- [x] Check if 0.5-2s segments get merged into previous speaker — NO, working correctly
- [x] Test on meetings with fast back-and-forth — verified on Rec73

---

## Planned

### Configurable embedding models + CAM++ evaluation
**Priority: MEDIUM** | From: minutes v0.10.0 | Status: IMPLEMENTED + BENCHMARKED (Apr 11)

Added model registry with TitaNet and CAM++ options. Benchmarked on real HiDock audio:
- TitaNet: same-speaker 0.855, cross-speaker 0.745, **gap 0.110**
- CAM++: same-speaker 0.945, cross-speaker 0.894, **gap 0.051**
TitaNet has 2x better speaker separation on our audio. Keeping as default.
CAM++ available via `set_speaker_embed_model("campp")`.

- [x] Download CAM++ ONNX model
- [x] Benchmark against TitaNet on real meetings — TitaNet wins on HiDock audio
- [x] Add as configurable option in models.py
- [x] Fix extract_neural_embedding to handle CAM++ input shape (N,T,80)

### Non-speech event anonymization
**Priority: LOW** | From: minutes v0.11.0 | Status: IMPLEMENTED (Apr 11)

- [x] Detect non-speech markers ([laughter], [cough], etc.)
- [x] Strip speaker assignment (speaker_id = -1)
- [x] Wired into diarize pipeline after segment building

### Post-meeting workflow nudges
**Priority: MEDIUM** | From: minutes v0.11.2 | Status: IMPLEMENTED basic (Apr 11)

Subtle status bar nudge after transcription queue completes.
Not a modal — just an info message in the status bar.

- [x] "Tag speakers" nudge when untagged count > 3
- [ ] "Open Voice Training" nudge after 5+ meetings with unconfirmed voices
- [ ] Weekly summary suggestion (Fridays)

### Silence stripping before Whisper
**Priority: MEDIUM** | From: minutes | Status: IMPLEMENTED (Apr 11)

- [x] Preprocess audio: strip silence >500ms, replace with 300ms padding
- [x] Only applied when >5% of audio is stripped
- [x] Adaptive noise floor (quietest 20% × 4x)
- [x] Temp WAV created, cleaned up after Whisper

### Track embedding model version in voice profiles
**Priority: LOW** | From: minutes v0.10.0

Model version already tracked in voice library entries. Enforcement not yet added.

- [ ] Skip matching when model versions differ
- [ ] Re-enrollment prompt when model changes

### Background auto-identification
**Priority: LOW** | From: minutes v0.11.2

After voice library has enough data, retroactively identify speakers in old transcripts.

- [ ] Background job to scan untitled speakers in existing transcripts
- [ ] Match against voice library
- [ ] Update _diarized.json with matched names
- [ ] Notification: "Identified James in 12 meetings"

---

## Rejected / Not Applicable

- **Stem-based energy diarization** — requires separate mic/system audio. HiDock is single recording.
- **Silence-to-padding replacement** — marginal value since hallucination filter catches output. Could revisit if hallucination remains an issue.
- **pyannote-rs segmentation model** — Rust-only, not compatible with our Python pipeline. We use Silero VAD instead.

---

## Reference: Minutes Releases

| Version | Date | Relevant Changes |
|---------|------|------------------|
| v0.10.0 | Apr 6 | Diarization overhaul: running-avg templates, merge pass, voice enrollment activated |
| v0.10.1 | Apr 6 | Speaker attribution improvements |
| v0.11.0 | Apr 8 | GPU whisper, short interjections preserved, non-speech anonymized |
| v0.11.2 | Apr 10 | Lifecycle nudges, multi-agent services, i18n summaries |
