# Emotion & Tone Detection in the Transcription Pipeline

Research date: 2026-04-18
Branch: `claude/emotion-tone-detection-Eik58`
Sources: emotion2vec (Ma et al., ACL 2024), SenseVoice (FunAudioLLM 2024), audeering wav2vec2 MSP-dim (Wagner et al., IEEE TPAMI 2023), Odyssey 2024 Emotion Challenge, openSMILE 3.0, MELD dataset (Poria et al., ACL 2019), W3C EmotionML (2014).

## Context: the user's thinking

- Today's pipeline: Whisper (local) → TitaNet diarization. Works well.
- Idea: treat transcript as **what** was said, and carry **how** it was said alongside it (emotion, tempo, prosody). Summaries can then focus on different dimensions depending on what's useful.
- Knowledge graph angle: tying tone + emotion to speaker identity over time lets us query "what triggers anger for person X?" or "what got a positive response?" via the existing MCP server.
- Philosophy: "horses for courses" — small specialist models stacked in a pipeline, not one frontier LLM swallowing raw audio. This fits the existing project pattern (Whisper + TitaNet already follow it).

This is a strong direction. It leverages the diarization investment (emotion is only useful when attributed to a speaker) and the knowledge-graph investment (tone trends are only useful aggregated).

## Current state (where to plug in)

- `transcription-pipeline/transcribe.py:164-180` — post-diarization hook point, between segment assignment and `write_transcript`.
- `shared/diarize_lite.py:37-63` — Silero VAD + TitaNet ONNX, produces speaker-labelled segments.
- `shared/transcript_writer.py:196-229` — writes `{basename}_diarized.json` with `segments: [{start, end, text, speaker, speaker_id}]`. Extension point for per-segment emotion.
- `shared/knowledge.py:33-100` — SQLite knowledge.db schema. Needs a new `utterance_features` table.
- `mcp-server/server.py` — exposes `search_meetings`, `get_meeting`, etc. Needs new tools for emotion queries.
- `Windows-App/core/transcription.py:50-80` — Windows port; must receive the same feature (PARITY.md rule).
- No existing sentiment/emotion code in the repo — greenfield.

## Model landscape (short version)

| Purpose | Pick | License | Why |
|---|---|---|---|
| Categorical SER per segment | **emotion2vec+ large** (Alibaba, FunASR) | Apache-2.0-compatible | Current open-source SOTA, 9 classes, 768-d embedding for future clustering |
| Inline emotion + ASR in one pass | **SenseVoiceSmall** (FunAudioLLM) | Apache-2.0 | Emits `<\|HAPPY\|>`/`<\|ANGRY\|>`/etc. tags inline with transcript; faster than Whisper. Candidate to replace Whisper later. |
| Prosody / tempo / pitch | **openSMILE 3.0** (eGeMAPSv02) | BSD-ish | 88 acoustic features, CPU-only, runs Mac + Windows. Cheap. |
| Text-side emotion | **SamLowe/roberta-base-go_emotions** | MIT | 28 fine-grained emotions; fuses with audio cat for robustness. |
| Speaking rate | derive from Whisper word timestamps | — | No model needed. Tokens ÷ VAD-trimmed duration. |

**Rejected:**
- `audeering/wav2vec2-large-robust-12-ft-emotion-msp-dim` — strong for valence/arousal/dominance, but **CC-BY-NC-SA** (non-commercial). Avoid unless we retrain a V/A/D head on emotion2vec+ embeddings ourselves.
- `ehcalabres/…-RAVDESS` — RAVDESS is acted speech; generalises poorly to meetings.
- NVIDIA NeMo SER — no production checkpoint as of 2026.

## Feature schema (proposed extension to `_diarized.json`)

```json
{
  "version": 2,
  "segments": [
    {
      "start": 12.3, "end": 18.7,
      "text": "...", "speaker": "Alice", "speaker_id": 0,

      "emotion": {
        "audio_cat": "happy", "audio_conf": 0.71,
        "audio_probs": {"happy": 0.71, "neutral": 0.18, "...": 0},
        "text_cat": "joy", "text_conf": 0.64,
        "valence": 0.68, "arousal": 0.41, "dominance": 0.52,
        "fused_cat": "happy", "fused_conf": 0.78
      },
      "prosody": {
        "speaking_rate_wpm": 168,
        "pitch_mean_hz": 192.1, "pitch_stddev": 34.0,
        "loudness_mean": 0.42, "loudness_stddev": 0.11,
        "pause_before_ms": 320,
        "egemaps_sha": "..."
      }
    }
  ]
}
```

Store the raw audio 768-d emotion2vec embedding in a sidecar `.npy` keyed by segment index — enables retuning later without re-running inference.

## Knowledge-graph schema (SQLite)

New table, following the MELD-dataset shape:

```sql
CREATE TABLE utterance_features (
  meeting_id TEXT, segment_idx INT, speaker_id INT, speaker_name TEXT,
  t_start REAL, t_end REAL,
  audio_cat TEXT, audio_conf REAL,
  text_cat TEXT, text_conf REAL,
  valence REAL, arousal REAL, dominance REAL,
  fused_cat TEXT, fused_conf REAL,
  speaking_rate_wpm REAL, pitch_mean_hz REAL, pause_before_ms REAL,
  PRIMARY KEY (meeting_id, segment_idx)
);
CREATE INDEX idx_uf_speaker_emotion ON utterance_features (speaker_name, fused_cat);
CREATE INDEX idx_uf_meeting ON utterance_features (meeting_id);
```

Rolled-up views: `speaker_emotion_trend` (weekly averages per speaker), `meeting_emotion_arc` (emotion trajectory across a meeting).

## New MCP tools (`mcp-server/server.py`)

- `speaker_emotion_profile(speaker, since?)` — baseline + deviations.
- `find_emotion_triggers(speaker, emotion, window_s=30)` — returns the utterances **preceding** a given emotion from this speaker (this is the "what triggers anger for Alice" query).
- `find_positive_responses(topic?)` — utterances followed by joy/gratitude from other speakers.
- `meeting_emotion_arc(meeting_id)` — per-minute valence/arousal timeseries.
- `talk_ratio(meeting_id)` — speaker-time shares (cheap derivation, complements emotion).

## Summary output — what gets surfaced

Extend frontmatter written by `transcript_writer.py`:

```yaml
emotion_summary:
  dominant_overall: "neutral (62%), happy (18%), tense (12%)"
  per_speaker:
    Alice: {happy: 0.41, neutral: 0.48, angry: 0.06, valence: 0.61}
    Bob:   {neutral: 0.70, sad: 0.12, valence: 0.44}
  notable_moments:
    - t: 14:22, speaker: Alice, event: "arousal spike (0.9), cat=angry"
    - t: 31:07, all: "laughter event"
talk_ratio: {Alice: 0.58, Bob: 0.42}
```

Different summary templates can then pull different slices (sales call → talk_ratio + patience; 1:1 → valence trend per speaker; family call → notable_moments).

## Staged rollout

Each stage ships independently, adds value on its own, and is cross-platform by default.

### Phase 1 — Prosody only (low risk, high signal)
- [ ] openSMILE eGeMAPSv02 per diarized segment on macOS path.
- [ ] Derive `speaking_rate_wpm` from Whisper word timestamps (needs `word_timestamps=True` in Whisper call — verify toggle).
- [ ] Extend `_diarized.json` to `version: 2` with `prosody` block; writer backward-compatible on read.
- [ ] Mirror in `Windows-App/core/transcription.py`.
- [ ] Update `PARITY.md`.
- **Why first:** CPU-only, no new model downloads, no license worries, unblocks talk-ratio + pace metrics that Gong treats as half of "engagement".

### Phase 2 — Categorical SER (emotion2vec+)
- [ ] Add `shared/emotion.py` that loads emotion2vec+ large via FunASR once, infers per segment.
- [ ] Cache embeddings `.npy` sidecar.
- [ ] MPS path on Mac, CPU-or-CUDA on Windows — mirror the TitaNet loader pattern.
- [ ] Extend `_diarized.json` with `emotion` block (audio side only initially).
- [ ] Update transcript markdown frontmatter with per-speaker distribution.
- **Evaluation:** hand-label 5 meetings' worth of segments, compare to model; document agreement in this file.

### Phase 3 — Knowledge graph + MCP queries
- [ ] Add `utterance_features` table to `knowledge.db`, migration script.
- [ ] Backfill from existing `_diarized.json` v2 files on first run.
- [ ] Add the 5 MCP tools above.
- [ ] Document queries in `docs/mcp-emotion-queries.md` with examples.

### Phase 4 — Text-side emotion + fusion
- [ ] Add go_emotions on transcript segments.
- [ ] Simple late-fusion rule (start weighted 0.6 audio / 0.4 text, retune on labelled data).
- [ ] Add `text_cat`, `fused_cat` to schema — already placeholders from Phase 2.

### Phase 5 — SenseVoice evaluation (optional, bigger swing)
- [ ] Bench SenseVoiceSmall vs Whisper large-v3-turbo on WER for a representative 2-hour sample.
- [ ] If WER within 10% relative and latency wins, offer SenseVoice as a config flag (`WHISPER_MODEL=sensevoice-small`) — inline emotion tags replace Phase 2 audio cat.
- [ ] Keep Whisper as default until proven.

## Open questions

- Privacy: emotion inference is more sensitive than transcripts. Should the user be able to disable it per meeting or per speaker? Suggest opt-out toggle in both apps + per-meeting frontmatter flag `emotion_analysis: false`.
- Calibration: per-speaker baselines matter (some people sound "angry" when neutral). Store and subtract speaker-baseline valence/arousal from raw values once we have ≥5 meetings of a speaker.
- Short segments: emotion2vec wants ≥1 s. Need to decide behaviour on sub-second turns — skip vs pool with neighbour.
- Model downloads: emotion2vec+ large is ~1 GB. Add to the existing model-cache bootstrap used for TitaNet.

## Rejected / Not applicable

- **Frontier-LLM-on-audio approach** (Gemini audio, GPT-4o audio): user explicitly rejected ("horses for courses"), and it's not local.
- **Audeering MSP-dim** for V/A/D: non-commercial license.
- **NeMo SER**: no production checkpoint.
- **RAVDESS-trained models**: acted speech, won't generalise.

## Completed

- [x] 2026-04-18 Pipeline mapped, model landscape surveyed, schema drafted, phased plan written.

## In progress

- [ ] Awaiting user sign-off on phase ordering and privacy defaults.

## Planned

- [ ] Phase 1 spike: openSMILE integration on macOS path.
