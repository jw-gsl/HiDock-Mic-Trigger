# Upgrade diarization + ASR using NeMo Sortformer + Parakeet
Research date: 2026-04-23
Source: colleague-shared `~/Downloads/transcribe.py` (Christopher Laidler)
Current baseline: `shared/diarize_lite.py` (Silero VAD → TitaNet embeddings → hierarchical clustering → post-merge) + Whisper large-v3-turbo-q5_0.

## Current state — where the quality wall is

Today's diarizer produces extreme speaker-skew artefacts on genuinely balanced conversations. Rec53 (55-min, 2 real speakers) most recent run:
- Before the `_assign_speakers_to_whisper_segments` fix: 95% / 5%.
- After the default-speaker-0 fix (commit 91205ee): 50% / 50% aggregate, but **only 19 turn-runs detected across 55 minutes**, with one 22-minute "Speaker 2" block — strongly suggests mis-attribution within long turns even though the aggregate balances.

Root causes in our current pipeline:
1. Only 53 of 177 VAD segments had ≥1.5s duration for embedding. Segments <1.5s inherit their neighbour's speaker label — so rapid turn-taking collapses into whichever speaker was "around". See `_MIN_EMBEDDING_DURATION = 1.5` in `shared/diarize_lite.py`.
2. Post-merge step collapsed 4 discovered clusters into 2 via distance threshold 0.336. If that merge was wrong, two real speakers are conflated; if right, we're fine — but we can't tell.
3. We assign whole whisper segments (multi-word chunks) to VAD segments via overlap. Speaker granularity stops at the VAD boundary, never at the word.
4. No overlap detection — simultaneous speech gets one label.

## Colleague's pipeline — what's different, ordered by impact

The full pipeline in `~/Downloads/transcribe.py` (Christopher Laidler, ~29KB, zero-config):

1. **Sortformer end-to-end neural diarization** (`nvidia/diar_sortformer_4spk-v1`). No VAD + embedding + clustering two-stage dance — Sortformer outputs speaker turns directly as a transformer inference. Runs in windows (300s with 30s overlap for long audio) and stitches across windows. This is the single biggest quality lever — state-of-the-art on academic benchmarks, purpose-built for conversational diarization rather than our ad-hoc pipeline.

2. **Word-level speaker alignment.** Parakeet ASR returns word-level timestamps. Each *word* is matched to the speaker turn with maximum overlap, then consecutive same-speaker words are grouped into segments for export. Our current pipeline assigns segments at the Whisper-segment level (typically 1-10s chunks), which is why long mono-speaker runs survive to the output. Word-level alignment would break those runs wherever a real speaker switch happens mid-sentence.

3. **Parakeet TDT 0.6B v2 for ASR** (`nvidia/parakeet-tdt-0.6b-v2`). Already on the user's disk. English-only, tops the HuggingFace Open ASR Leaderboard in English, ~60× real-time on Apple Silicon via MLX. Loses Whisper's 99-language coverage — acceptable for UK-based English meetings.

4. **TEN VAD** replacing Silero VAD. 306KB model (tiny), reports "sharp boundaries". Smaller win than #1–3 but a drop-in improvement.

5. **Chunking + silence-split strategy.** 20-min chunks split on silence gaps >0.5s, 0.15s padding around speech edges to avoid clipping boundary words. Cleaner than our monolithic Whisper pass.

## Costs of adoption

- **NeMo as a dependency.** `nemo.collections.asr` + torch + all NeMo satellites — ~2GB install into the transcription-pipeline venv. The only option that pulls in a lot of extra weight.
- **Sortformer is CPU-only on macOS.** Its conv2d stack isn't supported on MPS (`convolution_overrideable` falls through). Expect 2-3× slower than our MPS-Whisper path — but still acceptable for meeting-length audio. `transcribe.py` in the colleague's file explicitly notes this and forces CPU.
- **Sortformer caps at 4 speakers.** Fine for our meeting use cases; not a regression.
- **Parakeet loses non-English languages.** Needs a language-detect branch if we ever need non-English support. Low priority.
- **Rewrite scope**: `shared/diarize_lite.py` is ~1000 lines; the Sortformer-based replacement would be ~400 lines. We'd keep `voice_library_lite.py` (speaker name matching against a user's library) and wire it on top of Sortformer's output.

## Proposed integration — smallest coherent step

**Minimum viable swap:**

1. Add `nvidia/diar_sortformer_4spk-v1` to `MODEL_REGISTRY` in `shared/models.py` with `role: "diarization"`, `size_mb: ~250`, experimental flag until stable.
2. Add `shared/diarize_sortformer.py` exposing the same `diarize(audio_path, whisper_segments, n_speakers) -> dict` signature as `diarize_lite.diarize`. Keep `diarize_lite.py` in place as a fallback.
3. In `transcribe.py`, pick the backend via a config flag (`DIARIZER = "sortformer" | "lite"`, default "lite" at first, flip default once confirmed on James's recordings).
4. Extend Whisper's output to include word-level timestamps (`word_timestamps=True` in `whisper.transcribe` — already supported, no model change).
5. Replace segment-level speaker assignment with word-level in `transcribe.py` where it currently consumes `diarize_lite`'s output.
6. Verify on Rec53: target is for the 22-minute Speaker-2 block to break into multiple turns matching real speaker switches.

**Stretch (separate PRs):**
- Wire Parakeet as the ASR backend behind a config flag (model weights already downloaded). Flip the UI's ACTIVE/EXPERIMENTAL badge accordingly.
- Consider TEN VAD as an alternative to Silero (smaller, potentially sharper). Low priority.

## Measuring success

On Rec53 specifically:
- Before (current): 19 turn-runs, longest 22.8 min, aggregate 50/50.
- Target (with Sortformer + word-level): expect on the order of 100+ turn-runs over 55 min for a genuine 2-speaker conversation, with no runs longer than a minute or two.

If a meeting is actually 4 speakers, Sortformer handles up to 4 natively; our current `_MIN_SPEAKERS_FOR_LONG_AUDIO = 2` prior would bias against that.

## Rejected alternatives considered

- **Tune the existing pipeline** (drop `_MIN_EMBEDDING_DURATION`, use a shorter-segment embedding model, tune post-merge threshold). Real improvements possible but fundamentally limited by the two-stage VAD → cluster approach; Sortformer supersedes the whole architecture.
- **Swap VAD only** (Silero → TEN VAD). Small win, doesn't address the cluster-merge problems.
- **Use pyannote's `speaker-diarization-3.1`** instead of Sortformer. Comparable quality, but pyannote gates behind HuggingFace access token acceptance — more friction for user onboarding. Sortformer has the cleaner distribution story.

## Planned

- [ ] Draft `shared/diarize_sortformer.py` with the `diarize(...)` contract matching `diarize_lite.diarize`.
- [ ] Add Sortformer model to `shared/models.py` MODEL_REGISTRY (with download helper; model lives in HF cache).
- [ ] Wire word-timestamp support into Whisper call in `transcribe.py`.
- [ ] Replace segment-level alignment with word-level in `transcribe.py`'s diarized path.
- [ ] Config flag `DIARIZER` in `config.py` (default "lite", flip once verified).
- [ ] Smoke-test on Rec53, 2026Apr17-130532-AiAccTrans.wav, 2025Sep10-095421-Rec53 (known meetings with known speakers).
- [ ] Update Models UI to surface the Sortformer role + download state.
- [ ] Stretch: Parakeet as ASR backend behind `ASR_BACKEND` flag.

## Notes + questions before implementing

- Worth an honest probe first: does NVIDIA's license on Sortformer + Parakeet let us bundle them for our use case? Sortformer weights on HF: CC-BY-4.0 per the model card (same as Parakeet). Fine for personal + non-commercial use — check terms for distribution to other Volaris BUs.
- The colleague's file is zero-config and opinionated (Parakeet + Sortformer). Ours has more surface (downloads UI, voice library, summarization pipeline). Integrating Sortformer alongside keeps our product breadth; we're not swapping to a minimal CLI.
- Rough effort: ~1-2 focused work blocks for the minimum viable swap, assuming no NeMo install surprises on Apple Silicon.
