# HiDock / HiNotes — what transcription engine do they use, and why is it more accurate than our local pipeline?

Research date: 2026-04-29

Sources (primary, in order of evidence weight):
- `https://hinotes.hidock.com/data-processing-addendum` — names the sub-processors HiDock sends data to (highest-weight evidence: their own legal disclosure).
- `https://www.hidock.com/pages/hinotes` — language count (75) and product feature list.
- `https://hinotes.hidock.com/` — main JS bundle (`/assets/js/index-Q4GamRhg.js`, 8.4 MB), grepped locally at `/tmp/hinotes_bundle/index.js`. SPA — all transcription happens server-side, so the bundle does not directly identify the ASR model. Bundle does reveal: API surface under `/v1/...`, the AI engines exposed to the user, the vocabulary feature, and that "Whisper" in the UI is **HiDock's brand name for "a recording / note"**, NOT a reference to OpenAI Whisper.
- HiDock-Next (`github.com/sgeraldes/hidock-next`) — community OSS project. Implements the local USB / Jensen protocol but does **not** reverse-engineer the HiNotes cloud transcription path.
- OpenAI platform docs for `gpt-4o-transcribe` and `whisper-1`.
- Project files for comparison: `transcription-pipeline/transcribe.py`, `transcribe_parakeet.py`, `shared/whisper_guard.py`, `shared/corrections.py`.

---

## Headline answer

HiDock's own Data Processing Addendum names exactly three AI/cloud sub-processors:

| Sub-processor | Function (verbatim) |
|---|---|
| **OpenAI, L.L.C.** | "AI transcription and summarization" |
| **Anthropic PBC** | "AI summarization" |
| **Microsoft Azure** | "Hosting and storage" |

So **transcription is done by OpenAI**. The DPA does not name a specific OpenAI model. Given that:
- OpenAI's two production speech-to-text models in 2026 are `whisper-1` (Whisper-large-v2-class) and `gpt-4o-transcribe` (released 2025-03, top of OpenAI's WER/accuracy chart, ~99 languages with strong performance on the major ones).
- HiDock advertises "75 languages" and "high-precision" transcription.
- HiDock launched HiNotes' current generation after gpt-4o-transcribe was available and would have no reason to prefer the older `whisper-1`.

**Most likely model: `gpt-4o-transcribe`** (with `whisper-1` as the only other plausible candidate). This is an inference, not a confirmed fact — see "How to confirm" below.

The summarization side is multi-vendor: the bundle exposes `case "claude"`, `case "gemini"`, `case "gpt-5"`, `case "openai"` icons and onboarding copy that lists "AI engines: Claude 4.6, GPT-5.4, Gemini 3.1". So summary engine is user-selectable; transcription engine is a backend-only choice (not surfaced in the UI).

---

## Why HiDock's transcripts are more accurate than ours — likely contributors, ordered by impact

1. **Different ASR model.** We're running Whisper large-v3-turbo (or Parakeet TDT v2 on Apple Silicon — see `PLAN-asr-model-evaluation.md`). HiDock runs (very likely) `gpt-4o-transcribe`. On OpenAI's own benchmarks `gpt-4o-transcribe` is meaningfully ahead of Whisper across noisy / multi-speaker / accented English — exactly the meeting-room conditions we're transcribing. WER differences of 3–5 absolute points are realistic between Whisper-large and gpt-4o-transcribe on hard audio.

2. **Custom Vocabulary.** HiNotes ships a Vocabulary feature — confirmed by the API surface in the bundle (`/v1/vocabulary/list`, `/v1/vocabulary/create`, `/v1/vocabulary/delete`) and confirmed on the marketing page (Vocabulary ✔ across all tiers). Users add domain terms (people names, company names, product names, jargon) and the backend feeds them into the ASR as biasing / initial prompt. This is a step-change for accuracy on names and acronyms — and that is exactly the class of error that's most visible to a user reviewing a transcript. **Our pipeline has nothing equivalent.** We have `shared/corrections.py` (post-hoc string replacement) and `shared/whisper_guard.py` (hallucination filter), but no real-time biasing.

3. **Audio preprocessing tuned to HiDock hardware.** The HiNotes backend knows the device firmware, mic placement, sample rate, and codec. They can apply matched noise-suppression / AGC / silence-trim before ASR. We're handing the .hda → MP3 stream straight to Whisper / Parakeet without device-aware preprocessing.

4. **Model size and compute.** A cloud backend can run a 4–8 GB ASR model with no latency budget (their UI is async — "transcribed" status appears later). We're constrained by what fits comfortably on the user's laptop. Even ignoring model architecture, the cloud version can use a higher-quality pass without us noticing.

5. **Language hint.** HiNotes can require the user to pre-select language (or detect once, server-side, with high confidence). Whisper's auto-detect occasionally locks onto the wrong language for the first 30s of a recording, and the rest of the transcript inherits that error. Our config has `WHISPER_LANGUAGE` defaulting to None for some flows.

What is **not** the explanation:
- It's not summarization. The user said "transcripts" — that's pre-summary text. Claude / GPT / Gemini pick is irrelevant to transcript accuracy.
- It's not the device. Both HiDock's app and our pipeline read the same `.hda` (or downloaded `.mp3`) bytes — the audio is identical at the point of upload / transcription start.

---

## What we don't know (and could verify)

- **The exact OpenAI model HiDock invokes.** `gpt-4o-transcribe` is the leading guess but HiDock has not disclosed it. Confirmation paths:
  - **Network capture in the browser** while transcribing in HiNotes. The `/v1/user/device/file/upload` request is the audio submission. Subsequent polling on `/v1/user/device/file/list` or `/v1/note/whisper/list` returns the transcript. Headers / response payloads might leak a model name in metadata (e.g. `x-openai-model`). Low effort — Chrome DevTools → Network → filter by file/transcript.
  - **Side-channel timing**. `gpt-4o-transcribe` and `whisper-1` have different per-minute throughput characteristics on the OpenAI API. A 30-minute recording transcribed in <2 min strongly suggests gpt-4o-transcribe; ≥8 min is more consistent with whisper-1.
  - **Quality probe**. Submit a 30-second clip with a known difficult passage (proper nouns, accented speakers, numbers). Compare HiNotes output to our local Whisper output. If HiNotes nails proper nouns we can't get without vocabulary, that's vocabulary doing the work — not just a stronger ASR.
- **Whether vocabulary biasing is achieved via OpenAI's `prompt` parameter** (which both `whisper-1` and `gpt-4o-transcribe` support) or via a server-side rewrite step. The user-facing effect is the same.

---

## What we can do about it (options for our pipeline)

These are choices, not commitments. Each has a cost/quality tradeoff.

### Option A — Add a Vocabulary feature to our local pipeline
- Lowest-risk, highest-leverage move. Whisper takes an `initial_prompt` parameter; passing a comma-separated list of the user's vocabulary terms biases the model toward those spellings. Parakeet's MLX wrapper has analogous hotword support.
- New shared module `shared/vocabulary.py` + UI in Models Manager / Settings → "Vocabulary" tab. Persist as `~/HiDock/vocabulary.json`.
- This is the closest behavioural match to what HiNotes is doing for free, and it lands within our existing local-first design.
- Caveat: Whisper's `initial_prompt` is ~224-token-bounded. Need to think about prompt construction (most relevant N terms, or speaker-name terms first).

### Option B — Add `gpt-4o-transcribe` (or `whisper-1`) as an opt-in cloud backend
- New `transcribe_openai.py` following the existing `transcribe_*.py` contract.
- Pros: directly closes the model-quality gap with HiNotes.
- Cons: violates local-first (audio leaves the machine), introduces an OpenAI API key dependency and per-minute cost. Probably worth shipping **gated** and **off by default**, with a clear UI banner explaining where the audio goes. Mirrors the design we already sketched for Cohere Transcribe in `PLAN-asr-model-evaluation.md`.
- If we ship this and Vocabulary together, we'd cover both the model-quality and the names-and-jargon axes that drive the perceived accuracy gap.

### Option C — Tighter audio preprocessing
- Lower-leverage than A or B but cheap. We already use Silero VAD; adding adaptive AGC + a denoiser pass (`rnnoise` / `noise-suppression`) before transcription is a small refactor in `shared/audio_utils.py` and `transcribe.py`.
- Worth doing only after A. Won't on its own close the gap.

### Option D — Force language hint
- One-line change: set `WHISPER_LANGUAGE = "en"` (or per-recording user override) and stop relying on auto-detect for English-default users. We should already be doing this; verify.

### Recommended sequence
1. **Option A (Vocabulary)** — biggest perceived-accuracy win per unit work, stays local-first.
2. **Option D (force language)** — sanity check, free.
3. **Option B (OpenAI backend)** — opt-in, billed, the "match HiNotes exactly" escape hatch.
4. **Option C (preprocessing)** — only if A+B don't close the gap on noisy recordings.

---

## Open questions for the user

1. Is Option A (local Vocabulary biasing) something we should plan out properly, or should I leave this research in place and you'll decide later?
2. Are you OK with us adding a cloud-backed transcription option (Option B), gated behind a setting and BYO API key, or do you want the pipeline to stay strictly local?
3. Do you want me to do the network-capture confirmation step (open HiNotes in DevTools and capture an upload+transcribe round-trip) before we commit to which OpenAI model HiDock is using? It would take 5 minutes of you transcribing a recording while I watch the requests.

---

## Side findings (worth a separate note)

- **Secret check:** `transcription-pipeline/config.json:2` has a HuggingFace token (`hf_…`) committed in plaintext. Looks like a real token. Recommend revoking it on huggingface.co and moving to env var or per-user config not tracked by git. Independent of the transcription-engine question, this should be addressed.
- **`audionotes-preview.hidock.com` and `d3v-preview.hidock.com`** appear in the bundle but only as avatar-image hosts — no relation to transcription. Not a backend ASR endpoint.
- **HiNotes UI string mapping**: `"whisper" → "note"` and `"whisper" → "recording"` in their i18n table. If we ever cross-reference HiNotes feature names with our own, "Whisper" in their app = a captured recording / note, **not** the ASR model. Our `PLAN-hinotes-web-feature-mining-2026-04-21.md` uses the term consistently with their meaning already; nothing to fix there, but worth flagging for any new docs to avoid confusion with OpenAI Whisper.

## Completed
- [x] Verified the cached HiNotes bundle was gone and re-fetched it (`/tmp/hinotes_bundle/`).
- [x] Confirmed AI engines exposed in the UI are summary engines, not ASR.
- [x] Found the API surface (`/v1/...`) and confirmed transcription happens server-side.
- [x] Confirmed Vocabulary feature exists via `/v1/vocabulary/*` endpoints + marketing page.
- [x] Pulled HiDock's DPA — names OpenAI as the transcription provider, Anthropic + OpenAI for summarization, Azure for hosting.
- [x] Cross-checked OpenAI model line-up (whisper-1 vs gpt-4o-transcribe) against HiDock's "75 languages" claim.

## Planned (only if user wants to act)
- [ ] Vocabulary feature — design + implement (Option A above).
- [ ] OpenAI backend — opt-in cloud transcription path (Option B).
- [ ] Force-language config check (Option D).
- [ ] Network-capture confirmation of HiDock's ASR model.
- [ ] HuggingFace token rotation in `transcription-pipeline/config.json`.
