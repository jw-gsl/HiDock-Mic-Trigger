# Diarization quality — current state + recommended switch
Date: 2026-05-18
Branch: feature/voice-training
Related (don't duplicate): [[PLAN-sortformer-diarization-2026-04-23]], [[PLAN-diarization-improvements]]

## Today's evidence (why this came up)

Three recent meeting recordings, all on the **lite** diarizer (Silero VAD + TitaNet + agglomerative cluster + merge):

| File | Duration | Speakers found | Skew |
| --- | --- | --- | --- |
| 2026May18-130417-Rec77 | 38.7 min | 2 | 90% / 10% (S1: 2105s, S2: 220s) |
| 2026May18-123612-Rec76 | 23.6 min | **1** | 100% — every segment collapsed to Speaker 1 |
| 2026May18-112523-Rec75 | 63.2 min | 2 | 79% / 21% |

Rec76 is the smoking gun: a 24-min meeting returning a single speaker means the clustering pass collapsed everyone into one cluster, almost certainly because the post-merge cosine threshold (0.5 once embed_dim ≥ 128, `diarize_lite.py:485-486`) is too lenient for these embeddings on this audio.

## Why the lite pipeline is at its quality wall (recap)

From `PLAN-sortformer-diarization-2026-04-23.md` (still accurate):

1. Only segments ≥ 1.5s get their own embedding; shorter segments inherit a neighbour. Rapid turn-taking → mis-attribution.
2. Post-merge step can over-collapse genuine clusters (we have no signal that the merge was right).
3. Speaker boundary granularity stops at the VAD segment, never at the word.
4. No overlap detection.

These are architectural — tuning thresholds in `diarize_lite` will move the failure mode around (over-merge → under-merge) rather than fix it.

## Current Sortformer readiness — better than the April plan suggested

Checked today on this machine:

- `shared/diarize_sortformer.py` is implemented end-to-end (model load, window+stitch for long audio, speaker-ID normalisation, whisper-segment alignment).
- `shared/pipeline_dispatch.diarize()` already routes `"sortformer"` → that module.
- **NeMo is installed** in the transcription-pipeline venv (`nemo.collections.asr` resolves, alongside `torch`, `soundfile`, `onnxruntime`).
- Only blocker: `~/HiDock/pipeline_backends.json` still has `"diarization": "lite"`. Switching to `"sortformer"` flips the entire pipeline.

Caveats unchanged from the April plan:
- CPU-only on macOS (`convolution_overrideable` blocks MPS). Expect a slower diarize stage; the audio length we deal with is fine for it.
- 4-speaker ceiling. Acceptable for our meetings.
- Sortformer's `diarize_sortformer.py` is still **segment-level** alignment, not word-level — same as lite. Word-level alignment is a separate, additive improvement (Whisper supports `word_timestamps=True`; not wired into `transcribe.py` yet).

## Recommended path

**Step 1 — Switch active diarizer to Sortformer.**
Edit `~/HiDock/pipeline_backends.json` so `"diarization": "sortformer"`. No code change.

**Step 2 — Validate on Rec76 without re-transcribing.**
Use the existing rediarize command on the saved whisper segments:
```
.venv/bin/python3 transcribe.py rediarize \
  "/Users/jameswhiting/HiDock/Raw Transcripts/2026May18-123612-Rec76_diarized.json"
```
Expected: more than one speaker, plausibly 2–4 with a balanced split.

**Step 3 — If Step 2 looks right, rediarize the other recent bad ones (Rec77, Rec75).** Same command. No re-transcription needed because `_whisper.json` files are kept.

**Step 4 — Leave Sortformer active.** New meetings get diarized through it.

**Step 5 (separate, additive) — Word-level alignment.** Switch Whisper to `word_timestamps=True`, replace the segment-level overlap matcher in `diarize_sortformer._assign_words_to_turns` with a per-word version. This is the second-biggest lever the April plan flagged; do it once Sortformer's baseline is confirmed.

## What this is NOT

- **Not a Parakeet question.** Parakeet is an ASR backend. It replaces Whisper, not the diarizer. It can help word-level alignment (returns word timestamps natively) but doesn't directly improve speaker attribution. Worth doing later as a separate change.
- **Not a tuning problem.** The lite pipeline has been tuned (multiple rounds in `PLAN-diarization-improvements.md`); we're past the point where threshold nudges produce real gains.

## Rollback

If Sortformer underperforms (slow, hangs, worse attribution on some recordings), revert by flipping `pipeline_backends.json` back to `"diarization": "lite"`. State.json + raw transcripts are unaffected; only the speaker labels in `_diarized.json` change between runs of `rediarize`.

## Results — measured on 2026-05-18

Backend flipped to `sortformer` and the three failing recordings rediarized with the existing `_whisper.json` segments (no re-transcription):

| File | Duration | Before (lite) | After (Sortformer) |
| --- | --- | --- | --- |
| Rec76 | 24 min | 1 speaker, 40 segs (100 / 0) | 2 speakers, 50 segs, 171 turns (75 / 25) |
| Rec77 | 39 min | 2 speakers, 67 segs (91 / 9) | 2 speakers, 146 segs, 397 turns (58 / 42) |
| Rec75 | 63 min | 2 speakers, 92 segs (21 / 79) | 2 speakers, 214 segs, 1175 turns (54 / 46) |

Plausible meeting splits for all three. Turn counts went from "tens" to "hundreds" because Sortformer captures real back-and-forth that the lite clusterer was collapsing into long mono-speaker blocks. Sortformer wall-clock: ~25 s for a 24-min recording, ~75 s for the 63-min recording (CPU-only on macOS due to MPS conv2d gap — still fast enough).

## What got changed end-to-end

- `~/HiDock/pipeline_backends.json`: `"diarization": "sortformer"`.
- `shared/diarize_sortformer.py`:
  - Fixed `_run_window` parser — NeMo Sortformer returns RTTM-style strings (`"<start> <end> <speaker_id>"`), not tuples or dicts. The original code path would have crashed on the first real model output (and did, on first run).
  - Added voice-library matching (`_resolve_speaker_names`): concatenate up to 10 s of each Sortformer speaker's longest turns, embed via TitaNet, call `identify_speaker(threshold=0.55)`. Parity with the lite path's enrolled-name auto-tagging (was missing).
  - Output schema now matches `diarize_lite.diarize` exactly (`version`, `audio_file`, `segments` with `speaker_id`, `speaker_names` dict). Without this, downstream consumers (the macOS viewer, rediarize stats print, voice-library tagging) would have seen blank `speaker_names`.
  - Same-speaker merge + long-segment split + non-speech anonymisation, reusing the helpers in `diarize_lite`.
  - New `_assign_speakers_word_level` — per-word overlap matching when Whisper emits per-word timestamps. This is the second-biggest lever from the April plan; falls back cleanly to segment-level when word data is absent (the case for already-transcribed files).
- `transcription-pipeline/transcribe.py`:
  - `cmd_rediarize` now imports from `shared.pipeline_dispatch` instead of `shared.diarize_lite` directly. Without this, flipping the backend config had no effect on the rediarize path.
  - Whisper invocations now pass `word_timestamps=True`. New transcriptions will carry per-word data, so Sortformer's `_assign_speakers_word_level` path fires; existing transcripts (no word data in cached `_whisper.json`) fall back to segment-level matching.
- `transcription-pipeline/requirements.txt`: added the full NeMo transitive dep set (see deps section below), pinned `nemo-toolkit==2.5.3`.

## NeMo dep hell — workaround in place, brittle

NeMo's `pip install` story is rough on Python 3.13 / macOS:
- `nemo-toolkit==2.7.x` declares a hard import on NVIDIA's internal `nv_one_logger` package, which is **not on PyPI** (any public mirror). Downgrading to `2.5.3` removes some integration code but the same import still fires from `nemo.lightning.callback_group`.
- `nemo-toolkit[asr]` extras fail to build `kaldialign` from source under pip on macOS — needs a C toolchain we don't otherwise need.
- Several deps are declared but not installed by `nemo-toolkit` itself (hydra-core, lightning, fiddle, lhotse, cloudpickle, sentencepiece, transformers, jiwer, datasets, matplotlib, texterrors, ipython, …). All are now pinned in `requirements.txt`.

Workaround for `nv_one_logger`: a no-op stub package in `.venv/lib/python3.13/site-packages/nv_one_logger/` that satisfies the eager import in `nemo.lightning.one_logger_callback`. Telemetry is inference-irrelevant; the stub returns a permissive `_Anything` proxy that absorbs whatever chain NeMo's constructors call.

This stub is **per-venv** — running `setup-venv.sh` from scratch would have to recreate it. Options for the longer term:
- Vendor the stub into `transcription-pipeline/` and have `setup-venv.sh` drop it into `site-packages` after install.
- Patch NeMo's `one_logger_callback.py` post-install with `sed`/python — fragile across NeMo versions.
- Switch to `pyannote.audio` for diarization — cleaner deps, comparable quality, but uses a HF-gated model (the April plan rejected it for onboarding friction; that calculus shifts now that NeMo's install is also painful).

The first option is what I'd reach for next. For now the venv on this machine has Sortformer running.

## Open questions

- Sortformer is documented as up-to-4-speakers. The three test recordings all came back with 2 speakers in the rendered segments (even when 3 raw IDs were detected internally — the third had no overlap with any Whisper segment after merging). Worth eyeballing on a known-3+-speaker recording if one exists in the library.
- Voice-library auto-tagging in the Sortformer path is wired but untested on this machine (no enrolled speakers / no library data to match against in the test run). Verify next time the user enrols a voice and runs a fresh transcription.
