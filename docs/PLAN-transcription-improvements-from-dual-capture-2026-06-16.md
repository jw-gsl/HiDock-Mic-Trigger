# Transcription workflow improvements — learnings from the dual-capture test

Date: 2026-06-16
Source: A/B of the same 1:08:54 event captured on two devices —
`2026Jun12-215921-Rec02.mp3` (HiDock, MP3 96 kbps, 49.6 MB) vs
`Plaud/2026-06-12/2026-06-12 21-59-24.mp3` (Plaud, Opus ~34 kbps, 17.6 MB).
Both transcribed by the same whisper.cpp pipeline.

## What the test showed
- **Mic pickup, not codec bitrate, is the limiter.** On the clean close-mic presenter the two
  transcripts are near-identical; on room / secondary-speaker / casual-overlap audio the **Plaud
  was clearly better** (e.g. "holistic view to help with marketing … ROI" vs HiDock's "Martin's
  family"; "Brevora had the curse of going first" vs "curse phone first").
- **The two transcripts are complementary** — each fixes errors the other makes (Plaud got
  "pressure tested" / "personas" right; HiDock got "prove it" right).
- 34 kbps Opus is **not** a quality compromise for transcription; the 2.8× smaller file is codec
  efficiency.

## Recommendations (priority order)

### 1. Fuse same-event, cross-device transcripts (best-of-both)  ⭐ headline
The app already detects duplicate recordings (`merge-candidates` / `merge_groups.json`). A HiDock +
Plaud pair of the same event is exactly that: start times within seconds (21:59:21 / 21:59:24) and
**identical duration** (4134 s). When such a pair is detected, produce a **fused** transcript —
choose the higher-confidence segment per time window — instead of treating them as independent.
The test shows a fused transcript would beat either source alone.
- Needs per-segment confidence (#2) to choose well; without it, fall back to "prefer the better
  source" (#3).

### 2. Capture & surface Whisper confidence  ⭐ enabler + standalone value
`transcribe_cpp.py:153–158` keeps only `start/end/text`. pywhispercpp segments expose token
probabilities — capture per-segment `avg_logprob` / `no_speech_prob` (and a `compression_ratio`
proxy) into the `_whisper.json`. Unlocks:
- Flag **low-confidence segments** for human review in the UI (esp. the casual/overlap sections).
- The per-segment pick in #1.
- **Hallucination/repetition detection** (compression_ratio > ~2.4).
Standalone value even if #1 is never built.

### 3. Prefer the better-mic source when duplicates exist
When a same-event HiDock+Plaud pair exists, default to transcribing the **Plaud** source for
multi-speaker/room content (better mic array: 4 MEMS + VPU) — better quality *and* saves
transcribing both. Close-mic single-speaker: either is fine. Pairs naturally with #1's detection.

### 4. Tune decoding for hard audio
`model.transcribe()` uses defaults. Enable **beam search** + **temperature fallback** (whisper.cpp
supports both) to improve the overlapping/casual sections where both transcripts degrade. Costs
some speed; gate it behind a "high accuracy" option or apply only to low-confidence re-runs.

### 5. Diarization: prefer the better-mic source
Speaker separation should benefit from the Plaud's mic array. Worth diffing the two
`_diarized.json` to confirm, then prefer the better source for diarized output.

### 6. Storage: small Opus is validated
No action — confirms that keeping the compact Plaud files (and not pushing for higher capture
bitrates) costs nothing in transcription quality.

## Suggested first step
#2 (capture confidence) is low-risk, self-contained, and unlocks #1/#4. Then #1+#3 as a "same-event
fusion" feature on top of the existing merge-candidates machinery.

## Notes
- Pipeline files: `transcription-pipeline/transcribe_cpp.py` (whisper), `usb-extractor/extractor.py`
  (`merge-candidates`), `merge_groups.json`.
- Not yet implemented — advisory from the A/B test.
