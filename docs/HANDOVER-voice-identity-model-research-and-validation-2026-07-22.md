# Handover: voice-identity model research and validation

**Date:** 2026-07-22  
**Repository:** `/Users/jameswhiting/_git/hidock-tools`  
**Purpose:** give an independent LLM enough evidence to validate or challenge the speaker-identity research, benchmark design, safety gate, and current application integration without reconstructing the work from chat history.

## Review assignment

Please treat this as an audit brief, not as a request to endorse the current choice. Check the raw reports and implementation, identify leakage or selection bias, challenge the statistical claims and thresholds, verify the model and licensing research, and propose the smallest high-value next experiment.

The questions that matter are:

1. Is the evidence genuinely human-verified and correctly linked to bounded audio?
2. Does the leave-one-meeting-out design prevent leakage strongly enough?
3. Is `similarity >= 0.71` plus `winner margin >= 0.21` defensible for a review suggestion?
4. Is top-three-meeting median the right profile scorer?
5. Is the reported zero-error coverage likely to generalise, or is it a small-data/grid-search artefact?
6. Are audio cleanliness and crowded-meeting rules sensible, measurable, and fairly calibrated?
7. Is WeSpeaker ResNet293-LM still the best practical local candidate after testing the next models?
8. Are any source-code, model-weight, training-data, or commercial-use licences incompatible with this app?

## Executive conclusion at handover

WeSpeaker ResNet293-LM is the strongest model tested on this user's local, verified data. With the top-three-meeting median scorer it achieved 96.12% archive top-1 accuracy and 93.57% macro top-1 accuracy. An archive-selected gate of 0.71 similarity and 0.21 winner/runner-up margin accepted 301 of 387 archive cases with no observed error. Applied unchanged to the separate 25-case recent set, it accepted 10 cases, all correct. Combined observed safe-gate coverage was 311/412, or 75.49%, with zero observed false gate-passing results.

That does **not** establish a zero production error rate. The current app therefore uses the model only to present evidence for human review. It never silently applies a name.

Microsoft WavLM Base Plus SV was tested because it is a self-supervised-learning-based speaker-verification model. It was weaker on this benchmark and its archive-selected zero-error gate made one false accepted decision on the recent set. It was not promoted.

## Why this work was needed

The original problem had two related but distinct parts:

1. **Repair provenance and training evidence.** Some historical sidecar display names had been treated as though they were verified identities. That could contaminate voice profiles. The Riley Roberts examples exposed the risk: voice-library samples were attributed to Riley even where the corresponding diarized JSON did not contain Riley as a verified speaker.
2. **Improve identity matching.** The user also has timestamped HiDock exports in `~/Downloads/HiDock Files` whose speaker names were previously tagged by the user. Those exports can be deterministically linked to recording audio and provide substantially more trustworthy model-comparison data.

The work kept these concerns separate. Bare legacy display names are not silently promoted. Deterministically matched, hash-recorded, timestamped human exports form an isolated shadow library and benchmark. Recent `source=user`, `verified=true` sidecars form a separate validation set. The live TitaNet voice library is not replaced by the experimental library.

For the full provenance and legacy-migration history, read `docs/HANDOVER-legacy-voice-sample-plan.md`. The shorter result summary is `docs/VOICE-MODEL-BENCHMARK-2026-07-22.md`.

## Non-negotiable safety boundaries

- A model proposal is not a verified identity.
- No candidate model may silently rename a transcript speaker.
- Existing `source=user`, `verified=true` sidecar names remain authoritative and are skipped by candidate inference.
- Unknown speakers are logged as reviewed but are never enrolled.
- A correction teaches the candidate library only after the saved sidecar already contains the same final name with `verified=true`.
- Correcting an unverified automatic label must not rename or merge the old person's established live profile.
- The experimental library remains separate from `~/HiDock/Voice Library/embeddings.json`.
- TitaNet remains the current diarization/live-library model; WeSpeaker is an identity-review candidate only.
- Candidate inference is paused while HiDock recording is active.
- A one- or two-meeting profile may be shown as a closest candidate for manual review, but cannot pass the robust three-meeting gate.
- The 60-sample value is an active matching-set bound, not a destructive evidence limit. All provenance-backed evidence is retained; quality/diversity selection controls which samples are active.

## Evidence construction

### Historic human archive

`shared/human_archive_evidence.py` links timestamped named exports to local transcripts and audio. The manifest records the export SHA-256, source recording match, speaker name, bounded time range, and evidence status. It does not trust a name merely because it appears in an old diarized sidecar.

The point-in-time inventory at `/private/tmp/hidock-human-archive-inventory-2026-07-21.json` reports:

- 1,501 exports inspected;
- 366 named exports;
- 345 unambiguously matched meetings;
- 5 deliberately held ambiguous matches;
- 16 unmatched items;
- 1,149 eligible person-meeting candidates.

The merged WeSpeaker shadow library contains 1,133 distinct samples across 134 people. The build record notes six candidate clips that could not be decoded reliably at the requested offsets in two MP3 files. The difference between the 1,149 manifest candidates and 1,133 final samples also includes processing/deduplication effects, but a durable itemised merge/build log was not retained. This count reconciliation is a reproducibility weakness worth fixing before the next full bake-off.

The manifest currently lives under `/private/tmp`, and the candidate library's provenance lists 58 temporary shard paths. Those paths existed at handover but are not durable evidence storage. The next run should copy or regenerate the manifest and retain an immutable build ledger beside the candidate artifacts.

### Recent user-confirmed set

The separate recent validation set contains 25 `source=user`, `verified=true` speaker cases from recent sidecars. It was not used to choose the archive gate. Its purpose was to test whether a gate selected on older evidence survived a more recent distribution.

This set is useful but small: only 10 cases passed the selected WeSpeaker gate. It should be expanded continuously from explicit confirmations and corrections, without tuning the gate on every newly added case and then continuing to describe the same data as untouched.

It is also not 25 statistically independent recordings. The set includes `Merged-2026Jul20-093402-Rec69-to-2026Jul20-093437-Rec70_diarized.json` as well as its source meetings. That merged case did not pass the selected 0.21 margin, so it does not inflate the 10 accepted results, but it does affect headline recent top-1. A revised fixed test manifest should exclude derived duplicates or group them as one recording family.

### Explicit name aliases

The user confirmed these mappings during the work:

| Observed label | Canonical identity |
|---|---|
| James | James Whiting |
| Chris Wildmsith | Chris Wildsmith |
| Lucy | Lucy McKay |
| Lucy M | Lucy McKay |
| Gary | Gary Francis |
| Natasha | Natasha Fura |
| Hom | Hom Aboobakar |
| Jackson | Jackson |
| John | John |
| Oster | Oster |
| SDG | SDG |

They live in `docs/legacy-voice-backfill-aliases.json`. No other identities should be merged based solely on embedding similarity. `Andy` versus `Andy Wheeler` remains a likely cleanup item, but must not be merged without human confirmation.

## Benchmark protocol

The implementation is `shared/voice_benchmark.py`.

For each test case:

1. Choose one person's sample from one source meeting as the query.
2. Exclude **all** samples from that source meeting from every identity's gallery, not only from the correct person's gallery.
3. Require the target person to have at least three other gallery meetings.
4. Use at most 20 held-out cases per person so James Whiting's larger history does not dominate. The current implementation takes the first 20 after sorting by source path; it does not sample randomly or stratify by date/domain.
5. Compare the same case construction under three scorers: maximum exemplar, top-three-meeting median, and profile centroid.
6. Report ordinary top-1 accuracy and macro top-1 accuracy across eligible people.
7. Search a threshold/margin grid for the highest-coverage gate with zero observed archive errors.
8. Freeze that archive-selected gate and apply it to the separate recent set.

The clean archive benchmark contains 387 held-out meeting cases across 28 eligible people. It excludes the competing export `2025Feb20-135700-HiD33_diarized.json`.

The zero-error gate search uses similarity thresholds from 0.50 through 1.00 in 0.01 steps and margins from 0.00 through 0.30 in 0.01 steps. It maximises accepted correct cases, then coverage, then prefers the lower threshold and margin among ties. This search is explicit in `_zero_error_gate()` and should be reviewed for optimistic selection bias.

### Scorer definitions

- **Maximum:** best cosine similarity to any active gallery meeting.
- **Top-three-meeting median:** take at most one representative sample per source meeting, rank meeting-level similarities, and use the median of the top three.
- **Centroid:** average active gallery vectors, normalise the centroid, then take cosine similarity to the query.

Top-three median was chosen because it was more robust than a single lucky/noisy exemplar while retaining variation that a centroid can blur. At runtime, a profile with fewer than three supporting meetings falls back to maximum scoring but is marked `max_thin_profile` and cannot be a strong suggestion.

## Results

| Model / scorer | Archive top-1 | Archive macro top-1 | Recent top-1 |
|---|---:|---:|---:|
| TitaNet Small / max | 41.9% | 36.9% | 4.0% |
| CAM++ / max | 81.1% | 76.3% | 56.0% |
| ERes2Net Large / max | 87.1% | 82.3% | 76.0% |
| ERes2Net Large / top-three median | 91.0% | 84.9% | 76.0% |
| ERes2Net Large / centroid | 87.3% | 85.1% | 76.0% |
| WeSpeaker ResNet293-LM / max | 93.02% | 90.85% | 84.0% |
| WeSpeaker ResNet293-LM / top-three median | **96.12%** | **93.57%** | **88.0%** |
| WeSpeaker ResNet293-LM / centroid | 95.35% | 93.09% | **88.0%** |
| Microsoft WavLM Base Plus SV / max | 91.73% | 87.10% | 84.0% |
| Microsoft WavLM Base Plus SV / top-three median | 93.54% | 89.05% | 84.0% |
| Microsoft WavLM Base Plus SV / centroid | 92.76% | 89.52% | 84.0% |

The archive top-three WeSpeaker result is 372 correct top-1 results from 387 cases.

A point-in-time comparison of `(actual, held_out_source)` archive keys and `(actual, sidecar, speaker_id)` recent keys found no differences between the WeSpeaker and WavLM reports. This fairness check should become an automated assertion for every future model rather than an ad hoc shell comparison.

### Why the ordinary 0.70 / 0.04 gate was rejected

The report field named `production_gate` tests the older/default threshold of 0.70 and margin of 0.04. That name is historical benchmark vocabulary; the app does not auto-apply it. On the archive it accepted 357 cases but five were wrong: 98.60% precision is not acceptable for identity assignment. On the recent WeSpeaker set the same gate accepted 19 cases with two errors.

### Selected WeSpeaker gate

For top-three median:

| Dataset | Total cases | Accepted at 0.71 / 0.21 | Correct | Incorrect | Coverage |
|---|---:|---:|---:|---:|---:|
| Historic archive | 387 | 301 | 301 | 0 | 77.78% |
| Separate recent set | 25 | 10 | 10 | 0 | 40.00% |
| Combined observation | 412 | 311 | 311 | 0 | 75.49% |

The absolute similarity threshold rejects weak voice matches. The margin threshold rejects cases where the runner-up is too close. Both conditions must pass.

The app then adds policy holds that were not used to optimise the numeric gate:

- fewer than three supporting meetings;
- more than six detected speakers in the meeting;
- acoustic quality below 0.45;
- insufficient contiguous speech;
- missing/invalid audio, model, library, or compatible profiles.

Any warning means the result is displayed only as a closest candidate or held item, not as a gate-passing suggestion.

### Why centroid was rejected

WeSpeaker centroid appeared better on the archive: its archive-selected 0.69 similarity / 0.11 margin gate accepted 333/387, all correct. Applied unchanged to the recent set, however, it accepted 19 and made one error: Chris Wildsmith was proposed as James Whiting in `2026Jul15-155210-Rec55_diarized.json`, speaker 2. This was the clearest demonstration that archive-only zero-error coverage can be misleading.

### Why WavLM was rejected

WavLM top-three median's best archive zero-error gate was 0.89 similarity / 0.03 margin, accepting only 176/387 archive cases (45.48%). Applied unchanged to the recent set, it accepted four cases and one was wrong: Joe Kraft was proposed as James Whiting in `2026Jul17-093159-Rec63_diarized.json`, speaker 0. WavLM therefore had both lower safe coverage and a false recent acceptance.

## Concrete runtime sanity check

A no-write check was run against `2026Jul21-152405-Rec77_diarized.json`:

- speaker 1, currently an unverified auto-labelled James Whiting, ranked James first at 0.8262, Joe Kraft second at 0.5529, margin 0.2733, with 60 supporting James meetings and acoustic quality 0.975. It passed the strong review gate.
- generic speaker 0 ranked Riley Roberts first at 0.8638 and Jeff Chow second at 0.8522, a margin of only 0.0116. It was correctly treated as ambiguous and shown only as “Closest voice to review”.

This case validates the intended margin behaviour and also shows why high absolute similarity alone is unsafe.

Earlier manual review also established that speaker 1 in `2026Jul13-174615-Rec47_diarized.json` is James Whiting. Speaker 2 in `2026-07-07 14-47-20_diarized.json` is unknown in a noisy multi-person recording and must not be enrolled.

## Audio cleanliness

Audio cleanliness is worth measuring because bad source audio can poison a profile and make an otherwise high embedding similarity unreliable. The current heuristic is implemented through `_audio_quality_from_path()` and incorporates:

- speech density;
- a conservative RMS signal-to-noise estimate;
- clipping risk;
- decodability and bounded clip provenance.

The result is an explainable admission/review signal, not a perceptual Mean Opinion Score. Runtime candidate review adds `low_audio_cleanliness` below 0.45 and prevents a strong result. The library quality process archives weak evidence rather than deleting it.

An independent reviewer should challenge:

- whether 0.45 was empirically calibrated;
- whether the metric correlates with identity errors on this dataset;
- whether speech density penalises natural pauses or short speakers unfairly;
- whether room echo, overlapping speech, music, and far-field speech need separate measurements;
- whether quality should be a hard gate, a confidence feature, or only a reviewer warning;
- whether enrollment quality and query-time quality need different thresholds.

## Current app integration

The implementation is intentionally hybrid:

- TitaNet continues within-meeting diarization and the existing live voice library.
- WeSpeaker re-embeds only unverified speakers from bounded source audio when the transcript review panel opens.
- The isolated candidate library is ranked with one representative per meeting and top-three median.
- A passing result says `WeSpeaker suggests`; a non-passing best match says `Closest voice to review`.
- The proposed name, similarity, margin, runner-up, supporting meetings, and warnings are available to the reviewer.
- Only the reviewer's explicit confirmation writes the transcript name.
- Confirmation/correction teaches both the established live workflow and the isolated candidate library from the saved verified identity.
- Unknown outcomes are audit-only.
- Candidate learning is written atomically and logged in `review-events.jsonl` once events exist.
- Suggestion work is deferred during active recording.

The latest UI deployment also caps the speaker-validation pane at 260 points, gives the transcript at least 220 points and higher layout priority, and scrolls speaker rows internally. This fixes large speaker lists hiding the transcript. The separate bug where closing the transcript sidebar left audio playing was also addressed earlier in this workstream.

## Active runtime artifacts

Active configuration:

`/Users/jameswhiting/HiDock/Voice Library Candidates/active.json`

Candidate directory:

`/Users/jameswhiting/HiDock/Voice Library Candidates/WeSpeaker-ResNet293-LM-2026-07-22/`

Installed runtime model:

`/Users/jameswhiting/HiDock/Speech-to-Text/wespeaker_voxceleb_resnet293_lm.onnx`

The active configuration contains:

```json
{
  "enabled": true,
  "review_only": true,
  "model_key": "wespeaker_resnet293",
  "scorer": "top3_median",
  "threshold": 0.71,
  "min_margin": 0.21,
  "max_active_samples": 60,
  "speaker_count": 134
}
```

WeSpeaker model SHA-256:

`dbb1ccc7754caff552ebc46347a51aaee2669bb24efc740e665d1a1133d20e98`

WavLM checkpoint SHA-256 used in the comparison:

`e906bce2fa42fb497a1d1a9ecf81548adb7e03b12a5644e32d2f42f0d6500fad`

## Research findings

### Terminology

SSL means self-supervised learning. WeSpeaker is a speaker-recognition toolkit, not itself an SSL model. The selected ResNet293-LM is a supervised speaker-embedding model trained on VoxCeleb2. The “LM” suffix refers to large-margin fine-tuning, which WeSpeaker recommends for longer utterances.

Microsoft WavLM Base Plus SV starts from self-supervised WavLM pretraining, then is fine-tuned for speaker verification with an X-vector head and additive margin softmax (AM-Softmax) loss. Being SSL-based did not make it better on this local benchmark.

### WeSpeaker ResNet293-LM

- Official ONNX runtime artifact.
- 28.62 million parameters and 256-dimensional embeddings according to its model card.
- Trained on VoxCeleb2 development data with 5,994 speakers.
- Requires WeSpeaker-compatible Kaldi 80-bin filterbanks and per-utterance mean normalisation; a generic mel frontend is not interchangeable.
- Model card is CC BY 4.0; toolkit code is Apache 2.0.
- Strongest tested local performance, but slower than ERes2Net on this Mac.

### Microsoft WavLM Base Plus SV

- SSL-pretrained WavLM base-plus model, fine-tuned for speaker verification.
- Raw 16 kHz waveform input and 512-dimensional speaker representation in this integration.
- Official model card says WavLM pretraining used 94,000 hours in total and warns that verification thresholds are dataset-dependent.
- Lower local top-1 and safe-gate performance than ResNet293-LM.

### Is ResNet293-LM “the best model”?

It is the best **tested practical model on this user's data**, not a claim that it is universally state of the art. Public VoxCeleb EERs are useful screening evidence, but are not interchangeable with this application's noisy meetings, microphones, names, participant mix, clip extraction, and safety objective. Local zero-false safe-gate coverage is the promotion metric.

## Next candidates

### 1. SimAM-ResNet100 fine-tuned

This is the quickest next comparison because WeSpeaker publishes an official ONNX artifact (`voxblink2_samresnet100_ft.onnx`) and the existing WeSpeaker-compatible runtime should need the least adaptation. It should be run first to validate the model-plug-in path and obtain a cheap accuracy/latency comparison. Recipe reference: 50.2M parameters, Vox1-O EER 0.229 (LM) / 0.207 (+AS-Norm) / 0.202 (+QMF).

Important licence warning (added in the 2026-07-22 review): this checkpoint is VoxBlink2-trained, so under WeSpeaker's model-follows-dataset policy and VoxBlink2's explicit statement it is CC BY-NC-SA 4.0 with no commercial application — the same restriction flagged for ReDimNet2 below. It is a cheap local benchmark only, not a distributable replacement. If commercial-safe alternatives are ever needed, WeSpeaker's VoxCeleb-trained ResNet221-LM or CAM++ (CC BY 4.0) are the fallback class.

### 2. ReDimNet2-B6 VoxBlink2+VoxCeleb2 LM

This is the strongest practical deployment candidate identified in the research:

- approximately 12.3 million parameters and 13.05 GMACs for B6 according to the official repository;
- official `b6-vb2+vox2_v0-lm.pt` release asset;
- repository reports 0.23% VoxCeleb1-O EER and includes out-of-domain results;
- accepts mono 16 kHz raw waveform through the reference pipeline.

Important model identity warning: WeSpeaker's Hugging Face `wespeaker-voxceleb-redimnet2-B6-LM` configuration shows VoxCeleb2-only training, 192-dimensional output, and a 72-mel frontend. It is **not** the intended VoxBlink2+VoxCeleb2 PalabraAI checkpoint. Do not accidentally benchmark one and describe it as the other.

Important licence warning: the ReDimNet2 code repository is MIT, but the VoxBlink2 project says models trained on VoxBlink2/VoxCeleb are CC BY-NC-SA 4.0 and not for commercial application. Code licence does not automatically settle checkpoint/training-data rights. Resolve this before app distribution or production use.

### 3. W2V-BERT 2.0 MFA-LM

This is the strongest high-cost SSL accuracy candidate found. **Updated 2026-07-22 (follow-up research): WeSpeaker now officially supports W2V-BERT 2.0, which lowers the integration effort for offline benchmarking but does not change the deployment verdict.**

- Attribution correction: the backbone is Meta's `facebook/w2v-bert-2.0` (~600 million parameters, trained self-supervised on 4.5 million hours and 143+ languages, MIT licence). No `google/w2v-bert-2.0` checkpoint exists; Google's original 2021 w2v-BERT was never released. WeSpeaker's recipe README cites the Google v1 paper while loading Meta's checkpoint, which is the likely origin of "Google W2V-BERT 2.0" claims.
- Official WeSpeaker support: `wespeaker/models/w2vbert_adapter_mfa.py` plus `wespeaker/frontend/w2vbert.py`, recipe `examples/voxceleb/v2/run_w2v.sh` (PR #439 merged 2025-12-02; VoxCeleb reproduction and pretrained checkpoints via PR #466 merged 2026-07-01). The author's checkpoint also runs through the official wespeaker CLI (`--w2vbert2_mfa`).
- The speaker-verification recipe adds multi-scale feature aggregation (Adapter-MFA with LoRA) and large-margin fine-tuning: 580M-parameter frontend plus 6.2M-parameter backend, 256-dimensional embedding, ASP pooling, ArcFace.
- Reported EERs under the WeSpeaker harness: the VoxCeleb-only reproduction (ModelScope `shangguanqituan/wespeaker-w2v-bert2`, ~2.36 GB `.pt`) reaches 0.250 Vox1-O (LMFT, no AS-Norm/QMF); the author's VoxCeleb2+VoxBlink2 MFA-LM checkpoint reaches 0.138/0.285/0.625 Vox1-O/E/H with AS-Norm + QMF (0.14/0.31/0.73 in the author's repo; the paper reports 0.12 with calibration). All results trace to one research group, which also authored the WeSpeaker integration; no independent replication exists yet.
- Still not deployable in this app: no ONNX artifact exists and there is no export path (`export_onnx.py` handles fbank-input backends only); inference requires PyTorch + transformers + peft, ~2.4 GB fp32 weights, and roughly 4x the per-second FLOPs of ResNet293. The pruned 124+6.2M variant (6.31G MACs/s, 0.18% Vox1-O) would suit CPU deployment but is not distributed.
- The `.pth`/`.pt` artifacts are pickle-based checkpoints and should not be loaded before their provenance/hash is verified and an appropriate trust decision is made. Recorded SHA-256 for the third-party `model_lmft_0.14.pth` artifact: `454398fe4010bb7d1095517f3aff683c90a1eed644f68f83272c38ea81f953f9`.
- Licence: the strongest checkpoint is VoxBlink2-derived and therefore CC BY-NC-SA 4.0 (non-commercial only). The VoxCeleb-only reproduction follows VoxCeleb's CC BY 4.0 posture, though its ModelScope repo is loosely tagged Apache-2.0.

This model should be used as an offline accuracy ceiling, run via the official wespeaker CLI against the frozen benchmark — replacing the earlier plan of hand-wiring the third-party repository. It answers how much headroom exists above ResNet293 on this user's data without touching the app, and should be tested after the smaller deployment candidates unless used purely in that ceiling role.

#### Colleague try-out option (added 2026-07-22)

A colleague wants to try this model, so it is rolled into the plan as a managed option rather than an ad hoc experiment:

1. **Planned Models-UI entry.** `wespeaker_w2vbert2` appears in the Model Manager under Speaker Identity Review as a PLANNED, review-only, experimental entry. It can be inspected but not promoted to the active identity backend — activation stays disabled until the bake-off runtime exists, so trying it can never disturb the live review path.
2. **Resource preflight.** The entry carries a "Check compatibility" action backed by `models.py capability wespeaker_w2vbert2`, which verifies this laptop can actually run it: Apple-silicon architecture, total RAM (16 GB recommended, hard fail below 8 GB), at least 8 GB free disk (2.4 GB checkpoint + PyTorch stack), and importable `torch` / `transformers` / `peft` / `wespeaker` packages. Each check reports pass/warn/fail with a human-readable fix.
3. **Try-out order.** Run SimAM-ResNet100 first (cheap ONNX plug-in path), then use W2V-BERT 2.0 through the wespeaker CLI as the offline ceiling on the frozen benchmark, sharing results with the colleague. If the ceiling shows meaningful headroom, evaluate the unreleased pruned variant or a careful export before any UI promotion is reconsidered.
4. **Licence reminder for sharing.** The strong checkpoint is CC BY-NC-SA 4.0 (VoxBlink2-derived): fine to run locally with a colleague, not fine to ship in anything commercial.

## Required next bake-off

Every candidate must:

1. Re-embed the same bounded audio; embeddings from one model must never be reused by another.
2. Use the exact same canonical identities and case/source exclusions.
3. Use the same 387 archive cases and the same 25 recent cases, preferably locked by a durable manifest of exact `(person, source, time range)` keys rather than merely matching counts.
4. Run maximum, top-three median, and centroid scorers.
5. Select numeric gates on the archive only.
6. Apply each selected gate unchanged to the recent set.
7. Report top-1, macro top-1, accepted count, false accepted identities, abstentions, per-person results, latency, memory, model size, and failure count.
8. Inspect every false acceptance and a stratified sample of abstentions.
9. Include acoustic-quality and supporting-meeting slices.
10. Preserve immutable reports, model hashes, manifest hashes, build logs, environment/package versions, and exact source revision.

The promotion target is not merely higher top-1 accuracy. A replacement must maintain zero false gate-passing suggestions on both fixed sets and exceed ResNet293 top-three median's 311/412 combined safe-gate coverage (75.49%). Even a promoted replacement remains review-only until a much larger independently confirmed safety set justifies reconsidering that policy.

## Statistical and methodological concerns to challenge

1. **Small independent set.** Only 25 recent cases exist, and only 10 pass the selected gate.
2. **Grid-search optimism.** The archive gate is the best zero-error point across many threshold/margin pairs. Confidence intervals should account for model and gate selection. Even under a simple independent-binomial assumption, zero errors in 311 accepted cases gives a one-sided 95% error-rate upper bound of roughly 0.96%, not zero; correlation and selection make that simple bound optimistic.
3. **Closed-set evaluation.** Every query's true identity exists in the gallery. Production includes genuinely unseen people; open-set impostor trials are essential.
4. **Identity imbalance and eligibility.** Only 28 of 134 library people have enough meetings for archive evaluation. Thin profiles are not represented in the headline result.
5. **Correlated data.** Meetings, microphones, recurring participant groups, rooms, and dates may create dependencies even after holding out one meeting. The recent set also contains a derived merged recording alongside its source meetings.
6. **Archive label assumptions.** Deterministic linking proves which labelled export and audio range were used; it does not independently prove the historical human label was correct.
7. **Clip and case selection bias.** One bounded representative clip per person/meeting may prefer long, clean speech and underrepresent short or overlapped turns. For high-volume people, the first 20 lexicographically sorted sources are used rather than a seeded random or stratified selection.
8. **Participant-context leakage.** Excluding the full held-out meeting from every gallery is strong, but recurring co-speaker patterns or recording environments may still correlate with identity.
9. **Unknown-speaker behaviour.** A high margin among known profiles is not proof that the speaker is known. Add held-out identities and impostor/open-set trials.
10. **Score calibration.** Cosine scales differ between models. Per-model gate selection is appropriate, but calibration stability over time and new identities is untested.
11. **Quality policy calibration.** The 0.45 cleanliness and six-speaker policies are safety heuristics, not benchmark-derived thresholds.
12. **Active-sample selection.** The active cap of 60 is non-destructive, but the benchmark should compare cap sizes and quality/diversity selection to verify that useful variation is not excluded.
13. **Mutable candidate library.** Explicit confirmations can teach the active candidate library. Freeze a benchmark snapshot separately so later learning cannot change the claimed reference system.
14. **No confidence interval.** “Zero observed errors” needs an exact binomial upper bound or another clearly stated uncertainty estimate, not a claim of zero risk.
15. **Operational cost.** ResNet293 is slower than ERes2Net. Measure panel latency on realistic long/crowded meetings and verify background work cannot disturb recording.

## Suggested additional evaluation suites

- **Leave-one-identity-out/open-set:** remove the actual identity from the gallery and measure how often the system incorrectly passes a known-person gate.
- **Hard impostors:** evaluate known confusions such as James/Chris, Riley/Jeff, same-room colleagues, and voices with similar pitch/accent.
- **Noise strata:** clean, far-field, overlapping, clipped, music/background speech, and very short clips.
- **Temporal drift:** old gallery versus new query and new gallery versus old query.
- **Device/domain shift:** HiDock models, Plaud/imported files, meeting-room microphones, calls, and in-person recordings.
- **Gallery-size stress:** measure whether adding people or samples increases false matches and whether score normalisation is needed.
- **Profile poisoning simulation:** inject one wrongly labelled sample and measure scorer sensitivity.
- **Quality ablation:** compare no quality rule, warning-only, hard gate, and quality-weighted scoring.
- **Cap ablation:** compare active caps such as 20, 40, 60, 100, and unlimited while retaining the same evidence archive.
- **Reviewer UX:** measure whether showing a weak closest candidate biases humans toward confirming it incorrectly.

## Reproducibility commands

All commands below are read-only with respect to the live voice library. Commands with `--report` write only the named report file.

### Inspect the active candidate

```bash
jq . "$HOME/HiDock/Voice Library Candidates/active.json"
```

### Re-run a no-write suggestion

```bash
transcription-pipeline/.venv/bin/python transcription-pipeline/transcribe.py \
  speaker-suggestions \
  "$HOME/HiDock/Raw Transcripts/2026Jul21-152405-Rec77_diarized.json"
```

### Recompute the archive benchmark from a frozen shadow library

```bash
transcription-pipeline/.venv/bin/python -m shared.voice_benchmark \
  --library "$HOME/HiDock/Voice Library Candidates/WeSpeaker-ResNet293-LM-2026-07-22/voice-library.json" \
  --report /private/tmp/wespeaker-independent-recheck.json \
  --min-gallery-meetings 3 \
  --max-cases-per-speaker 20 \
  --exclude-source "$HOME/HiDock/Raw Transcripts/2025Feb20-135700-HiD33_diarized.json"
```

### Verify the selected recent gate

```bash
jq '[.cases[] | select(.best_score >= 0.71 and .margin >= 0.21)] |
  {accepted: length,
   correct: map(select(.best_name == .actual)) | length,
   incorrect: map(select(.best_name != .actual)) | length}' \
  "$HOME/HiDock/Voice Library Candidates/WeSpeaker-ResNet293-LM-2026-07-22/recent-user-evaluation-top3_median.json"
```

Expected result:

```json
{"accepted":10,"correct":10,"incorrect":0}
```

### Run focused and full tests

```bash
transcription-pipeline/.venv/bin/python -m pytest -q \
  shared/tests/test_voice_benchmark.py \
  shared/tests/test_voice_candidate_review.py \
  shared/tests/test_human_archive_evidence.py

transcription-pipeline/.venv/bin/python -m pytest -q shared/tests
```

At handover, the full shared suite passed: 494 tests in 4.62 seconds. The focused candidate/benchmark set was included in that run.

## Point-in-time artifact hashes

These hashes describe the artifacts inspected for this handover. The candidate library may later change through explicit confirmed review events, so a mismatch is not automatically corruption; check `review-events.jsonl` and provenance first.

| Artifact | SHA-256 |
|---|---|
| Human archive inventory | `2c042d9543c2a9a09a325018067bccdef57c59105375754c8b395af0816753dd` |
| WeSpeaker candidate library | `55ca8071a069b9d4b96aa132f5fc48fa3a4914d72d28c7d9d04871fc53514490` |
| WeSpeaker archive report | `d2fe1faefd587ddd7e9a6323096e1463a8409599769d487ae375306095d61991` |
| WeSpeaker recent top-three report | `b487944571ba27856aac4418be086b1a6b5e39a84208eb2f42bb2f673e94ce99` |
| WavLM archive report | `a9f2e4a3efae22ef7b8a686e808517064ae57aa0c38269c440baf0b78fbafaa0` |
| WavLM recent top-three report | `afd661e35408d3155e7cbe176ffd7ac19ef188bf3cc84454dfbbda79b6dff10d` |

## Code and artifact map

### Research/evidence code

- `shared/human_archive_evidence.py` — deterministic export/audio inventory, shadow-library construction, recent evaluation, sidecar replacement planning.
- `shared/voice_benchmark.py` — leakage-controlled leave-one-meeting-out benchmark and gate search.
- `shared/audio_utils.py` — bounded audio decoding and frontend utilities.
- `shared/voice_library_lite.py` — model sessions, embedding extraction, sample archive, quality/diversity active selection, acoustic measures.
- `shared/models.py` — model registry, runtime download metadata, WeSpeaker SHA validation.

### Review-only integration

- `shared/voice_candidate_review.py` — config activation, unverified-only suggestions, ranking, policy holds, correction learning, atomic candidate-library writes.
- `transcription-pipeline/transcribe.py` — `speaker-suggestions` and `record-speaker-suggestion` CLI commands.
- `hidock-mic-trigger/Sources/AppDelegate.swift` — background CLI wiring and active-recording guard.
- `hidock-mic-trigger/Sources/Views/TranscriptViewerView.swift` — suggestion UI, explicit confirmation/correction/unknown actions, candidate-learning callbacks, capped validation pane.
- `hidock-mic-trigger/Sources/Views/ModelManagerView.swift` — review-only model presentation.

### Raw reports

- WeSpeaker archive: `~/HiDock/Voice Library Candidates/WeSpeaker-ResNet293-LM-2026-07-22/leave-one-meeting-out-clean.json`
- WeSpeaker recent scorer reports: same directory, `recent-user-evaluation-{max,top3_median,centroid}.json`
- WavLM reports: same directory under `comparisons/wavlm-base-plus-sv/`
- ERes2Net reports: `~/HiDock/Voice Library Candidates/ERes2Net-2026-07-22/`
- CAM++ and TitaNet intermediate comparison artifacts remain under `/private/tmp/hidock-human-verified-*-shadow-2026-07-22/` and should not be treated as durable storage.

## Verification and deployment state

- Targeted Python checks passed: 23 tests.
- Full `shared/tests` suite passed: 494 tests in 4.62 seconds.
- Swift validation build succeeded.
- The actual application deployment went through the native approval popup; the rebuild hook was not bypassed for deployment.
- `/Applications/HiDock Mic Trigger.app` passed `codesign --verify --deep --strict --verbose=2`.
- The latest deployed build includes the candidate review integration and the speaker-validation-pane height fix.
- The repository worktree contains many pre-existing and in-progress changes. Do not run broad reset, checkout, clean, or formatting operations.

## Official sources to verify

- [WeSpeaker repository and toolkit documentation](https://github.com/wenet-e2e/wespeaker)
- [WeSpeaker pretrained-model table](https://github.com/wenet-e2e/wespeaker/blob/master/docs/pretrained.md)
- [Official WeSpeaker ResNet293-LM model card](https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet293-LM)
- [Microsoft WavLM Base Plus SV model card](https://huggingface.co/microsoft/wavlm-base-plus-sv)
- [Official SimAM-ResNet100 fine-tuned ONNX download](https://wenet.org.cn/downloads?models=wespeaker&version=voxblink2_samresnet100_ft.onnx)
- [Official ReDimNet2 repository](https://github.com/PalabraAI/redimnet2)
- [WeSpeaker ReDimNet2-B6-LM configuration](https://huggingface.co/Wespeaker/wespeaker-voxceleb-redimnet2-B6-LM/blob/main/config.yaml)
- [VoxBlink2 project and model-licence notice](https://voxblink2.github.io/)
- [Meta W2V-BERT 2.0 base model card](https://huggingface.co/facebook/w2v-bert-2.0)
- [W2V-BERT 2.0 speaker-verification repository](https://github.com/ZXHY-82/w2v-BERT-2.0_SV)
- [W2V-BERT 2.0 LM checkpoint page](https://huggingface.co/zl389/w2v-bert-2.0_SV/blob/main/model_lmft_0.14.pth)
- [WeSpeaker W2V-BERT 2.0 recipe and results](https://github.com/wenet-e2e/wespeaker/blob/master/examples/voxceleb/v2/README.md)
- [WeSpeaker W2V-BERT integration PR #439](https://github.com/wenet-e2e/wespeaker/pull/439)
- [WeSpeaker VoxCeleb reproduction and checkpoints PR #466](https://github.com/wenet-e2e/wespeaker/pull/466)
- [ModelScope wespeaker-w2v-bert2 checkpoints](https://www.modelscope.cn/models/shangguanqituan/wespeaker-w2v-bert2)
- [W2V-BERT 2.0 SV paper (arXiv 2510.04213)](https://arxiv.org/abs/2510.04213)

## Recommended independent-review output

Ask the reviewing LLM to return:

1. a verdict on whether the current review-only deployment is safe enough to keep testing;
2. any correctness or leakage defects, with exact file/function references;
3. recalculated benchmark and gate metrics from the raw JSON;
4. an uncertainty estimate for the zero-observed-error claim;
5. an open-set test design and promotion rule;
6. a ranked next-model plan balancing accuracy, local runtime, integration effort, and licensing;
7. any reason to disable the active candidate immediately;
8. a minimal patch/test plan for the most important issue found.

## Definition of done for the next phase

The next phase is complete only when:

- the benchmark manifest and build ledger are durable and immutable;
- exact cross-model case equality is automatically asserted;
- at least one open-set/held-out-identity suite is included;
- confidence/uncertainty is reported honestly;
- SimAM and ReDimNet2 have been compared, with W2V-BERT used if its extra cost answers a meaningful question;
- model/checkpoint licensing is resolved for the intended use;
- any candidate proposed for promotion has zero false gate-passing results on the fixed archive and recent sets;
- any promoted model exceeds 75.49% combined safe-gate coverage;
- confirmation/correction events enlarge a future validation set without contaminating the frozen test set;
- automatic naming remains disabled unless separately proposed, reviewed, and approved on substantially stronger evidence.
