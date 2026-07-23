# Voice model benchmark — 2026-07-22

## Outcome

WeSpeaker ResNet293-LM is the current review-only candidate. It materially
outperforms TitaNet Small, CAM++, ERes2Net Large, and Microsoft WavLM Base Plus
SV on both the historic human-verified archive and the separate recent
user-confirmed recordings. It is not approved for silent automatic labelling.

The selected candidate is now integrated into the app's existing speaker
verification panel as a review-only identity engine. The live TitaNet library
has not been replaced and no automatic transcript rename is permitted.

## Evidence used

- 1,133 distinct samples from 134 people, derived from human-labelled HiDock
  exports and bounded source audio.
- Six clips were excluded because two MP3 recordings could not be reliably
  decoded at the required offsets.
- The competing-export HiD33 meeting was excluded from the benchmark.
- Benchmark cases hold out the entire source meeting from every gallery.
- A person needs at least three other meetings in the gallery.
- Cases are capped at 20 per person so James Whiting's larger history does not
  dominate the result.

## Results

| Model / scorer | Archive top-1 | Balanced top-1 | Recent top-1 |
|---|---:|---:|---:|
| TitaNet Small / max | 41.9% | 36.9% | 4.0% |
| CAM++ / max | 81.1% | 76.3% | 56.0% |
| ERes2Net Large / max | 87.1% | 82.3% | 76.0% |
| ERes2Net Large / top-three median | 91.0% | 84.9% | 76.0% |
| ERes2Net Large / centroid | 87.3% | 85.1% | 76.0% |
| WeSpeaker ResNet293-LM / max | 93.0% | 90.9% | 84.0% |
| WeSpeaker ResNet293-LM / top-three median | **96.1%** | **93.6%** | **88.0%** |
| WeSpeaker ResNet293-LM / centroid | 95.4% | 93.1% | **88.0%** |
| Microsoft WavLM Base Plus SV / max | 91.7% | 87.1% | 84.0% |
| Microsoft WavLM Base Plus SV / top-three median | 93.5% | 89.1% | 84.0% |
| Microsoft WavLM Base Plus SV / centroid | 92.8% | 89.5% | 84.0% |

The clean archive benchmark contains 387 cases across 28 people. The separate
recent set contains 25 user-confirmed cases.

## Conservative gate

The gate was selected on the historic archive and then checked without tuning
on the separate recent set. For WeSpeaker with the top-three-meeting median
scorer, a minimum similarity of 0.71 and a minimum winner/runner-up margin of
0.21 produced:

- 301 accepted archive decisions, all correct (77.8% archive coverage);
- 10 accepted recent decisions, all correct (40.0% recent coverage);
- 311 accepted decisions from 412 cases overall, with zero observed errors;
- 75.5% observed combined coverage.

The centroid scorer had higher apparent coverage on the archive but made one
false accepted match when that archive-selected gate was applied to the recent
set. It is therefore not the conservative choice. Even 311 zero-error accepted
examples support review suggestions, not irreversible silent naming.

## Recommended system behaviour

1. Keep every existing `source=user`, `verified=true` name authoritative.
2. Use WeSpeaker top-three-median suggestions only for generic or unlabelled
   speakers with at least three supporting meetings.
3. For thinner profiles, show a max-scorer candidate for manual review only;
   never treat a one-clip profile as sufficient for automatic naming.
4. Display the candidate, runner-up, scores, margin, supporting meetings, and
   audio-quality or purity warnings in the disposable review page.
5. Never auto-apply a suggestion while the model remains in candidate mode.
6. Feed confirmations and rejections into the benchmark as held-out cases.
7. Reconsider automatic application only after a substantially larger,
   independently confirmed safety set has zero false accepted matches.

## Model/runtime notes

- Official model: WeSpeaker VoxCeleb ResNet293-LM ONNX, 256-dimensional output.
- Model SHA-256: `dbb1ccc7754caff552ebc46347a51aaee2669bb24efc740e665d1a1133d20e98`.
- The model needs WeSpeaker's Kaldi-compatible 80-bin filter-bank frontend with
  per-utterance mean normalisation; the generic ERes2Net frontend is not
  interchangeable.
- It is materially slower than ERes2Net on this Mac, so it should initially be
  used for background/review processing rather than latency-sensitive live
  naming.
- Microsoft WavLM Base Plus SV was also tested using its official 404.5 MB
  PyTorch checkpoint and raw 16 kHz frontend. It ran faster than WeSpeaker in
  the four-worker archive build, but was less accurate, its conservative gate
  covered fewer cases, and its archive-selected top-three gate produced one
  false accepted match on the recent validation set. It is not the candidate.
- WavLM checkpoint SHA-256:
  `e906bce2fa42fb497a1d1a9ecf81548adb7e03b12a5644e32d2f42f0d6500fad`.
- The archive still exposes a likely identity cleanup item: the separate
  `Andy` profile scored 0/4 while `Andy Wheeler` scored strongly. Do not merge
  them without a human confirmation.

## Reproducibility

The benchmark implementation is `shared/voice_benchmark.py`. The selected
library, model, and raw evaluation reports must remain in an isolated candidate
directory, separate from HiDock's live voice library.

## App integration

- TitaNet remains responsible for within-meeting diarisation and the existing
  live voice library.
- WeSpeaker re-embeds only unverified speakers when the transcript review panel
  opens. It is paused while a HiDock recording is active.
- A gate-passing result is labelled as a suggestion. A below-gate result is
  labelled only as the closest voice to review, with its warnings visible.
- Confirmed sidecar names are skipped and cannot be overwritten.
- Only an explicit reviewer confirmation changes a transcript or teaches the
  isolated WeSpeaker library. Unknown decisions are audit-only.
- Correcting an unverified automatic name does not rename that person's
  existing live voice profile.
- Active configuration lives at
  `~/HiDock/Voice Library Candidates/active.json`; the candidate library stays
  separate from `~/HiDock/Voice Library/embeddings.json`.

## Next comparison gate

WeSpeaker is a toolkit; ResNet293-LM is supervised rather than a
self-supervised-learning model. The next candidates are SimAM-ResNet100 FT,
ReDimNet2-B6 VoxBlink2+VoxCeleb2 LM, and W2V-BERT 2.0 MFA-LM. Since 2026-07-01
WeSpeaker officially supports W2V-BERT 2.0 (Meta's checkpoint) with pretrained
models, so it can be run through the wespeaker CLI as an offline accuracy
ceiling; it has no ONNX export path and remains PyTorch-only, and both it and
the two other VoxBlink2-trained candidates are non-commercial licensed. Each
candidate must use the same 387 leave-one-meeting-out archive cases and 25
untouched recent cases. Promotion requires zero false gate-passing suggestions
while exceeding the current 75.5% combined safe-gate coverage; a higher top-1
score alone is not sufficient.
