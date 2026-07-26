# Bake-off: ReDimNet2-B6 vs WeSpeaker ResNet293-LM
Date: 2026-07-25
Status: complete — ReDimNet2 top3_median clears the promotion bar on numbers (licence blocks distribution)

## Protocol compliance (handover "Required next bake-off")

- Same bounded audio re-embedded per model (1,139 eligible candidates; same 6
  decode failures as WeSpeaker → 1,133 samples, 133 people).
- Same canonical identities and exclusions: case sets identical except one
  additional Leslie McNeely case — the frozen WeSpeaker library splits
  `Leslie McNeely` (6) and `Leslie Mcneely` (1) into case-duplicate people;
  the ReDimNet2 build canonicalised them (7). Case-equality check: 387/387
  frozen keys ⊆ 388 ReDimNet2 keys. (The lowercase Leslie duplicate is a
  cleanup item for the live candidate library, same class as Andy.)
- Same 387/388 archive cases (leave-one-meeting-out, ≥3 gallery meetings,
  ≤20 cases/person, HiD33 excluded), same 25-case frozen recent set plus
  8 new confirmations since (33 total; evaluated both).
- Gates selected on the archive only, applied unchanged to the recent set.
- Reports, hashes, and provenance preserved under
  `~/HiDock/Voice Library Candidates/ReDimNet2-B6-2026-07-25/`.

## Results

| Model / scorer | Archive top-1 | Archive macro | Archive zero-error gate | Recent (25) | Recent (33) |
|---|---:|---:|---|---|---|
| WeSpeaker ResNet293 / top3_median | 96.12% | 93.57% | 0.71/0.21 → 301/387, 0 err | 10/25, 0 err | — |
| **ReDimNet2-B6 / top3_median** | **96.65%** | **93.98%** | **0.50/0.23 → 330/388, 0 err** | **14/25, 0 err** | **19/33, 0 err** |
| ReDimNet2-B6 / max | 93.56% | 89.80% | 0.99/0.0 → 0/388 (useless) | — | — |
| ReDimNet2-B6 / centroid | 95.36% | 92.37% | 0.82/0.07 → 327/388, 0 err | 1 false accept | 24/25 correct, 1 false |

Combined zero-error safe-gate coverage (archive + recent-33):
**ReDimNet2 top3_median: 349/421 = 82.9%** vs WeSpeaker 311/412 = 75.49%.
On the frozen 25-case subset: 330+14 = 344/413 = 83.3% vs 75.49%.

**Promotion bar (numbers only): zero false gate-passing on both fixed sets
AND exceed 75.49% combined coverage — ReDimNet2 top3_median passes.**

## The centroid trap replicates

ReDimNet2 centroid's archive gate (0.82/0.07, 327/388 zero-error) false-accepts
on the recent set: **Chris Wildsmith proposed as James Whiting in
`2026Jul15-155210-Rec55_diarized.json` speaker 2** — the same case that
caught WeSpeaker's centroid. Two independent confirmations that (a) centroid
is unsafe here, (b) the recent set genuinely discriminates real gates from
grid-search artefacts, (c) top3_median remains the correct scorer.

## SimAM-ResNet100 status

Not benchmarked: unobtainable. wenet.org.cn now serves only a JS app shell
for the documented download URL; no Hugging Face or ModelScope mirror exists
(Wespeaker hosts samresnet34 but not samresnet100). WeSpeaker's official
pretrained.md points only at wenet.org.cn. Re-attempt if a mirror appears.

## Artefacts and provenance

- Checkpoint: `~/HiDock/Speech-to-Text/b6-vb2+vox2_v0-lm.pt` (official
  PalabraAI/redimnet2 v1.0.0 release asset; sha256
  `e0a7d340a92f798720d1208949aa6a6bd0cddcb0ba7d4cec33596a17a484e6a2`).
  Pickle checkpoint — provenance verified before `torch.load`.
- Model code: `~/HiDock/Speech-to-Text/redimnet2-repo` (MIT) — torch.hub's
  urllib downloader fails SSL on this machine, hence the vendored clone.
- Shadow library: 6 shards → merged `voice-library.json` (133 people, 1,133
  samples, 830 active), provenance `embedding_model=redimnet2_b6`.
- Reports: `leave-one-meeting-out-clean.json`,
  `recent-user-evaluation-{max,top3_median,centroid}.json`,
  plus `SHA256SUMS.txt` in the candidate dir.
- Build ledger for the frozen WeSpeaker library (durability fix):
  `benchmark-freeze-2026-07-22/build-ledger-1149-vs-1133.json` —
  1,149 total − 10 ineligible = 1,139 eligible − 6 decode failures = 1,133.
- Integration: `_ReDimNet2Session` in `shared/voice_library_lite.py`
  (192-dim, raw 16 kHz waveform); `redimnet2_b6` model key in
  `human_archive_evidence` shadow/evaluate CLIs.

## Suspect archive label found

`2025Mar20-113500-HiD02` "James Whiting" 476–506 s sits at cosine ≈ 0.10–0.15
against every other James clip under **both** ReDimNet2 and WeSpeaker (other
James pairs 0.71–0.88). Likely a mislabeled archive clip. Not removed —
needs human confirmation before any library change.

## Licence

ReDimNet2 code is MIT; the B6 `vb2+vox2` checkpoint is VoxBlink2-derived →
**CC BY-NC-SA 4.0: local benchmark/review use only, not for distribution.**
If promoted for personal local use this is fine; any commercial/shipped use
needs the VoxCeleb-trained class instead (ResNet221-LM or CAM++, CC BY 4.0).

## Recommendation

Numbers support promoting ReDimNet2-B6 top3_median (0.50/0.23) as the
review-only candidate, replacing WeSpeaker ResNet293 (0.71/0.21): better
top-1, better macro, +7.4pp combined safe coverage, zero false accepts on
both sets. Activation is a user decision (config switch in
`~/HiDock/Voice Library Candidates/active.json`); candidate learning would
continue into the ReDimNet2 library. Remaining bake-off items from the
handover stay open: open-set/held-out-identity suite, confidence intervals,
W2V-BERT as offline ceiling.

## Outcome (2026-07-25, later same day)

**Promoted.** ReDimNet2-B6 is the active review candidate. The Models UI
Speaker Identity Review stage now lists both models with a radio toggle:
each candidate dir holds a `review-candidate.json`, and `models.py
set-active` repoints `active.json` (backing up first), so WeSpeaker is one
click away if wanted. Live verification on Rec76 produced strong Riley
Roberts (0.83 — the speaker who was mislabelled "Rebecca" by the old
auto-matcher) and strong Ellen Barss, with two cautious review rows.
