# Voice library accuracy + transcript speaker-tools UI
Planning date: 2026-07-10
Status: IN PROGRESS
Sources: shared/voice_library_lite.py (enroll/identify/centroid), shared/speaker_meta.py
(score_speakers, speaker_embeddings), diarize_sortformer/diarize_lite (identify_speaker
threshold 0.65), Views/TranscriptViewerView.swift (top-bar buttons, onEnrollSpeaker,
onReclusterWithLabels, onRediarize), AppDelegate (enrollSpeakerInVoiceLibrary, rematch verb).

## Problem
Matching is weak because each enrolled voice is built from too few, too-short samples:
- **Confirm enrolls ONE short segment** — `segments.first(where: speakerId)` — often a
  2-word clip. Terrible voiceprint.
- **We ignore the good embedding we already have** — every sidecar stores
  `speaker_embeddings[id]`, a centroid over ~10s of that speaker's longest turns.
- **The tagged backlog is unused** — many imported files already have named speakers +
  stored embeddings; a ready-made training set.
- **Single averaged centroid** loses intra-speaker variation (same person, different
  mic/meeting) → misses / false matches.

## Plan (all four, per 2026-07-10 decision)
### 1. Enroll the meeting centroid, not one segment
On confirm/rename, enroll the sidecar's `speaker_embeddings[id]` directly (robust,
multi-segment, no audio re-decode — also works for Opus/Plaud the app can't decode).
New: `enroll_from_diarized(name, diarized_path, speaker_id)` + CLI verb; Swift confirm
path passes the speaker id + sidecar path instead of audio start/end.

### 2. Bulk "Build library from my tagged meetings"
Sweep every `*_diarized.json`; for each TRUSTWORTHY named speaker enrol its stored
embedding. Trustworthy = non-generic name AND not (source=="auto" && !verified) — i.e.
legacy user-tagged (no meta) + user/verified included; new *unverified auto-matches*
excluded so we don't poison the library. CLI `enroll-from-transcripts --dir`; triggered
from a menu item.

### 3. Multi-exemplar matching (the big accuracy lever)
Store MANY embeddings per voice and match best-of instead of one running average:
- Schema: `speakers[name] = {samples: [{embedding, dim, model, source, added_at}], ...}`.
  Migrate legacy `embedding` → `samples:[{...}]` on load (backward compatible).
- `identify_speaker(emb)` = max cosine over that speaker's same-dim samples (best exemplar).
- `enroll` archives provenance-backed samples; dedup near-identical (cos > 0.98); quality-and-diversity selection bounds the active matching set to ~60 without deleting archived evidence.
- `score_speakers` margin uses the same best-of scoring.

### 4. Enrollment depth in the Voice Library UI
Show sample count / #meetings per voice so thin profiles are visible; sort/flag.

### Current sample-depth policy (2026-07-17)
Quantity helps only when the samples are trustworthy and varied. The working
targets are:

- **Thin:** fewer than 5 samples or fewer than 3 distinct meetings.
- **Usable:** at least 5 samples across 3 meetings.
- **Healthy:** about 12 samples across 5 meetings; 20–40 is useful for especially
variable rooms/mics. The active matching profile is capped at 60 exemplars per
person, while all admitted samples remain archived and the UI's meeting count
is calculated from the full trustworthy transcript archive.

Confirmed/backfilled diarized embeddings now carry the sidecar, speaker id,
audio reference, and representative segment as provenance. A repeated
confirmation from the same meeting replaces that meeting's exemplar; similar
samples from different meetings are retained. This gives us more diversity
without letting repeated clicks or one noisy meeting inflate a profile.

Historical backfill uses the same trust boundary: verified/user-confirmed labels
and legacy named labels are eligible, unverified auto-matches are not. If an old
sidecar has no stored speaker embedding, backfill extracts a bounded representative
audio clip and records its source sidecar, speaker id, and time range. Derived
`Merged-*` transcripts are excluded by default so a merged meeting does not
double-count its child meetings.

The Voice Library exposes this as a per-speaker historical backfill action. It
retains up to 60 diverse exemplars for matching, while retaining the full
trustworthy meeting count as coverage metadata; backfill is therefore safe to
repeat after more meetings are confirmed.

## Correction-learning contract (refined 2026-07-20)

Speaker confirmation is positive training data, but a correction is more
valuable than a plain confirmation and must be represented separately.

- Record every meaningful correction as an immutable event: meeting/sidecar,
  source speaker id, name originally assigned, assignment source and confidence,
  final confirmed name, action (`correct`, `merge`, `unknown`), and the sample
  provenance used to reinforce the final identity.
- Treat `auto name A → user-confirmed name B` as a detector error by default:
  add the corrected exemplar to B, preserve A's profile, and do not silently
  rename or absorb A. Only an explicit Voice Library merge may combine profiles.
- A transcript-level cluster merge must retain the source cluster ids and the
  surviving identity, so later evaluation can distinguish “one person split
  into two clusters” from “two people confused with one another”.
- Corrections become the voice-identification evaluation set: future matching
  should be measured on corrected assignments, with false-positive and
  false-negative counts visible alongside sample depth.
- Rematching may use accumulated correction history to tighten candidate
  ranking/thresholds, but must never rewrite a user-verified assignment without
  an explicit action.

This is reference-profile learning, not model-weight training: confirmed
embeddings expand and curate the exemplar set; correction events provide the
auditable ground truth needed to improve matching policy safely.

The Voice Library list now exposes the profile state and distinct meeting
coverage. The backend exposes sample metadata plus individual sample deletion;
the UI supports audition, source reveal, and cleanup, with multi-select removal
for profiles that should no longer be used for matching.

### Quality gate v2 (2026-07-21)

The active matching set is quality-gated rather than filled simply until it
reaches its 60-exemplar operating cap. Every admitted exemplar remains in the
profile archive, but matching uses only the selected active set. The score is:

- structural evidence: label provenance, representative-segment duration and
  attributable talk coverage; and
- acoustic cleanliness when the source clip can be inspected: speech density,
  an RMS-based signal-to-noise estimate and clipping risk.

Acoustic inspection is explicitly opt-in during reassessment because historical
audio may be missing or undecodable. A failed inspection is reported and leaves
the structural score intact; it must never erase trusted historical evidence.
Use `reassess-quality --dry-run --audio --report PATH` to review the proposed
active/archive movements before applying it to the live library. The score is
an explainable eligibility signal, not a MOS or a substitute for human review
of disputed samples.

### Rematch review gate (in progress)

Do not treat a high cosine score as an automatic transcript identity. The
stored-embedding-only `rematch-preflight` command produces a no-write review
queue with the best profile, runner-up, margin, talk time, turn count, and
meeting speaker count. A candidate is held rather than surfaced for review when
it is below the stricter similarity threshold, ambiguous against the runner-up,
has under 8 seconds of attributable speech or fewer than three turns, or comes
from a meeting with more than six speakers. This directly addresses the noisy
July 7 false positive: high similarity is insufficient when the diarized source
cluster is not reliable.

Every reviewed candidate also has an immutable JSONL correction event path:
`record-rematch-correction`. A confirmed proposal, rejected proposal, and
explicitly unknown speaker are distinct actions. Rejections are evaluation
evidence for the gate; they must never remove or negatively train the proposed
person's voice profile.

`rematch-preflight-batch --dir PATH --report PATH` builds this same no-write
queue for every non-merged diarized sidecar. The report is deliberately separate
from the action that changes a transcript, so it can be audited, sampled, and
threshold-tuned before any UI rollout.

## UI: transcript speaker-tools reorg (2026-07-10)
Top bar is cluttered and the speaker actions are unclear (especially the automatic/best pass and Re-cluster).
- **Document actions** stay top-right: Undo, Copy All, Show File, Export SRT.
- **New secondary "Speakers" strip** under the title bar, contextual: `Refine`,
  `Redetect`, `Rematch`, `Reassign` (only when trusted named anchors exist).
  Each has a plain-language tooltip:
  - Refine: use the configured automatic diarizer, preserve confirmed/legacy
    names, and re-split oversized or provisional parts.
  - Redetect: rerun diarization with an explicit expected speaker count.
  - Rematch: fill only generic/unconfirmed speakers from the Voice Library.
  - Reassign: use confirmed/legacy names as fixed anchors for this meeting;
    conservatively reassign the remaining turns.
- **Per-transcript Re-match** lives here (new `onRematch(jsonPath)` → `rematch` verb).
- **Move the batch** "Re-match untagged meetings" OUT of the Voice Library window into a
  menu item; add "Build Voice Library from Tagged Meetings" alongside it.

### Follow-up: in-view re-diarisation feedback

- [x] Keep re-diarisation progress and results in the open transcript viewer,
      in the gap between the **Speakers** heading and its action buttons; do not
      close and reopen the transcript just to show the result.
- [x] While running, show a clear state such as **Re-diarising…** in that
      location. On completion, show **Re-diarisation complete** and a compact
      change summary: speaker count before → after, number of segment speaker
      assignments changed, and **no changes** when the result is identical.
- [x] Preserve the result in the viewer so the user can immediately inspect
      the changed speaker pills, segments, and karaoke boundaries. Errors should
      also be reported in this same status area, with the existing main-window
      sync status treated as secondary progress telemetry.
- [x] Add UI/model tests for running, success-with-changes,
      success-without-changes, and failure states.

## Decisions
- Do 1+2+3+4.
- Bulk enrol excludes unverified auto-matches (trust only user/verified/legacy-named).
- Best-of-samples matching (max cosine), capped sample count, dedup near-duplicates.
