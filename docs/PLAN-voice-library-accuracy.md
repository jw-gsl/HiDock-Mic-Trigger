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
- `enroll` appends a sample; dedup near-identical (cos > 0.98); cap N (keep most recent ~30).
- `score_speakers` margin uses the same best-of scoring.

### 4. Enrollment depth in the Voice Library UI
Show sample count / #meetings per voice so thin profiles are visible; sort/flag.

## UI: transcript speaker-tools reorg (2026-07-10)
Top bar is cluttered and the "re-*" actions are unclear (Re-cluster needed explaining).
- **Document actions** stay top-right: Undo, Copy All, Show File, Export SRT.
- **New secondary "Speakers" strip** under the title bar, contextual: `Re-match`,
  `Re-cluster` (only when ≥1 named), `Re-diarize (N)`. Each with a plain-language tooltip:
  - Re-diarize: "Start over — detect the speakers again from scratch."
  - Re-cluster: "Use the names you've set as anchors and re-assign the unnamed bits to the closest of them (this meeting only)."
  - Re-match: "Fill in unnamed speakers by matching their voice against your saved Voice Library."
- **Per-transcript Re-match** lives here (new `onRematch(jsonPath)` → `rematch` verb).
- **Move the batch** "Re-match untagged meetings" OUT of the Voice Library window into a
  menu item; add "Build Voice Library from Tagged Meetings" alongside it.

## Decisions
- Do 1+2+3+4.
- Bulk enrol excludes unverified auto-matches (trust only user/verified/legacy-named).
- Best-of-samples matching (max cosine), capped sample count, dedup near-duplicates.
