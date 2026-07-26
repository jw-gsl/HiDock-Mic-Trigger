# Handover: legacy voice-sample backfill and matching accuracy

**Date:** 2026-07-21  
**Repository:** `/Users/jameswhiting/_git/hidock-tools`  
**Purpose:** hand this work to a new, stronger session without requiring it to reconstruct the voice-library decisions from the git diff.

## Executive summary

The historical HiDock transcript archive contains a large amount of useful speaker identity information. Many older diarized sidecars have human-readable names and timestamped speaker turns, but predate the current `speaker_meta` and `speaker_embeddings` fields. Those labels were previously kept out of automatic voice-library enrollment because their provenance was not explicit: a non-generic name might be a real user confirmation, an imported label, a stale correction, or an accidental auto-match.

The right policy is not to discard this information and not to silently trust all of it. Treat it as **legacy naming evidence** and migrate it through a staged, provenance-preserving backfill:

1. inventory and report the historical evidence;
2. normalize names and resolve aliases explicitly;
3. dry-run candidate exemplars and ambiguities;
4. enroll only trustworthy named turns, with provenance attached;
5. validate a small pilot against real transcripts;
6. roll out in batches, keeping the operation repeatable and reversible;
7. use subsequent user corrections as the evaluation set for tightening matching.

The current implementation already provides the core data model and backfill primitive. The next session should focus on operationalizing the audit/pilot flow and measuring quality before any full archive migration.

## User-facing decisions already made

The transcript speaker tools now have these meanings and order:

| Action | Meaning | What it may change |
|---|---|---|
| **Refine** | Automatic diarization pass using the configured backend. Preserve confirmed and legacy-named timestamp anchors, and split oversized/provisional blocks. | Generic or provisional speaker assignments and segment boundaries. |
| **Redetect** | Re-run diarization with an explicit expected speaker count. | Fresh detector clusters, while preserving named anchors where timestamp overlap supports them. |
| **Rematch** | Match only generic/unconfirmed speakers against the current voice library. | Generic/unconfirmed names only; never a confirmed or legacy-named person. |
| **Reassign** | Use named speakers as fixed anchors and conservatively reassign remaining turns. | Unanchored/generic turns only. |

The old “Best” action was not merely a formatting pass. It was the automatic/best diarization path with no explicit speaker count. It has been renamed **Refine** to make that clear.

Existing human names must remain visible and must not be overwritten by a later automatic pass. Legacy named speakers are treated as anchors when the sidecar has no metadata, or when the metadata says `legacy`/`legacy_import`. An unverified `source=auto` match is deliberately not an anchor.

## Why historic names were not automatically enrolled before

The previous safety boundary was reasonable but too conservative for this archive:

- old sidecars often had only `speaker_names`, with no source, confidence, or verification flag;
- a readable name does not by itself prove that the diarizer cluster belongs to that person;
- automatically enrolling every old name could poison the profile with an accidental label and make future matches look more confident than they deserve;
- derived `Merged-*` transcripts can duplicate the same meeting and inflate both samples and apparent coverage;
- a single old speaker cluster may represent a bad diarization split or a name copied from another source.

The user has now confirmed that the historic mapping is strong enough to use. The change in policy is therefore: **legacy labels are eligible for a controlled backfill, not for unconditional silent enrollment.** Every resulting sample must retain where it came from, which sidecar speaker id produced it, and—when audio was used—the exact bounded time range.

## Current data and trust model

A diarized sidecar generally contains:

```json
{
  "segments": [],
  "speaker_names": {"0": "James Whiting"},
  "speaker_meta": {
    "0": {"source": "user", "confidence": 1.0, "verified": true}
  },
  "speaker_embeddings": {"0": [/* normalized vector */]}
}
```

The relevant provenance classes are:

| Source/state | Default treatment |
|---|---|
| `generic` / `Speaker N` | Never enroll; eligible for Rematch only. |
| `auto` | Do not use as a backfill source or anchor, even if a stale record says `verified=true`. User confirmation must be recorded as `source=user`, `verified=true`. |
| `user`, `verified=true` | Strong evidence; eligible for enrollment and anchoring. |
| `legacy` / `legacy_import`, including non-generic names from a sidecar with no metadata | Preserve as display/history only. Do not backfill by default; it requires an explicit, independently audited migration. |
| `human_archive_verified` | Timestamped named export previously verified by the user, unambiguously linked to its recording and hash-recorded. Eligible for anchors and shadow-library embedding. |
| `unknown` | Do not enroll. |

Name normalization should collapse whitespace and case-only duplicates. It must not silently merge meaningful aliases or probable typos. A merge such as `Wildmsith` → `Wildsmith` needs an explicit user decision and should be represented as a library merge, not as an automatic identity inference.

## Voice-library sample model

The active library is in `~/HiDock/Voice Library/embeddings.json`. A profile stores multiple exemplars rather than one running-average embedding:

```json
{
  "speakers": {
    "James Whiting": {
      "samples": [
        {
          "embedding": [],
          "embedding_dim": 192,
          "model": "titanet-small",
          "source": "backfill",
          "label_source": "legacy_import",
          "source_file": "/.../meeting_diarized.json",
          "speaker_id": "0",
          "audio_file": "/.../meeting.mp3",
          "segment_start": 123.4,
          "segment_end": 153.4,
          "quality_score": 0.92,
          "quality_state": "active_candidate",
          "active": true,
          "added_at": "...",
          "id": "..."
        }
      ]
    }
  }
}
```

Important rules already implemented in `shared/voice_library_lite.py`:

- legacy single-embedding entries migrate to a one-item `samples` list on load;
- matching is best-of-exemplars (maximum cosine similarity), not similarity to one average;
- a repeat from the same source meeting refreshes that meeting's exemplar rather than duplicating it;
- every provenance-backed sample is retained in the profile archive; a quality-and-diversity-selected active set of at most 60 exemplars is used for matching;
- similar exemplars from different meetings are retained because room, microphone, and speaking context vary;
- historical meeting coverage is counted from the full trustworthy archive, independently of the 60-sample matching cap;
- stored diarizer embeddings are preferred because they already represent multiple turns and avoid re-decoding audio;
- older sidecars without an embedding can use a bounded representative audio clip when `--audio-fallback` is explicitly enabled.

The current profile guidance is:

- **Thin:** fewer than 5 samples or fewer than 3 meetings;
- **Usable:** at least 5 samples across 3 meetings;
- **Healthy:** roughly 12 samples across 5 meetings;
- 20–40 samples can help for people with variable rooms/mics; matching remains capped at 60.

## Backfill implementation already present

The main operation is:

```bash
transcription-pipeline/.venv/bin/python -m shared.voice_library_lite \
  enroll-from-transcripts \
  --dir "$HOME/HiDock/Raw Transcripts" \
  --name "PERSON NAME"
```

Useful options:

- `--name` can be repeated to target specific people;
- `--audio-fallback` permits bounded audio extraction for legacy sidecars with no stored embedding;
- metadata-free legacy labels are excluded by default; `--include-legacy` is an explicit unsafe compatibility override;
- `--include-merged` is normally **not** wanted; child meetings are the evidence by default;
- `--max-samples` controls the active matching-set cap, defaulting to 60; it never deletes archived evidence.
- `--dry-run` produces the candidate inventory without changing the voice library; use this for Stage 0.
- `--report PATH` additionally saves that JSON report to an explicit path.
- `--alias-file docs/legacy-voice-backfill-aliases.json` applies only the explicit, user-confirmed legacy-label mappings; the report retains both the observed and canonical names.
- `--stored-embeddings-only` is the recommended first-pilot guard: it selects only existing diarizer embeddings before applying the per-person cap.
- `reassess-quality --dry-run --report PATH --audio` scores existing samples, optionally inspects decodable source clips for speech density, signal-to-noise estimate and clipping risk, and reports the proposed active/archive changes without saving them. It combines acoustic cleanliness with structural provenance only where audio was actually inspected; unreadable or unavailable legacy clips retain their structural assessment.

> **Write guard:** the command changes `~/HiDock/Voice Library/embeddings.json` unless `--dry-run` is supplied. Run the dry-run report, inspect it, snapshot the library, and then run a name-scoped pilot.

Before the next rollout, run this separate quality dry run against the current
library. It reads source audio only when `--audio` is included and does not
write the library:

```bash
transcription-pipeline/.venv/bin/python -m shared.voice_library_lite \
  reassess-quality \
  --dry-run \
  --audio \
  --report /safe/location/voice-library-quality-dry-run.json
```

Review the active/archive movements and any `audio_inspection_failures` in that
report. Do not treat the score as a perceptual MOS: it is an explainable
admission gate, not a replacement for listening to disputed samples.

The operation currently:

- scans `*_diarized.json` files;
- skips `Merged-*` by default;
- ignores generic names and unverified auto-matches;
- deduplicates to one representative cluster per named person per sidecar;
- prefers `speaker_embeddings[speaker_id]`;
- optionally extracts a bounded 30-second clip from the longest attributable turn;
- writes `source=backfill`, the original `label_source`, and detailed sidecar/speaker/audio provenance;
- is safe to repeat for an already-seen source because the source meeting exemplar is refreshed rather than duplicated;
- reports files, eligible labels, stored-embedding enrollments, audio enrollments, skips, per-person counts, and historical meeting counts.

The Voice Library UI also exposes historical meeting coverage, sample depth, provenance inspection, sample deletion, and a per-speaker backfill action. The new session should verify those flows against the real archive before broad rollout.

## Recommended migration runbook

### Stage 0 — protect the archive and capture a baseline

Do not modify transcript sidecars or the voice library in this stage. First run:

```bash
transcription-pipeline/.venv/bin/python -m shared.voice_library_lite \
  enroll-from-transcripts \
  --dir "$HOME/HiDock/Raw Transcripts" \
  --dry-run \
  --report /safe/location/legacy-voice-inventory.json
```

Then snapshot or otherwise preserve:

- `~/HiDock/Raw Transcripts/`;
- `~/HiDock/Voice Library/embeddings.json`;
- any existing transcript corrections or manually edited markdown.

Generate an inventory containing, per sidecar:

- recording identity and date;
- whether it is a derived `Merged-*` artifact;
- named speaker ids and their metadata source;
- segment count, total duration, longest turn, and whether words/timed words exist;
- presence/dimension/model of `speaker_embeddings`;
- names that occur under multiple speaker ids;
- names with suspiciously little attributable speech;
- names that differ only by case/whitespace or look like aliases/typos.

The inventory must be a report, not a write-back migration. Keep the report so the new session can compare before/after counts.

### Stage 0 result — 2026-07-21

A dry run completed without modifying the archive or voice library. It read 1,562 sidecars and reported 5,929 speaker records. It explicitly reported and excluded 19 derived merged records; of the remaining records, 50 verified-user records and 1,312 metadata-free legacy-import records were eligible, while 4,548 were excluded by the trust policy. Under the default 60-sample cap, only 19 selected candidates had stored embeddings; 947 selected candidates lacked an embedding and were skipped because audio fallback was not enabled.

The report surfaced canonicalisation work before a pilot, including `Chris Wildmsith` / `Chris Wildsmith`, `Kirian Weidener` / `Kieran Redpath`, and unqualified labels such as `James`, `Gary`, `John`, `Lucy`, and `Jackson`. The pilot and the stored-embedding rollout recorded below were subsequently completed; no transcript sidecar was changed.

### Stage 1 — establish canonical identities

Create an explicit mapping table:

| Observed legacy label | Canonical person | Decision | Evidence/notes |
|---|---|---|---|
| `...` | `...` | keep / alias / merge / exclude | `...` |

The current user-confirmed mappings live in `docs/legacy-voice-backfill-aliases.json`:

| Observed legacy label | Canonical person |
|---|---|
| `James` | James Whiting |
| `Chris Wildmsith` | Chris Wildsmith |
| `Lucy` | Lucy McKay |
| `Lucy M` | Lucy McKay |
| `Gary` | Gary Francis |
| `Natasha` | Natasha Fura |
| `Hom` | Hom Aboobakar |
| `Jackson`, `John`, `Oster`, `SDG` | unchanged |

Rules:

- exact case-insensitive matches can be normalized;
- aliases and typos require explicit confirmation;
- `Unknown`, `Unknown speaker`, and `Speaker N` are excluded;
- do not infer two different people from a shared first name alone;
- do not merge library profiles merely because their embeddings are close.

### Stage 2 — dry-run candidate quality

Before enrolling, produce one candidate record per `(canonical person, source sidecar)` with:

- source file and recording date;
- old speaker id and original label;
- provenance class;
- duration and number of turns;
- stored embedding availability and model/dimension;
- fallback audio range if required;
- whether another name overlaps the same time range;
- whether the same legacy name appears in multiple clusters in that meeting;
- a reason for inclusion or exclusion.

Candidates with conflicting labels, negligible speech, invalid audio, missing timing, or unexplained identity collisions should go to a review queue rather than being enrolled.

### Stage 3 — run a small pilot

Choose a small set of well-known people with many historic meetings. Run the backfill for those names only. Prefer stored embeddings first; enable `--audio-fallback` only after inspecting the missing-embedding count.

For each pilot profile:

- inspect the sample provenance list;
- check that samples span distinct meetings rather than repeated derived artifacts;
- manually audition a representative subset where audio is available;
- run Rematch only on generic speakers in a few unrelated meetings;
- record false positives, false negatives, no-match cases, and ambiguous cases.

Do not judge the pilot only by the number of enrolled samples. The important measurement is whether the newly enrolled profile improves correct identification without stealing turns from another known person.

Provisional pilot gates (tighten them once the first labelled correction set exists):

- zero enrollment of `generic`, `unknown`, or `auto` evidence;
- zero changes to transcript sidecars during backfill;
- at most one retained exemplar per person per source sidecar;
- zero false positives in the manually audited set; any false positive stops rollout;
- record every no-match and ambiguous match, then set a precision/ambiguity target before batch rollout.

### Stored-embedding pilot result — 2026-07-21

The first write pilot used the explicit alias file and `--stored-embeddings-only`, after snapshotting `~/HiDock/Voice Library/embeddings.json` as `embeddings.pre-legacy-pilot-2026-07-21.json`. It processed 13 stored TitaNet embeddings: 10 for James Whiting and 3 for Chris Wildsmith. No audio fallback ran and no transcript sidecar was modified.

James already had the previous destructive 60-sample cap, so that historical pilot retained 60 samples while replacing nine older source meetings with nine new backfill meetings and refreshing one existing-source exemplar. Chris changed from 56 retained samples to 58: two new source meetings plus one refreshed exemplar. The quality-gated archive now replaces that FIFO retention behaviour: future samples are retained as evidence and only a bounded active set is used for matching. Each pilot sample records its original label, source class, sidecar, speaker id, and embedding model.

The next required validation is a manually audited Rematch on a few unrelated meetings. Do not enable audio fallback or expand the batch until that check establishes acceptable precision.

### Quality-gate and stored-embedding rollout — 2026-07-21

Before the archive rollout, the live library was snapshotted as
`~/HiDock/Voice Library/embeddings.pre-quality-and-canonical-merge-2026-07-21.json`.
The explicitly confirmed legacy profiles `James`, `Lucy`, `Lucy M`, `Gary`,
`Natasha`, and `Hom` were merged into their canonical names. No name was
inferred or merged from embedding similarity.

The `quality-v2` reassessment then inspected 820 decodable bounded source clips
and kept structural-only scoring for 115 stored embeddings with no source
audio. It changed the active matching set without deleting evidence: 913 of
935 samples were active and 22 were archived. The acoustic component combines
speech density, a conservative RMS signal-to-noise estimate, and clipping risk;
it is an eligibility signal, not a MOS. Decoder warnings from some legacy MP3s
did not cause a failed inspection and no sample was deleted.

The full trusted **stored-embedding-only** archive run then scanned 1,562
sidecars and processed 27 candidate embeddings across 12 people: Adam
Mohamedally (1), James Whiting (10), Chris Wildsmith (3), Rebecca Nemaric (2),
Dante Bradley (1), Ian Reay (1), Ellen Barss (2), Jeff Chow (2), Jenny Helland
(1), Joe Kraft (1), Chris Laidler (1), and Garry Clarke (2). It used no audio
fallback and skipped none. Source refresh and deduplication mean that this did
not create 27 net new samples: the resulting library contains 120 speakers,
939 retained samples, 917 active matching samples, and 373 historical meetings.
All retained samples remain auditable; only the active set participates in
matching.

### Human-verified export recovery pilot — 2026-07-21

The timestamped exports in `~/Downloads/HiDock Files` are user-confirmed
historical speaker mappings and are now handled separately from bare legacy
sidecar labels. The new `shared/human_archive_evidence.py` inventory records
the source-export SHA-256, unambiguous timestamp/recording match, named audio
range, and later acoustic score for every candidate. It does not modify the
live library or sidecars.

The first inventory found 366 named exports, 345 unambiguous matched meetings,
five deliberately held ambiguous matches, and 1,149 eligible
person-meeting clips (one bounded representative clip per person per meeting).
Explicit aliases are applied before profile creation. A 100-clip / 31-person
shadow-library pilot was built in isolated shards and evaluated only against
held-out `source=user`, `verified=true` sidecar speakers. It produced one false
automatic label (a confirmed Kieran Redpath evaluation segment proposed as
Ruby), so the rollout gate failed. No live library or transcript was changed.

The next action is to improve/expand the shadow evidence and re-run the
held-out evaluation until it has zero false automatic labels at a useful
coverage level; do not switch the live matcher merely by raising thresholds to
force abstention.

The remaining, deliberately separate rollout decision is whether to run
audio-fallback backfill for the trustworthy legacy labels that lack stored
diarizer embeddings. That should be a small, auditable pilot with manual audio
review—not a broad automatic run.

### Rematch dry-run audit — 2026-07-21

`transcribe.py rematch` now supports `--dry-run --report PATH`. It applies the
same conservative matching logic in memory, shows the proposed speaker labels,
and does not write either the diarized sidecar or its rendered markdown.

Two unrelated stored-embedding-only audit cases were run:

| Sidecar | Proposed change | Confidence | Outcome |
|---|---|---:|---|
| `2026Jul13-174615-Rec47_diarized.json` | `Speaker 1` → James Whiting | 97.99% | Dry-run only; sidecar SHA-256 unchanged. |
| `2026-07-07 14-47-20_diarized.json` | `Speaker 2` → Adam Mohamedally | 97.04% | Dry-run only; awaiting a human audio review. |

Each meeting retained its remaining generic speakers. These are proposed
unverified auto labels, not confirmed training evidence. A reviewer should
audition the relevant turns and either apply the one-file rematch, leave it
generic, or explicitly correct it. Do not batch-rematch the other candidates
until these two decisions have been reviewed.

### Stage 4 — validate with the correction loop

Every user correction should be treated as valuable labelled data:

- retain immutable meeting/sidecar, source speaker id, original name/source/confidence, final name, action, and sample provenance;
- for an automatic `A → B` correction, add the corrected exemplar to B but preserve A;
- only an explicit library merge may absorb A into B;
- keep transcript cluster lineage when clusters are merged;
- use corrections as the evaluation set for threshold and margin tuning.

The matcher now requires both an absolute similarity threshold and a lead over the runner-up. This is intentionally conservative: an ambiguous voice should remain generic for review rather than become a confident-looking false positive.

### Stage 5 — expand in batches

After the pilot passes, backfill the remaining canonical names in batches. Keep each batch report and the source library snapshot. A batch should be stoppable if:

- a profile receives unexpected names;
- one meeting contributes more than one retained exemplar;
- a high-volume person has suspiciously low or high coverage;
- the no-match/ambiguous rate moves materially;
- unverified auto labels are being admitted;
- derived meetings are inflating counts.

Only after the batch reports look correct should historical names be used to seed wider Rematch coverage.

## Important distinction: backfill versus transcript processing

Normal transcript processing must not silently convert every legacy name into a voice sample. The safe separation is:

- **label preservation:** keep trustworthy existing human names attached to their timestamp intervals when Refine or Redetect runs;
- **voice-library backfill:** an explicit, reportable operation that adds selected historical exemplars with provenance;
- **Rematch:** after the library grows, apply it only to generic/unconfirmed speakers;
- **user confirmation:** promote an auto match to verified training data only after review.

This prevents a bad historical label from being both preserved and silently amplified across the entire archive.

## Rec 55 / oversized-block validation case

The local July 15 Rec 55 sidecar contains a no-word segment around 19:18:

- source: `2026Jul15-155210-Rec55_diarized.json`;
- approximately `1158.72`–`1516.48` seconds;
- about `357.76` seconds in one block;
- named speakers in the current sidecar are James Whiting, Jeff Chow, and Chris Wildsmith, with verified user provenance.

The splitter now handles long no-punctuation/no-word segments by creating continuous synthetic word chunks and enforcing a maximum duration cap. A read-only simulation on the July 15 Whisper source produced continuous chunks with a maximum of about 29.4 seconds under the 30-second default cap.

When validating Refine on Rec 55, confirm all of the following:

- named timestamp anchors remain named;
- the large 19:18 block becomes readable sub-blocks;
- no text is lost or duplicated;
- segment timing remains continuous;
- separately, the transcript viewer does not revert to the first meeting when opening a sidecar (a UI navigation regression, not a splitter acceptance condition).

## Files to read first

- `docs/PLAN-voice-library-accuracy.md` — broader accuracy plan and correction-learning contract.
- `shared/voice_library_lite.py` — profile schema, matching, historical backfill, provenance, and CLI.
- `shared/human_archive_evidence.py` — verified-export inventory, isolated shadow build, and held-out evaluation.
- `shared/speaker_meta.py` — generic-only Rematch and metadata rules.
- `shared/merge_speaker_labels.py` — timestamp-based preservation of existing and legacy names.
- `shared/recluster_with_anchors.py` — named-anchor Reassign behaviour and merge cap.
- `shared/diarize_lite.py` — oversized segment splitting used by the diarization paths.
- `transcription-pipeline/transcribe.py` — `rediarize`, `rematch`, `recluster-with-anchors`, and voice-library command wiring.
- `hidock-mic-trigger/Sources/Views/TranscriptViewerView.swift` — Refine/Redetect/Rematch/Reassign UI and tooltips.
- `hidock-mic-trigger/Sources/Views/VoiceLibraryView.swift` — profile depth, coverage, provenance, and backfill UI.

## Verification already completed

The current implementation was checked with:

```bash
transcription-pipeline/.venv/bin/python -m pytest -q shared/tests
# 462 passed

git diff --check
# clean
```

The macOS app build and test target also completed successfully using Xcode with code signing disabled and the deployment hook skipped via `GITHUB_ACTIONS=true`. The worktree was already substantially dirty before this handover; the next session must preserve unrelated existing changes and should not use a broad reset or cleanup.

## Next-session checklist

1. Read this document and `docs/PLAN-voice-library-accuracy.md`.
2. Inspect the current worktree before editing; separate pre-existing changes from new work.
3. Build the Stage 0 archive inventory without modifying sidecars or the library.
4. Add or verify a dry-run/report mode for the backfill if the inventory cannot be produced without writes.
5. Resolve canonical names and aliases explicitly.
6. Run a named-person pilot using stored embeddings first.
7. Review provenance and manually sample the pilot audio.
8. Measure Rematch precision/recall-style outcomes on generic speakers and record ambiguous matches.
9. Only then consider a full historical backfill.
10. Keep the correction events as the durable evaluation set for future threshold, margin, and embedding-policy changes.

## Open questions for the next session

- Should the dry-run report be a JSON artifact, a markdown report, or both?
- Which legacy names are already confirmed strongly enough to form the pilot cohort?
- Should the fallback audio policy evolve from one longest 30-second clip per meeting to several shorter, diverse clips when a sidecar has no stored embedding?
- What minimum evidence should move a legacy label from “eligible for backfill” to “trusted anchor” if the archive contains conflicting labels for the same time range?
- Should corrections be stored in a dedicated JSONL/event file before broader evaluation tooling is added?
- What precision/ambiguity target is acceptable for automatic Rematch, and how should the UI surface “no confident match” versus “not enough evidence”?

## Definition of done for the legacy migration

The migration is complete only when:

- every enrolled exemplar has source sidecar, speaker id, source class, and audio range where applicable;
- generic and unverified auto labels have not been enrolled;
- derived merged meetings are not double-counted by default;
- canonical names and aliases are documented;
- the operation is repeatable without duplicate samples;
- the library can show both retained sample count and full historical meeting coverage;
- a pilot and full-run report exist;
- corrections remain auditable and can be used to evaluate future matching changes;
- no existing human transcript labels were silently overwritten.
