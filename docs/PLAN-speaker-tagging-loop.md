# Speaker tagging: close the verify → voice-library loop
Planning date: 2026-07-04
Status: PLAN ONLY (no code changes yet)
Sources: hidock-mic-trigger/Sources/AppDelegate.swift (checkSpeakersTagged ~7751,
refreshTranscriptionState ~7634, needsTaggingCount wiring), HiDockViewModel.swift
(needsTaggingCount ~458, mergedFileTagged), Views/TranscriptViewerView.swift
(speaker naming, onEnrollSpeaker, onReclusterWithLabels, hasUserNamedSpeakers),
Views/SyncToolbarSection.swift (~123 "N need tagging"), Views/RecordingsTableView.swift
(Tagged column), shared/diarize_sortformer.py (auto-match via voice_library_lite
identify_speaker, conf ≥0.55, ~356–396), shared/voice_library_lite.py.

## Problem
A transcribed meeting is flagged "needs tagging" unless **every** speaker has a
real name. `checkSpeakersTagged` returns false the moment any speaker is still
`"Speaker N"`:
```
for (_, name) in speakerNames { if name matches /^Speaker \d+$/ { return false } }
```
So a meeting where you've correctly tagged 2 of 3 speakers — and the 3rd is
someone you genuinely don't know — nags forever. There's also no way to *confirm*
that an auto-match / a name is correct, and no signal that a name came from the
voice library at all. The loop that should feed corrections back into the voice
library is open.

## What exists today
- **Auto-matching already happens.** `diarize_sortformer.py` embeds each speaker
  (TitaNet) and calls `voice_library_lite.identify_speaker(emb, threshold=0.55)`;
  a confident match writes the enrolled name into `speaker_names`. But the
  `_diarized.json` only stores `speaker_names: {id → name}` — **no provenance**
  (auto vs user), **no confidence**, **no verified flag**.
- **Tagging is boolean + all-or-nothing.** `speakersTagged` (per row) and
  `needsTaggingCount` (`transcribed && !speakersTagged`) drive the toolbar
  "N need tagging" pill and the table's Tagged column.
- **Naming UI exists.** `TranscriptViewerView` lets you rename speakers, enroll a
  speaker to the voice library (`onEnrollSpeaker(name, audioPath, start, end)`),
  and "Re-cluster from my labels". `hasUserNamedSpeakers` already distinguishes
  user-named from generic.

## Desired behaviour (from the request)
1. A meeting with **≥1 speaker tagged or matched from the voice library** shows a
   **confirmation icon**, not a "needs tagging" nag.
2. A per-meeting **verify UI**: go in, see each voice, confirm/correct it. Correct
   confirmations **feed back into the voice library** to keep improving it.
3. If some speakers are tagged and a remaining speaker is genuinely **unknown**,
   the meeting should **not** keep showing "needs tagging" — you can mark that
   speaker "unknown/guest" and the meeting counts as reviewed.

## Design
Reframe tagging from "all speakers named" → **"all speakers reviewed"**, with
provenance tracked per speaker.

### 1. Per-speaker metadata (data model)
Extend the diarized sidecar so each speaker carries provenance + review state.
Keep `speaker_names` for backward compat; add a parallel `speaker_meta`:
```jsonc
"speaker_names": { "0": "James Whiting", "1": "Speaker 2", "2": "Chris" },
"speaker_meta": {
  "0": { "source": "auto",    "confidence": 0.82, "verified": false },
  "1": { "source": "unknown", "confidence": null, "verified": false },
  "2": { "source": "user",    "confidence": null, "verified": true  }
}
```
- `source`: `auto` (voice-library match), `user` (typed/confirmed), `unknown`
  (acknowledged guest), `generic` (untouched "Speaker N").
- `diarize_sortformer.py` writes `source:"auto"` + `confidence` when
  `identify_speaker` matches (it already has both), else `source:"generic"`.
- The viewer writes `source:"user"`/`unknown` + `verified:true` on confirm.

### 2. Per-meeting tagging state (replaces the boolean)
Compute one of:
- **`.unreviewed`** — nothing confirmed and no auto-match (all generic).
- **`.partial`** — ≥1 speaker named/matched, but ≥1 still `generic`
  (not yet reviewed / acknowledged).
- **`.reviewed`** — every speaker is either named or explicitly `unknown`
  (i.e. the user has accounted for all of them). Auto-matches count toward
  "named" but see the icon nuance below.

`checkSpeakersTagged` → `speakerReviewState(transcriptPath) -> TaggingState`.
`needsTaggingCount` counts **`.unreviewed`** meetings only (optionally `.partial`
too, as a softer secondary count) — so a meeting with one confirmed speaker and
one acknowledged-unknown no longer nags.

### 3. Icons (RecordingsTableView Tagged column + row)
- `.reviewed`, all user/unknown confirmed → **`checkmark.seal.fill`** (green) —
  "verified".
- `.reviewed` but includes **unconfirmed auto-matches** → **`sparkles` / wand
  badge** (amber/blue) — "matched from library, confirm to lock in".
- `.partial` → **`person.crop.circle.badge.checkmark`** (amber) — "some tagged".
- `.unreviewed` → current **`tag`** (grey) — "needs tagging".
Tooltip on each states the exact counts ("2 named · 1 auto-matched · 1 unknown").

### 4. Verify panel in TranscriptViewerView (closes the loop)
A "Speakers" section listing each speaker with:
- current name (editable TextField), colour swatch, % of talk time;
- **provenance badge**: `auto 82%` / `you` / `unknown` / `unnamed`;
- actions: **Confirm ✓** (accept name → `source:user|auto`, `verified:true`),
  **rename**, **Mark unknown/guest** (`source:unknown`, `verified:true`).
- **Confirm enrolls to the voice library** (reuse `onEnrollSpeaker` with the
  speaker's representative audio — the diarizer already gathers up to ~10s per
  speaker). Confirming a *correct auto-match* reinforces the centroid;
  confirming a *rename* enrolls/updates that voice. This is the feedback loop.
- Existing "Re-cluster from my labels" stays (uses confirmed names as anchors).

### 5. Voice-library reinforcement (shared/voice_library_lite.py)
- `enroll`/update accepts an "reinforce existing" path so a confirmed match
  averages the new embedding into the stored centroid (improves future matches)
  rather than only creating new entries.
- Optional: track per-voice sample count / last-updated for a Voice Library UI
  quality indicator.

## Files to touch (when implemented)
- shared/diarize_sortformer.py — write `speaker_meta` (source/confidence).
- shared/voice_library_lite.py — reinforce-on-confirm; sample metadata.
- transcribe.py / transcribe_cpp.py — pass through any new enroll/confirm verb.
- AppDelegate.swift — `speakerReviewState` (replace checkSpeakersTagged),
  needsTaggingCount semantics, confirm→enroll wiring.
- HiDockViewModel.swift — TaggingState, per-row state, counts.
- Views/TranscriptViewerView.swift — the Speakers verify panel.
- Views/RecordingsTableView.swift + SyncToolbarSection.swift — new icons/labels.

## Open questions
- Should `.partial` still contribute to the toolbar nag (softer), or only
  `.unreviewed`? (Lean: nag only `.unreviewed`; show `.partial` as the amber
  icon on the row without a global nag.)
- Auto-match confidence threshold to surface as "confirm me" vs "trust silently"
  (currently 0.55 to match at all).
- Where representative per-speaker audio for enroll-on-confirm comes from when
  the viewer confirms (re-derive from the diarized segments vs cache during
  diarization).
