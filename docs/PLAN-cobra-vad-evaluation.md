# Cobra VAD Evaluation + Full-Pipeline Model Audit

Research date: 2026-04-20
Branch: `claude/evaluate-cobra-vad-rU6ny`
Sources: picovoice.ai (Cobra docs, pricing, VAD benchmark blog), github.com/Picovoice/cobra, pypi.org/project/pvcobra, hackster.io (Picovoice free-tier announcement), huggingface.co/pyannote/speaker-diarization-3.1, brasstranscripts.com 2026 diarization comparison, AssemblyAI blog (top diarization libraries 2026), `docs/PLAN-asr-model-evaluation.md`, `docs/PLAN-diarization-improvements.md`, `shared/diarize_lite.py`, `shared/models.py`, `transcription-pipeline/`, `README.md`.

## TL;DR

- **Cobra VAD is accuracy-competitive with Silero and faster, but its licensing model (Picovoice AccessKey + 3-active-user/month free cap) makes it a poor fit for a local-first, zero-network desktop app.** Recommend **do not adopt**. Keep Silero as default, keep TEN VAD on the planned list.
- **Bigger wins available elsewhere in the pipeline that weren't flagged before**:
  1. **Speaker embedding upgrade to WeSpeaker ResNet293** — newer, larger, used by pyannote 3.1, likely materially better than TitaNet Small on our hardest case (group meetings).
  2. **Overlapping-speech detection (OSD)** — currently a silent quality bug: overlap is always assigned to one speaker, losing the other.
  3. **pyannote-audio 3.1 as an alternative diarization backend** — end-to-end segmentation + embedding + OSD in one package. Bigger architectural change; worth prototyping behind a flag.
  4. **distil-whisper/distil-large-v3.5** — drop-in Whisper replacement, ~2× faster, nearly identical WER. Cheap safety-net option alongside Parakeet.

## Context

User noticed that **Cobra VAD (Picovoice)** didn't appear in the earlier VAD research in `PLAN-asr-model-evaluation.md` (which only compared Silero vs TEN VAD). They want:
1. An honest assessment of whether Cobra is the right VAD for us.
2. A fresh look at every stage of the pipeline to see if better models exist than what we currently use or have already prototyped.

## Current pipeline (as of commit 38c9b27 on this branch)

From `README.md:43–127` and `shared/diarize_lite.py`:

| # | Stage | Model(s) | Size | Swappable? |
|---|---|---|---|---|
| 1 | Audio load | ffmpeg decode | — | No (infra) |
| 2 | Audio prep | rule-based (RMS/peak norm, silence strip) | — | No (infra) |
| 3 | VAD | **Silero VAD** (default); TEN VAD (planned) | 2 MB | Yes |
| 4 | ASR | **Whisper large-v3-turbo** (default); Parakeet TDT v2 (prototype); Cohere Transcribe (prototype) | 547 MB–4 GB | Yes |
| 4.5 | Forced alignment (only for Cohere) | wav2vec2-CTC per language | 1.2 GB each | Yes |
| 5 | Text cleanup | rule-based Whisper-Guard + corrections | — | No (infra) |
| 6 | Diarization | **Silero VAD** (reused) + **TitaNet Small** (default) or CAM++ + scipy clustering | 10–28 MB | Yes (embed model only) |
| 7 | Output writing | rule-based | — | No (infra) |
| 8 | LLM summarisation | local CLI (claude/codex/gemini/ollama) | — | N/A |
| 9 | Integrations | rule-based | — | N/A |

## Cobra VAD — detailed evaluation

### Claimed performance (Picovoice docs)

| | Silero VAD (our current) | Cobra VAD | TEN VAD (planned) | WebRTC |
|---|---|---|---|---|
| Architecture | Deep CNN (ONNX) | Proprietary DNN | DNN (native lib) | GMM signal proc |
| Size | 2 MB | Small (not published) | 306 KB | — |
| Claimed accuracy | 87.7% TPR @ 5% FPR | **99% / highest AUC** | Superior to Silero on LS/GS/DNS | 50% TPR @ 5% FPR |
| RTF on CPU | baseline | **~8.6× faster (RTF 0.005)** | ~32% faster | Very fast |
| Licence — code | MIT ✓ | **Apache-2.0 code** but… | Apache-2.0 +conditions ⚠ | BSD ✓ |
| Licence — use | Fully free ✓ | **AccessKey + 3-active-user/month free tier, paid above** ⛔ | Open weights ✓ | Fully free ✓ |
| Network required | No ✓ | AccessKey validation + telemetry likely ⚠ | No ✓ | No ✓ |
| Output | Per-frame probability | Per-frame probability | Per-frame probability | Binary |

### The AccessKey problem (deal-breaker)

Picovoice's free tier is advertised as "commercial-use permitted, no credit card" but capped at **3 active users per month per account**. Beyond that, it's a paid commercial licence. The AccessKey also needs Picovoice Console validation.

For us:
1. **Local-first positioning breaks**: the README advertises "All local, no network, no API keys." Cobra's AccessKey is essentially an API key, and validation typically phones home on first-run.
2. **Distribution ceiling**: once Volaris ships this to more than 3 people in a month, we'd need an enterprise licence. Pricing isn't public, implying $$$.
3. **Friction on install**: every user would need to sign up at picovoice.ai/console, copy a key, paste it into settings. Significant UX regression from "install and it works."
4. **Binary redistribution**: Picovoice's commercial terms typically restrict redistributing the engine binaries, which is awkward for an open-source app.

### The speed/accuracy gain isn't where we need it

Our VAD workload is **two offline passes per recording**, not real-time streaming:
- Pass 1: `_replace_silence_with_padding` before ASR (stage 2 uses RMS heuristic, not VAD — but the point stands for the VAD stage proper).
- Pass 2: `detect_speech_segments` for diarization boundaries.

Silero already runs at several hundred × real-time on our CPU. An 8.6× speedup saves maybe 1–3 seconds on a 1-hour meeting — invisible next to Whisper (~15 minutes) or Parakeet (~1 minute). The accuracy edge is real but modest; Silero is **already good enough**: the remaining quality problems in diarization come from embedding confusion and overlap handling, not from VAD false positives.

### Verdict

**Do not adopt Cobra.** Accuracy/speed wins are real in principle but moot in our offline batch context, and the licensing is incompatible with our distribution model. If we ever build a **real-time voice-agent mode** (always-on mic listening), Cobra would be worth revisiting then — but we don't have that use case today.

Keep TEN VAD on the planned list (open weights, similar precision win, no paid ceiling) as the eventual Silero upgrade — but even that's marginal per `PLAN-asr-model-evaluation.md:104–117`.

## Full-pipeline model audit — where the real upgrades are

### Stage 3: VAD

- **Silero (current)**: Keep. MIT, 2 MB, battle-tested, offline-first.
- **TEN VAD (planned)**: Still the best upgrade path. Verify the "additional conditions" clause before shipping, as already flagged.
- **Cobra**: Rejected (see above).
- **No upgrade urgency here** — VAD errors are not the dominant quality issue in our transcripts.

### Stage 4: ASR — already well-researched

No new finding; `PLAN-asr-model-evaluation.md` already covers Whisper / Parakeet / Cohere exhaustively.

One addition worth putting on the custom-models list:

- **`distil-whisper/distil-large-v3.5`** — drop-in for Whisper large-v3-turbo, ~2× faster on the same hardware with WER within ~1% of the original. Works with the existing whisper.cpp / Python Whisper backends without any code changes (it implements the Whisper interface). Easy win for users who want speed without going Apple-Silicon-only Parakeet. Tier 1 in the user-extensible-backend plan.

### Stage 4.5: Forced alignment — fine as planned

`torchaudio.functional.forced_align` + wav2vec2-CTC per-language is the right path. No newer finding.

### Stage 6: Diarization — **biggest upgrade opportunity**

Current stack is Silero VAD → TitaNet-Small embeddings → scipy hierarchical clustering → running-average merge. This is a 2023-era pipeline hand-assembled to sidestep pyannote's Rust/PyTorch weight. It works but has two known weaknesses:

1. **Embedding quality plateaus on group meetings.** TitaNet Small is 23M params trained on VoxCeleb-scale data. Newer speaker-embedding models trained on 5–10× more data with modern architectures are substantially stronger.
2. **No overlapping-speech detection.** When two people talk over each other, Whisper transcribes the dominant voice and our pipeline assigns it to exactly one speaker — the other speaker's words are silently dropped. Measurable in any real multi-person recording.

#### Option A (incremental): swap TitaNet Small → WeSpeaker ResNet293-LM

- **Model**: `eek/wespeaker-voxceleb-resnet293-LM` (Hugging Face, ONNX available).
- **Architecture**: ResNet293 with Large-Margin loss, state-of-the-art on VoxCeleb SV benchmarks.
- **Why now**: this is the embedding model pyannote 3.1 uses internally. Adopting it standalone keeps our pipeline while upgrading the most important component. ~8 s embedding time per segment per pyannote's own benchmarks — similar budget to TitaNet for our segment lengths.
- **Effort**: add an entry to `SPEAKER_EMBED_MODELS` in `shared/models.py` mirroring the TitaNet/CAM++ pattern. Verify output dimension handling in `extract_neural_embedding` (likely 256-dim). ~30 lines + benchmarking.
- **Evaluation plan**: same benchmark harness we used for CAM++ (`PLAN-diarization-improvements.md:70–82`): same-speaker cosine, cross-speaker cosine, gap. CAM++ lost to TitaNet on our audio (gap 0.051 vs 0.110); ResNet293 is a fresh candidate with a genuinely different training regime, so it deserves its own run.

#### Option B (architectural): pyannote-audio 3.1 as a second diarization backend

- **Model**: `pyannote/speaker-diarization-3.1`.
- **What it gives us**:
  - Neural PyanNet segmentation (replaces our Silero VAD + custom boundary logic).
  - **Overlapping speech detection** — explicitly emits overlap intervals; we could attribute the overlap to multiple speakers or at least flag it in the transcript.
  - WeSpeaker embeddings + built-in clustering tuned jointly with the segmentation model.
  - ~10% DER on standard benchmarks; our home-grown pipeline doesn't publish a DER figure but anecdotal transcript review suggests we're worse on 4+-speaker meetings.
- **Trade-offs**:
  - Pulls in `pyannote-audio`, `torch`, `speechbrain` — large install (~2 GB).
  - Needs a Hugging Face token to download the pretrained pipeline (user gesture once).
  - Slower than our current stack on CPU; fine on Apple Silicon MPS / CUDA.
  - Licensing: pyannote models are MIT, but the gated-model handshake is friction.
- **Proposed integration**: `shared/diarize_pyannote.py` as a parallel path to `diarize_lite.py`. Gate behind `diarize_backend = "lite" | "pyannote"` config. Default stays "lite" until benchmarked.

#### Option C (minimum viable overlap fix): post-hoc OSD on our existing pipeline

If pyannote-audio is too heavy a dep, a middle path:
- Use `pyannote/overlapped-speech-detection` model standalone (~30 MB) to emit overlap intervals.
- In stage 6, when an overlap interval spans a Whisper segment, mark it in the output (`speaker: "Speaker 1+?"` or a separate `overlap: true` field) so the UI can surface "this is a cross-talk moment, transcript may be incomplete."
- Not a quality fix, but at least stops silently eating the second speaker.

### Ranking of upgrades by expected impact

1. **WeSpeaker ResNet293 embeddings** (Option A) — high impact on the main quality complaint ("speaker confusion in group meetings"), low integration cost.
2. **Overlap detection / pyannote 3.1 backend** (Option B or C) — addresses a silent correctness bug, unlocks "show where people talked over each other" UI.
3. **distil-whisper/distil-large-v3.5** as a Tier-1 custom model — cheap speed win for non-Apple-Silicon users.
4. **TEN VAD swap** — still nice-to-have, still marginal.
5. **Cobra VAD** — rejected.

## Completed

- [x] 2026-04-20 Researched Cobra VAD: accuracy benchmarks, licensing model, free-tier terms, AccessKey friction.
- [x] 2026-04-20 Audited every pipeline stage (1–9) for current models and alternatives.
- [x] 2026-04-20 Identified WeSpeaker ResNet293 and pyannote 3.1 overlap detection as the genuinely neglected upgrade paths.
- [x] 2026-04-20 Confirmed Cobra is a bad fit (licensing + gains don't land in offline batch context).

## Planned (pending user direction)

- [ ] Decide: do we want to prototype WeSpeaker ResNet293 as a third `SPEAKER_EMBED_MODELS` entry and benchmark it against TitaNet/CAM++?
- [ ] Decide: pyannote 3.1 as a second diarization backend vs. standalone OSD model vs. no change?
- [ ] Add `distil-whisper/distil-large-v3.5` to the default custom-model suggestions in the Models Manager (Tier 1).
- [ ] Cross-link this file from `PLAN-asr-model-evaluation.md` so future VAD-related research lands in one place.

## Rejected / Not applicable

- **Cobra VAD** — Picovoice AccessKey licensing incompatible with our local-first, zero-key distribution model. Free tier capped at 3 active users/month. Accuracy/speed gains don't land in offline batch context. Revisit only if we add a real-time always-listening mode.
- **Switching to `pyannote` wholesale as the default** — too heavy a dependency change; our lite pipeline is good enough for 74% "Good" output and ships with no HF token handshake.
