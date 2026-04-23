# Eval suite — measure pipeline accuracy on curated ground-truth audio
Research date: 2026-04-23
Status: plan only — not implemented
Related: `docs/PLAN-self-improving-pipeline-2026-04-23.md` (consumer), `docs/PLAN-sortformer-diarization-2026-04-23.md` (motivator)

## Why

Every model/pipeline change we ship is currently validated by eyeball. We noticed the 95%/5% Rec53 diarization skew because it was visibly broken; we measured the fix by looking at one print statement. That works for screaming-obvious regressions and fails silently for the subtle ones. Chris Laidler's feedback — *"this desperately needs evals"* — is correct: if the Model Manager lets users swap backends, and the self-improving pipeline auto-proposes new ones, neither is trustworthy without a number that says "this is X% better than what we had".

We need a standing eval suite: **curated ground-truth audio + reference transcripts, run the pipeline against it, emit a closeness-percentage per metric.**

## What "perfect ground truth" looks like in practice

Three tiers of ground truth, from cheapest to most valuable:

1. **Public academic benchmarks.** LibriSpeech test-clean / test-other, AMI Meeting Corpus, VoxConverse. Decades of papers use these; they come with exact reference transcripts and speaker turn annotations. Great for benchmarking against the field; less representative of *our* actual audio (professional microphones, clean rooms, not HiDock-on-a-desk meeting audio).

2. **Our own recordings, hand-corrected.** Pick 5–10 representative meetings from `Raw Transcripts/` and correct them to near-perfection: exact word-level transcript, exact speaker labels with timestamps. This is *our* audio — HiDock-specific acoustics, our real meetings, the quirks our pipeline actually needs to handle. Cost: ~1 hour of human editing per meeting-hour of audio, up front, then amortises over every future eval.

3. **Synthetic ground truth.** Script a short dialogue, record it yourself reading both parts (or with a friend), timestamped manually. Repeatable, cheap, good for catching specific failure modes (rapid turns, overlapping speech, noise). Not representative of general-purpose meeting audio.

Start with Tier 2 — it's what the pipeline needs to be good at, and the audio is already on disk. Tier 1 as a secondary benchmark. Tier 3 for targeted regression tests.

## Dataset structure

```
shared/evals/data/
├── meetings/
│   ├── 2026Mar15-VolarisAI-weekly.mp3
│   ├── 2026Mar15-VolarisAI-weekly.json      # ground truth
│   ├── 2026Apr01-product-review.mp3
│   ├── 2026Apr01-product-review.json
│   └── ... (5–10 total)
├── synthetic/
│   ├── 2speakers-rapid-turns.wav
│   ├── 2speakers-rapid-turns.json
│   └── ...
└── benchmarks/
    └── README.md  # how to fetch LibriSpeech / AMI externally (too big to vendor)
```

Each ground-truth JSON:
```json
{
  "audio_file": "2026Mar15-VolarisAI-weekly.mp3",
  "duration_s": 2850.0,
  "speakers": ["James", "Maria", "Jonas"],
  "segments": [
    {"start": 0.0, "end": 3.2, "speaker": "James", "text": "Right, let's kick off."},
    {"start": 3.2, "end": 5.8, "speaker": "Maria", "text": "Sure, I can go first."},
    ...
  ]
}
```

Keep the schema identical to what `diarize_lite.diarize()` produces so the comparison is apples-to-apples.

## Metrics

| Metric | What it measures | Target |
|---|---|---|
| **Word Error Rate (WER)** | Transcription accuracy — lower is better. WER = (subs + inserts + deletes) / reference word count. | < 10% on our meetings |
| **Character Error Rate (CER)** | Transcription accuracy at character level, more forgiving of contractions and punctuation. | < 5% |
| **Diarization Error Rate (DER)** | Who-spoke-when accuracy — includes speaker confusion, missed speech, false alarms. | < 15% on 2-speaker meetings |
| **Speaker F1** | Per-turn speaker attribution: precision and recall of correctly-labelled turns. | > 0.85 |
| **Turn-count ratio** | `predicted_turns / reference_turns` — catches the "one 22-minute Speaker-2 block" failure mode. | 0.8–1.2 |
| **Wall-clock time** | seconds of pipeline runtime per minute of audio | < 30s/min on Apple Silicon |
| **Dependency weight** | sum of installed model + pip package sizes | informational |

Primary reported headline: **"Transcription 92.4% accurate · Diarization 87.1% accurate"** — one number per stage, as a closeness percentage (100 minus the error rate). Detailed metrics as an expandable breakdown.

## Scoring as a "closeness percentage"

User said: *"see how close it gets on a percentage basis."* Mapping each metric to a 0–100% score:

- Transcription: `100 - WER%` (clamped at 0).
- Diarization: `100 - DER%`.
- VAD: `F1_score × 100`.
- Overall pipeline: geometric mean of the above, weighted by user priority (transcription usually matters most).

Every eval run produces an `EVAL-<date>-<backend-combo>.md` file with:
- Headline closeness % per stage
- Per-meeting breakdown
- Worst-case examples (meeting where WER was highest, which gets investigated manually)
- Wall-clock timing
- Diff against the prior eval run for the same backend combo

## Ingestion path from current work

We already have:
- 227 transcribed meetings in `Raw Transcripts/`
- Matching MP3s in `Recordings/`
- James tagging speakers as he reviews them — those corrected labels are partial ground truth

Step 1: export a "candidate ground truth" from James's hand-tagged diarized JSONs. For each, the speaker labels are known-correct (James edited them); the transcribed text is approximate (Whisper). So we have ground-truth diarization but not ground-truth transcription.

Step 2: pick 5–10 of those meetings; hand-correct the transcript to exact. That gives us a full ground-truth set with ~1 hour of editing.

Step 3: anything user-edits going forward automatically counts as additional ground truth, if they opt-in a meeting to the eval set (one checkbox in the UI).

## Eval runner

```
shared/evals/
├── runner.py         # orchestrates: load ground truth, run pipeline, compute metrics
├── metrics.py        # WER / DER / F1 / turn-count implementations
├── compare.py        # diff two result files for PR bodies
└── report.py         # pretty-print the EVAL-<date>-*.md output
```

Entry points:
```
python shared/evals/runner.py baseline                     # run current active backend
python shared/evals/runner.py candidate --transcription=parakeet --diarization=sortformer
python shared/evals/compare.py EVAL-2026-04-23-baseline.md EVAL-2026-04-25-candidate.md
```

Wall-clock timing measured with `time.monotonic()` around each stage, so the report carries timing deltas alongside accuracy deltas.

## Integration points

**Self-improving pipeline** (`docs/PLAN-self-improving-pipeline-2026-04-23.md`): the agent proposing a new backend **must** run the eval on it and include the comparison table in the PR body. This is the gate that makes automation trustworthy.

**CI**: add a lightweight eval job that runs on every PR — only against `synthetic/` recordings (fast, deterministic) so PRs get a quality signal without multi-hour CI runs. Full eval runs weekly on schedule with a bigger dataset.

**Model Manager UI**: "Last eval: 91.7% overall · ran 3 days ago" line in the header. If the user opens the Model Manager after changing the active backend, prompt them to kick off an eval run to see the effect.

**CLAUDE.md**: add a rule that fixing any quality issue requires updating or adding an eval case that would have caught it.

## Rejected alternatives

- **Only academic benchmarks.** Doesn't capture HiDock-specific acoustics, our meeting patterns, or our speaker count distribution.
- **Only user-facing "rate this transcript 1–5".** Too subjective, too noisy, doesn't decompose into per-stage signal.
- **Zero eval; trust the leaderboards.** What we do today. Blind to our actual use case.

## Planned

- [ ] Pick 5 representative meetings from `Raw Transcripts/` for Tier-2 ground truth.
- [ ] Hand-correct each transcript to exact (~5 hours of work, one-time).
- [ ] Write `shared/evals/metrics.py` (WER / DER / F1).
- [ ] Write `shared/evals/runner.py` (baseline + candidate modes).
- [ ] Write `shared/evals/compare.py` (report generator).
- [ ] Run baseline with current active backends (Whisper + lite); commit EVAL-baseline.md.
- [ ] Add a `synthetic/` dataset: 3 hand-crafted 30–60s clips targeting rapid turns, overlap, noise.
- [ ] Wire CI to run synthetic evals on PRs.
- [ ] Add the Model Manager header line (current eval score + age).
- [ ] Feed the eval runner into the agent brief for the self-improving pipeline.

## Open questions

- Where do hand-corrected transcripts live? In `shared/evals/data/` (checked in) → gives us portability and diff-able history; but that checks meeting audio into the repo (privacy?). Probably needs `.gitignore` for audio and a separate private ground-truth store.
- How many meetings is "enough"? 5 is probably the floor for meaningful numbers; 20 would be significant; beyond that hits diminishing returns against the cost of human correction.
- Do we care about language-specific breakdowns? All current meetings are English; multilingual becomes interesting only once Parakeet (English-only) vs Whisper (99 languages) tradeoff matters in practice.
- What's the minimum viable first version? Probably: `metrics.py` with WER+DER, 3 hand-corrected meetings, a runner that dumps one `EVAL-*.md`, done. Everything else is additive.
