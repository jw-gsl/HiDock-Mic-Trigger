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

### 2. Per-meeting tagging state (SIMPLIFIED per 2026-07-04 discussion)
The earlier three-state (unreviewed / partial / reviewed) confused: "amber
partial" vs "grey unreviewed" wasn't a meaningful distinction. Collapse to the
user's model — **tagged = a multi-speaker meeting with ≥1 confirmed speaker**:

- **Single-speaker meetings are never flagged.** Nothing to disambiguate → no
  tag state, never in the nag.
- **`.needsTagging`** — multi-speaker, nothing confirmed and no auto-match.
- **`.autoMatched`** — multi-speaker, the voice library matched ≥1 speaker but
  none confirmed yet ("confirm to lock in").
- **`.tagged`** — multi-speaker, ≥1 speaker confirmed (locked in). Done — even if
  other speakers remain unknown/unnamed. There is NO separate "partial" state:
  once you confirm one, the meeting is tagged.

`checkSpeakersTagged` → `speakerReviewState(transcriptPath) -> TaggingState`.
**The toolbar "N need tagging" pill (SyncToolbarSection ~123) counts only
`.needsTagging`** — so once you've confirmed one speaker, or a match exists, the
meeting stops nagging even if someone in it is unknown.

### 3. Icons (RecordingsTableView Tagged column + row) — three states
- **`.tagged`** → **`checkmark.seal.fill`** (green) — "confirmed".
- **`.autoMatched`** → **`sparkles`** badge (blue/amber) — "matched from the
  voice library, confirm to lock in".
- **`.needsTagging`** → **`tag`** (orange — keep the current orange, not grey) —
  the only state that feeds the nag.
Tooltip states the counts ("2 confirmed · 1 auto-matched · 1 unnamed").

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

### 5b. Re-match existing transcripts after the library grows (NEW — no function exists)
Requested 2026-07-04: after enrolling new voices, sweep existing meetings that
still have generic `Speaker N` and auto-match them. **This does not exist today**
— matching only happens inline during diarization, and `_diarized.json` stores no
per-speaker embeddings. Two complementary pieces:
- **(a) Store per-speaker embeddings at diarization time.** Add
  `speaker_embeddings: {id → [192 floats]}` to `_diarized.json` (the diarizer
  already computes them for matching — just persist them). Then a **cheap**
  `rematch` re-runs `identify_speaker` against the current library with no audio
  access. Only helps meetings diarized after this change.
- **(b) Re-embed fallback for legacy transcripts.** A `rematch <transcript>`
  pipeline command that, when embeddings aren't stored, re-derives per-speaker
  embeddings from the audio (TitaNet, as diarization does), matches, and writes
  any new names into `speaker_names` for still-generic speakers only (never
  overwrites a user-confirmed name).
- **Batch entry point:** "Re-match untagged meetings" (menu/toolbar) that runs
  `rematch` over every `.needsTagging`/`.autoMatched` meeting after an enrol —
  this is the backlog side of closing the loop. Respect a "don't clobber
  confirmed names" rule; surface results as new `.autoMatched` (sparkle) for the
  user to confirm. NB: batch re-embed is CPU-heavy — gate it / queue it so it
  doesn't fight the transcription queue.
- Files: shared/diarize_sortformer.py (persist embeddings), shared/voice_library_lite.py
  (`identify_speaker` already suits), new `rematch` verb in transcribe.py +
  transcribe_cpp.py, AppDelegate.swift (batch trigger + per-meeting action).

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

## Decisions (2026-07-04)
- **No "partial" state.** Three states only: needsTagging / autoMatched / tagged.
  A meeting is tagged once ≥1 speaker is confirmed (multi-speaker only).
- **Nag counts only `.needsTagging`.** (The "toolbar nag" = the "N need tagging"
  pill in the sync toolbar.)
- Single-speaker recordings are never flagged for tagging.

## Related
- **`PLAN-voice-library-ui-and-tagging.md`** — the Voice Library UX (opt-in
  enrol checkbox at naming, multi-select remove, per-speaker meeting counts,
  click-count-to-filter, speaker filter, sorting, audition/play samples) and the
  PYTHONPATH fix. This tagging-loop doc is the deeper design for the state
  machine + verify panel; that doc is the library-side hand-off. The
  **enroll-on-confirm** here is the same enrol path that doc's opt-in checkbox
  drives — keep them consistent (confirming a speaker = enrol, gated by the same
  opt-in preference).

## Open questions
- Auto-match confidence threshold to surface as "confirm me" vs "trust silently"
  (currently 0.55 to match at all).
- Where representative per-speaker audio for enroll-on-confirm comes from when
  the viewer confirms (re-derive from the diarized segments vs cache during
  diarization). NB `PLAN-voice-library-ui-and-tagging.md` flags that enrolment
  must persist per-sample provenance (`samples: [...]`) for the play-samples
  feature — decide that schema BEFORE any bulk enrol so provenance isn't lost.
