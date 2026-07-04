# Voice Library UI empty + speaker "Tagged" logic

Research date: 2026-07-04
Source: HiNotes→HiDock H1 bulk migration work (external data task); code refs in `hidock-mic-trigger/Sources/`, `shared/voice_library_lite.py`.

## Current State

Two app-side items surfaced while bulk-importing ~1180 HiNotes recordings into the
H1 library (device grouping, in-app transcript viewer, real speaker names, and
Voice Library enrollment all otherwise working). Neither is a data problem — the
migration data on disk is correct. Both are pre-existing app bugs/behaviours,
independent of the migration.

## Findings

### 1. Voice Library window shows "No voices enrolled" despite a populated library  (BUG)

`~/HiDock/Voice Library/embeddings.json` is valid and populated (14 speakers, 192-dim
TitaNet embeddings, standard schema). `voice_library_lite.py list` returns all 14
when run with `PYTHONPATH` set to the repo root.

Root cause: `openVoiceLibrary()` (`AppDelegate.swift:3576`) shells out to
`voice_library_lite.py list` (args at ~3600) but — unlike `loadVoiceTrainingData`
(`AppDelegate.swift:3526-3538`) — does **not** set `process.environment` (PYTHONPATH)
or `process.currentDirectoryURL` before `process.run()` (~3604). The script's
module-level `from shared.audio_utils import ...` (`voice_library_lite.py:17`) then
raises `ModuleNotFoundError: No module named 'shared'` (because `sys.path[0]` is the
`shared/` dir, not its parent). Process exits non-zero with empty stdout →
`JSONSerialization` yields nothing → `speakers = []` → `VoiceLibraryView.swift:57`
"No voices enrolled". Reproduced directly (no PYTHONPATH fails; `PYTHONPATH=repo`
succeeds). Not a schema mismatch and not a stale cache (it re-runs the subprocess
each time the window opens; there is no in-app refresh button).

Same defect in the other three callers — `enrollSpeakerInVoiceLibrary` (~3752),
`deleteVoiceLibrarySpeaker` (~3782), `renameVoiceLibrarySpeaker` — none set
PYTHONPATH/cwd, so **in-app enroll/delete/rename silently fail** the same way. Only
the Voice Training path sets it, which is why that one works.

Fix: before `try process.run()` in `openVoiceLibrary`, set cwd + env like
`loadVoiceTrainingData`:
```swift
process.currentDirectoryURL = URL(fileURLWithPath: bundledResourcesRoot ?? repoRoot)
var env = ProcessInfo.processInfo.environment
env["HOME"] = NSHomeDirectory()
env["PYTHONPATH"] = bundledResourcesRoot ?? repoRoot   // parent of shared/
process.environment = env
```
Cleanest: a single helper that builds a correctly-configured `Process` for
`voice_library_lite.py` (script path, venv python, cwd, PYTHONPATH) and route all
four call sites through it. (Latent: if ever run from a real bundle with no venv, the
`/usr/bin/python3` fallback at ~3592 lacks numpy — separate issue.)

### 2. "Tagged" status on named recordings  (BEHAVIOUR, not a bug)

A recording shows "Tagged" once its speaker labels are real names (the check treats a
label as untagged only while it matches `^Speaker \d+$`). Bulk-migrated meetings that
carry real names from HiNotes therefore read "Tagged", which is correct. If a
different UX is wanted (e.g. "Tagged" only when *all* speakers named, or a distinct
"partially named" state), that's a small predicate change (the tagged-column check,
~`AppDelegate.swift:7751-7773`). Optional.

## Completed
- [x] Root-caused the empty Voice Library window (missing PYTHONPATH/cwd in `openVoiceLibrary`).
- [x] Confirmed migration data + enrollment are correct (14 speakers via CLI).
- [x] Identified the same defect in enroll/delete/rename callers.

## Completed (2026-07-04, branch `fix/voice-library-pythonpath`)
- [x] Fixed the empty Voice Library window: added `configureVoiceLibraryProcess(_:)` helper
      (sets cwd + `PYTHONPATH` = `bundledResourcesRoot ?? repoRoot`) and routed all four
      callers through it — `openVoiceLibrary`, `enrollSpeakerInVoiceLibrary`,
      `deleteVoiceLibrarySpeaker`, `renameVoiceLibrarySpeaker`. Build succeeded + deployed;
      `list` now returns the 14 enrolled speakers. (Still to do: commit/PR the branch.)

## Planned — Voice Library UX (requested 2026-07-04, NOT yet built)
Goal: a curated library, not "everyone ever named". Interlinked features:
- [ ] **Opt-in enrolment at naming time**: when naming a speaker in the transcript viewer,
      add an "Add to Voice Library" checkbox (so naming ≠ auto-enrol). Default off/on TBD.
      (Backend already supports enrol on demand via `voice_library_lite.py enroll`.)
- [ ] **Voice Library management**: multi-select + bulk "remove" of speakers no longer wanted.
- [ ] **Per-speaker meeting count** in the Voice Library list (how many meetings each appears in).
- [ ] **Click a speaker's count → filter the recordings list** to that speaker's meetings.
- [ ] **Speaker filter** in the recordings list (see who was in which meetings).
      Note: "who is in a meeting" = the `speaker_names` values of that meeting's
      `_diarized.json`; a speaker↔meeting index can be built by scanning those.
- [ ] **Sorting** in the Voice Library list (by name / sample count / last updated / #meetings).
- [ ] **Audition samples**: view/play a speaker's enrolled samples; if a person has
      multiple samples, open a sub-window listing each with a play button.
      DEPENDENCY: `enroll_speaker` currently keeps only the averaged embedding +
      `sample_count` — it does NOT store per-sample provenance. To play samples,
      enrolment must persist a `samples: [{audio_file, start, end}]` list per speaker
      (schema change in `voice_library_lite.py` + whatever bulk-enrol we run). Decide
      this BEFORE bulk-enrolling the migration set, or provenance is lost.
- [ ] Decide whether to adjust "Tagged" logic (optional; see §2).

## Named-speaker distribution (informs bulk-enrol threshold)
141 distinct named people across the HiNotes transcripts. In ≥2 meetings: 63;
≥3: 42; ≥5: 27. Top: James Whiting 346, Ian Reay 83, Joe Kraft 73, Rob Kirby 73,
Jeff Chow 66, Chris Wildsmith 58, Chris Laidler 46, Sean Denton 38, Tony Harper 32.

## Open decision — bulk enrolment for the ~369 migrated named meetings
Since the user does NOT want every named person auto-enrolled, the migration should not
blindly enrol all named speakers. Options: (a) enrol only recurring speakers (in ≥N meetings);
(b) enrol all then prune via the (planned) multi-select removal; (c) enrol none in bulk and
let the user opt-in going forward. Transcripts keep the real names regardless of choice.
