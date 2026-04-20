# ASR Model Evaluation — Cohere Transcribe vs Parakeet v2 vs Whisper (+ TEN VAD)

Research date: 2026-04-20 (updated with TEN VAD)
Branch: `feature/voice-training`
Sources: HuggingFace Open ASR Leaderboard, Cohere Labs announcement (2026-03-26), NVIDIA Parakeet v2 model card, mlx-community/parakeet-tdt-0.6b-v2, senstella/parakeet-mlx (GitHub), TEN-framework/ten-vad (GitHub + HF), TechCrunch/VentureBeat coverage.

## Context

A user is pushing to replace OpenAI Whisper large-v3-turbo with one of two newer models:
- **Cohere Transcribe** (released 2026-03-26)
- **NVIDIA Parakeet TDT v2** (0.6B, on HF since May 2025; v3 now also exists)

Both models top the Hugging Face Open ASR Leaderboard with better WER than Whisper. The user reports anecdotally "Whisper feels a bit… not brilliant."

## Head-to-head vs Whisper large-v3-turbo

| | Whisper large-v3-turbo | Cohere Transcribe 03-2026 | Parakeet TDT 0.6B v2 |
|---|---|---|---|
| Params | 809M | 2B | 600M |
| File size | 547 MB (q5_0 GGML) / ~1.5 GB (fp16) | ~4 GB fp16 | ~1.2 GB fp16, ~2 GB MLX, ~66 MB CoreML ANE |
| HF Open ASR Leaderboard WER | ~7.4% | **5.42% (#1)** | 6.05% (#2) |
| Licence | MIT | **Apache-2.0** ✓ | **CC-BY-4.0** ✓ (attribution required) |
| Languages | 99 (multilingual) | 14 (en, de, fr, it, es, pt, el, nl, pl, ar, vi, zh, ja, ko) | **English only** ⚠ |
| Automatic language detection | Yes | **No** — must pre-specify ⚠ | N/A (English only) |
| Per-segment timestamps | Yes | **No** ⚠⚠⚠ | Yes (word-level) |
| Built-in punctuation/capitalisation | Yes | Yes | Yes |
| Apple Silicon story | whisper.cpp (Metal), Python via MPS | MLX backend exists (Rust impl confirmed) | **`parakeet-mlx` (senstella) + CoreML via FluidInference + `mlx-community/parakeet-tdt-0.6b-v2`** |
| Memory required | ~2 GB | ~5.6 GB | ~2 GB MLX / 66 MB on Neural Engine |
| Speed on M-series Mac | 3–5× real-time | Unknown, likely slower (2B params) | **~60× real-time** (1h8m audio → 1m2s) |
| Diarization | via our TitaNet path | **No built-in, and no timestamps means our existing diarization can't align speakers to text** ⚠⚠⚠ | via our TitaNet path |

## The Cohere problem (important to surface early)

Cohere Transcribe does **not emit timestamps or speaker diarization**, and does **not do automatic language detection**. Our pipeline is explicitly built around per-segment timestamps:

1. Whisper produces segments with `start`/`end` in seconds.
2. Silero VAD finds speech boundaries independently.
3. `_assign_speakers_to_whisper_segments` walks both lists and assigns each Whisper segment a speaker label based on which VAD segment it overlaps.
4. Transcript writer emits `[MM:SS-MM:SS] **Speaker N:** …` blocks and `_diarized.json` keyed on segment index.

Without timestamps, none of that works. We'd get a wall of unattributed text. Cohere explicitly position this as a limitation in the model card and advise pairing it with a separate forced-aligner (e.g., MMS aligner, wav2vec2-ctc) if you need time info — adding a whole extra model + alignment stage.

**Conclusion**: Cohere Transcribe is a SOTA text-only transcriber, but it's a **step backward for us** unless we bolt on forced alignment. Worth revisiting when/if Cohere Labs ships a timestamped variant.

## Parakeet v2 is the real win

All the user's desired benefits with far fewer integration costs:

- Beats Whisper on WER (6.05% vs 7.4%).
- **Much faster on Apple Silicon**: `parakeet-mlx` reports 60× real-time on M-series; a 6-hour meeting could transcribe in ~6 minutes vs ~90 minutes on Whisper. This alone fixes the timeout/slow-transcription problems.
- CoreML variant uses the Neural Engine with a 66 MB working set — accessible on the cheapest Macs.
- Keeps per-segment timestamps → our existing diarization pipeline still works.
- Active community ports: `mlx-community/parakeet-tdt-0.6b-v2`, `senstella/parakeet-mlx` (Python pip package), `FluidInference/parakeet-tdt-0.6b-v2-coreml`.

**The one real constraint**: English only. For our current usage (UK/US meetings), this is almost always the right default. But we'd need to keep Whisper available for non-English recordings.

## Proposed architecture

Introduce a `transcribe_backend` config setting with three options:

1. **`parakeet-mlx`** (new default for English) — via `parakeet-mlx` Python package.
2. **`whisper-mps`** (current default) — OpenAI Whisper Python, multilingual fallback.
3. **`whisper-cpp`** (bundled builds) — whisper.cpp q5_0 GGML, already wired.

Auto-select logic:
- If user's `WHISPER_LANGUAGE` is `en` or `None` → Parakeet.
- Otherwise → Whisper.
- User can override in Settings.

File layout fits the existing pattern — there's already `transcribe.py` (Python Whisper) and `transcribe_cpp.py` (whisper.cpp). Add `transcribe_parakeet.py` alongside.

## Dependencies

`parakeet-mlx` pulls in:
- `mlx` (Apple Silicon only; skip install on Windows/Linux)
- `numpy` (already have)
- `librosa` (already have for audio loading)
- The 1.2 GB model weights on first run (cache to `~/HiDock/Speech-to-Text/parakeet-tdt-0.6b-v2-mlx/`)

Windows story: Parakeet is not supported on Windows through MLX. We'd keep Whisper as the default on Windows and on non-English audio on Mac.

## Attribution requirement (CC-BY-4.0)

We must credit the Parakeet model in any distribution. Plan:
- `About` window: "Speech recognition: Parakeet TDT 0.6B v2 (NVIDIA, CC-BY-4.0)"
- `README.md`: credits section
- `PARITY.md`: note the licence

## VAD replacement: TEN VAD vs Silero (added 2026-04-20)

User also flagged TEN VAD (theten.ai, TEN-framework/ten-vad) as a potential upgrade over Silero.

| | Silero VAD (current) | TEN VAD |
|---|---|---|
| Size | ~2 MB ONNX | ~306 KB shared library |
| Licence | MIT | Apache-2.0 **with additional conditions** (verify before shipping) |
| Formats | ONNX | ONNX + native C/C++/Python/Java/Go/WASM |
| Apple Silicon | ONNX on CPU (how we run it) | Pre-built macOS arm64 ✓ |
| Training data | Multi-domain, 99+ languages | LibriSpeech + GigaSpeech + DNS Challenge |
| RTF | Baseline | **~32% faster** |
| Transition latency | Several hundred ms | Frame-level ✓ |
| Precision/recall | Industry standard | **Superior on LS/GS/DNS benchmarks** per TEN's docs |

**Assessment for our pipeline:** Real but modest win.

We use VAD in two places, both **offline** (not real-time):
1. Pre-transcription silence stripping (`_replace_silence_with_padding`)
2. Diarization speech-boundary detection

The latency advantage TEN advertises matters for **conversational AI / voice agents** (their stated target market) — not for our batch processing of an MP3. The precision-recall improvement would slightly reduce false-positive speech segments (breathing, room noise) feeding into diarization, which could marginally improve speaker clustering quality.

Net: nice-to-have, not a must-have. Order of priority:
1. Parakeet ASR (large WER + speed win)
2. Better speaker count estimation (already shipped this session)
3. TEN VAD swap (marginal offline gain, non-trivial re-test cost)

**Licence caveat**: "Apache-2.0 with additional conditions" is not pure Apache-2.0. Before shipping, we need to read the actual LICENSE file and verify the additional conditions are compatible with a desktop app distribution. If conditions include things like "no commercial use without notification" or ad-network requirements (which are the typical "additional conditions" patterns), that's a blocker.

## Risk and uncertainty

- Parakeet v2's "24-minute single pass" claim is with full attention — behaviour on very long recordings (6-hour Rec48) may still need chunking. Need to prototype.
- `parakeet-mlx` is a third-party port; stability and maintenance are less certain than NVIDIA's official NeMo path. But the CoreML variant (`FluidInference/parakeet-tdt-0.6b-v2-coreml`) is an escape hatch.
- The `WHISPER_LANGUAGE` config currently defaults to English anyway, so auto-selection is a safe default.

## Completed

- [x] 2026-04-20 Web research on both models (HF Open ASR Leaderboard, Cohere announcement, Parakeet model card, MLX port ecosystem).
- [x] Flagged Cohere Transcribe's missing timestamps as a pipeline blocker.
- [x] Documented TEN VAD as a low-priority future VAD upgrade (licence caveat noted).
- [x] Prototyped `transcribe_parakeet.py` — full backend mirroring Whisper's JSON output contract (segments with start/end/text, _diarized.json, _whisper.json, frontmatter, Whisper-Guard, corrections, diarization, hooks). Installed `parakeet-mlx` 0.5.1 into `transcription-pipeline/.venv`. Import + `--help` verified.
- [x] Added Parakeet to `shared/models.py` MODEL_REGISTRY with `managed_externally: True` (parakeet-mlx uses HF hub cache, not our MODELS_DIR). Added to `requirements.txt` with `darwin + arm64` marker.
- [x] Updated `test_model_paths_resolve_to_models_dir` to skip externally-managed entries. All 329 tests passing.
- [x] 2026-04-20 Documented Cohere-as-third-backend path with forced aligner, flagged MMS-FA CC-BY-NC licence as blocker, identified `torchaudio.functional.forced_align` + wav2vec2-CTC as the commercial-friendly aligner path.
- [x] Drafted tiered user-extensible-backend design (Whisper-compatible custom models as Tier 1 quick win, wav2vec2-family as Tier 2, arbitrary-architecture plugin system deferred as Tier 3).
- [x] Sketched Models Manager UI showing backend radio group + custom model list + aligner language sub-management.

## Planned

- [ ] **Benchmark Parakeet vs Whisper on 5 representative recordings** (1:1, small group, large group, short, long). Compare WER on a known-good passage + wall-clock time. This is the decision gate — if Parakeet clearly wins on real HiDock recordings, proceed with rollout. If it's a wash on accuracy but wins on speed, still proceed. If it loses on accuracy for noisy group audio, demote to opt-in.
- [ ] Wire a `transcribe_backend` setting into `config.py` and AppDelegate's transcription subprocess spawner — routes to `transcribe.py` vs `transcribe_parakeet.py` based on backend choice + language detection.
- [ ] **Models UI gap**: the Models Manager checks `installed` by looking at `MODELS_DIR / filename`. Parakeet's managed-externally cache means it will always show "Not installed". Either add an `is_installed(key)` function that understands external caches, or add a one-time post-install hook that symlinks the HF cache into MODELS_DIR for display purposes.
- [ ] Add a backend selector UI — probably in Models Manager as radio buttons or a segmented control at the top, since the user's mental model is "which speech recognition model am I using".
- [ ] Spike Cohere + forced aligner as a second backend: use `ctc-forced-aligner` or WhisperX's wav2vec2 CTC aligner to add word-level timestamps to Cohere's text output. Decision: if Parakeet's quality is "good enough" and Cohere+aligner is "marginally better but slower", ship Parakeet only and leave Cohere as a spec waiting for a timestamped variant.
- [ ] Update About window + README with CC-BY-4.0 attribution for Parakeet.
- [ ] Update `PARITY.md` — Mac gets Parakeet default, Windows stays on Whisper.
- [ ] Prototype `transcribe_cohere.py` following the transcribe_parakeet.py pattern. Depends on: Cohere 2B weights (4 GB disk, 5.6 GB RAM).
- [ ] Write `shared/forced_align.py` wrapping `torchaudio.functional.forced_align` with a per-language wav2vec2 CTC model lookup. Add aligner models to MODEL_REGISTRY as lazy-loaded per-language entries.
- [ ] Tier 1 custom-model support: "Add Whisper-compatible model" button in Models Manager → text field for HF model ID → writes to `~/HiDock/custom_models.json` → runtime-merged with MODEL_REGISTRY. ~80 lines Python + Swift.
- [ ] Extend the Models Manager UI to match the sketch above — backend segmented control, custom models list, aligner language sub-management for Cohere.

## Cohere as a third backend (added 2026-04-20)

User now wants Cohere as an option even though it has no timestamps — they're happy to add a forced aligner. This makes Cohere viable but not trivial.

### Architecture: audio → Cohere → text → forced aligner → timestamped segments

```
MP3  ──► Cohere Transcribe (2B params, ~5.6 GB RAM)
           │ produces plain text, no timing
           ▼
         Forced aligner (wav2vec2-CTC)
           │ aligns each word to a time range in the original audio
           ▼
         Our standard segments format (start/end/text)
           │
           ▼
         Whisper-Guard → Diarization → Transcript writer
```

### Forced aligner — licence minefield

| Option | Licence | Notes |
|---|---|---|
| `ctc-forced-aligner` (Mahmoud Ashraf) with **MMS-FA** | **CC-BY-NC 4.0** ⛔ | Non-commercial. Blocker. |
| `facebook/wav2vec2-base-960h` | Apache-2.0 ✓ | English only, clean commercial use |
| `jonatasgrosman/wav2vec2-large-xlsr-53-english` | Apache-2.0 ✓ | English, stronger than base |
| `jonatasgrosman/wav2vec2-large-xlsr-53-*` (13 other langs) | Apache-2.0 ✓ | One model per language — matches Cohere's 14 languages |
| `torchaudio.functional.forced_align` | BSD-2-Clause ✓ | The algorithm itself; needs a CTC model to drive it |

**Path**: use the `torchaudio.functional.forced_align` API with a per-language wav2vec2 CTC model. We pre-select the aligner based on Cohere's input language (since Cohere requires pre-specified language anyway, we already have that signal).

### Runtime footprint

- Cohere Transcribe: ~5.6 GB RAM, ~4 GB disk
- wav2vec2 aligner per language: ~360 MB RAM each, ~1.2 GB disk
- **Total if all 14 Cohere languages installed**: ~5.6 + ~18 GB disk. Realistically users would only install aligners for languages they actually record in — UI should let them manage this.

### Integration plan

1. `transcribe_cohere.py` — new backend following the transcribe.py contract. Loads Cohere via `transformers`, runs inference, hands the text + raw audio to the aligner stage.
2. `shared/forced_align.py` — new module wrapping `torchaudio.functional.forced_align` with a wav2vec2 CTC model lookup keyed by language.
3. MODEL_REGISTRY: one entry for Cohere + one entry per wav2vec2 aligner language (lazily downloaded).
4. Backend selector in Models Manager: same segmented control as Parakeet, but with an expandable "Installed languages" sub-list for Cohere.

### When to choose Cohere over Whisper or Parakeet

- Non-English recording in one of Cohere's 14 languages, user values top WER over speed
- English recording where the user has hit a Parakeet limitation (accent, technical vocab) and wants to try the #1 leaderboard model
- Explicit user opt-in in settings — not the default

## User-extensible backends (new section)

User asked: "a way to allow the input of other models, so other model names that it could go and add and get".

### Tiered approach

**Tier 1 — Whisper-compatible custom model (easy)**

Many ASR models on HuggingFace are Whisper-family fine-tunes: `distil-whisper/distil-large-v3.5`, `openai/whisper-large-v3-turbo` (non-quantised), community fine-tunes for domains/languages. All of these use the same `whisper.load_model()` or `WhisperForConditionalGeneration` interface and produce the same segment shape.

UI: in Models Manager, "Add a Whisper-compatible model" button → text field for HuggingFace model ID → download on confirm. Stored in a user-config file (`~/HiDock/custom_models.json`). Added to MODEL_REGISTRY at runtime.

Implementation: ~80 lines. Reuses the existing whisper loader.

**Tier 2 — CTC / wav2vec2-family custom model (medium)**

Lets users swap the aligner or use a different ASR family without code changes. Needs a type hint from the user ("this is a CTC model" / "this is a Wav2Vec2 model") to pick the right loader.

UI: same "Add model" button, with a backend-family dropdown alongside the model ID field.

**Tier 3 — Arbitrary architecture (hard, don't do yet)**

For models that don't fit any of the backends we've already coded (e.g. a novel streaming architecture, a C/Rust binary like whisper.cpp variants). Would need a plugin system where users can drop a Python module into `transcription-pipeline/backends/` implementing a standard interface:

```python
class TranscriptionBackend:
    name: str
    display_name: str
    languages: list[str]
    supports_timestamps: bool
    def is_available(self) -> bool: ...
    def transcribe(self, audio_path: Path, **kwargs) -> TranscriptionResult: ...
```

Defer until we see actual demand. Starts getting into "third-party code execution inside the app" territory which needs a security review.

### Proposed UI (Models Manager)

```
┌────────────────────────────────────────────┐
│ Speech Recognition Backend                 │
│ ( ) Auto (Parakeet for English, Whisper…)  │
│ (•) Parakeet TDT v2 (MLX)         Installed│
│ ( ) Whisper large-v3-turbo        Installed│
│ ( ) Cohere Transcribe 03-2026    Not inst. │
│     └── Aligner languages: [en] [de] [+]   │
│ ( ) Custom: …                              │
│     [+ Add Whisper-compatible model]       │
├────────────────────────────────────────────┤
│ Custom models (2):                         │
│  • distil-whisper/distil-large-v3.5   [×]  │
│  • Volaris/volaris-whisper-ft        [×]   │
├────────────────────────────────────────────┤
│ Voice Detection                            │
│ (•) Silero VAD       [Switch to TEN VAD]   │
│                                            │
│ Speaker Recognition                        │
│ (•) TitaNet                                │
│ ( ) CAM++ (12% better, 512-dim)            │
└────────────────────────────────────────────┘
```

## Rejected / Not applicable

- **Cohere Transcribe without a forced aligner** — no timestamps, breaks diarization. Only viable with an aligner layer.
- **`ctc-forced-aligner` with MMS-FA** — CC-BY-NC, non-commercial. Blocker for Volaris distribution.
- **Parakeet v3** — newer but the ecosystem (`parakeet-mlx`, CoreML builds, attribution practices) is still built around v2. Revisit once v3 MLX ports mature.
- **Cohere via their hosted API** — violates local-first design.
- **Plugin system for arbitrary architectures (Tier 3 custom backend)** — deferred until demand justifies the security / code-review work of executing third-party Python modules.
