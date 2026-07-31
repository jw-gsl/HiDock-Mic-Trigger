# Handover — diarisation quality, calendar safety, and app fixes
Written: 2026-07-29 (late morning), for a fresh session
Branches: five stacked PRs, **#62 → #63 → #64 → #65 → #66**
Deployed: `/Applications/HiDock Mic Trigger.app` rebuilt 2026-07-29 ~10:40

## Read this first — three things that will bite you

**1. Python runs live from the repo working tree.** There is no bundled pipeline in
the app bundle, so the installed app executes `<repo>/transcription-pipeline/transcribe.py`
and `<repo>/shared/*.py` as they are on disk *right now*. Editing Python changes
the running app immediately, and switching git branches changes it too. James hit
a mid-edit failure because of this. Swift only changes on rebuild.

**2. The deploy dialog now really checks.** It used to claim every build "may
interrupt active work" regardless, which reads like a detection and gets ignored —
and a deploy silently killed an in-flight re-diarisation on 28 July. It now pgreps
for transcription/downloads/conversion/voice-training, names what it found, and
defaults to Cancel only when something is genuinely running.

**3. `docs/PLAN-*.md` is gitignored** by repo policy, so the analysis docs below
are local-only on James's machine. This HANDOVER file is tracked.

## Where the work stands

| PR | Scope | State |
|---|---|---|
| [#62](https://github.com/jw-gsl/HiDock-Mic-Trigger/pull/62) | Explicit speaker count can split labels **up**, not only merge down | green, Python only |
| [#63](https://github.com/jw-gsl/HiDock-Mic-Trigger/pull/63) | Split one recording into two independently transcribable meetings | green, **UI never clicked through** |
| [#64](https://github.com/jw-gsl/HiDock-Mic-Trigger/pull/64) | Calendar gating, transcript git history, Meeting column | green, **UI never clicked through** |
| [#65](https://github.com/jw-gsl/HiDock-Mic-Trigger/pull/65) | Diarisation quality + measurement harness + pyannote backend | green, Python only |
| [#66](https://github.com/jw-gsl/HiDock-Mic-Trigger/pull/66) | HF token, calendar no-match, Meeting column controls, app fixes | green, **UI never clicked through** |

**Test checklist: `docs/PLAN-test-checklist-prs-62-to-66-2026-07-28.md`** — 56 checks.
Start with **5.18–5.24** (the bugs James found live today) and **5.17** (start a
transcription, then rebuild — the dialog must say BUSY).

## The single most important finding

**A silently-dead embedding contract had disabled most of the voice logic.**
`_ReDimNet2Session` wraps a Torch model and has no ONNX graph, but every consumer
called `extract_embedding(..., onnx_session=…)`, which probes `session.get_inputs()`.
Every caller treats a failed embedding as "no evidence available", so it failed
**completely silently**. Since `f0893f0` selected ReDimNet2, cross-window linking,
mixed-turn repair and split-to-count had all been no-ops in production.

Cost, same two speakers and audio: **TitaNet 0.971 vs ReDimNet2 0.263**. TitaNet's
space is saturated on this data (every enrolled voice 0.97–0.99 against anyone),
which explains both 2% name recall *and* indiscriminate merging.

Fixing that one function: confusion 24.3% → 19.0%, exact counts 25% → 37.5%.

## Measurement — and a reversal worth understanding

`shared/diarisation_eval.py` scores diarisation against ground truth that already
existed: **346 reviewed sidecars** in `~/HiDock/Raw Transcripts` with ≥2
human-confirmed speakers, all with audio (255 h).

```bash
transcription-pipeline/.venv/bin/python3 -m shared.diarisation_eval --list
transcription-pipeline/.venv/bin/python3 -m shared.diarisation_eval \
    --sample 16 --max-seconds 420 --out mine.json --baseline before.json
# --backend {sortformer,pyannote,lite} forces a backend for one run only
# --no-two-sided-partition / --no-refine-assignments force refinements off
```

**Two refinements were enabled on a 16-meeting / 420 s sample, then reverted after
a 60-meeting full-length run contradicted it:**

| | OFF | ON |
|---|---|---|
| Count exact | 28.3% | 33.3% (sample claimed +31 pts; really +5) |
| Count bias | −0.483 | **+1.150** |
| Name recall | **35.4%** | **7.8%** |
| Confusion | 16.1% | 7.0% |

Counts got worse in 28/60 meetings, recall fell in 39/60, and a 3-speaker meeting
was given **17 speakers**. Both defaults are now **off**; the code is retained and
gated (`_TWO_SIDED_PARTITION_DEFAULT`, `_REFINE_ASSIGNMENTS_DEFAULT`).

**Two methodological lessons, more valuable than the result:**

- **The 420 s window hid the failure it was meant to catch.** A short window caps
  how many speakers can appear, so runaway splitting was invisible. Window length
  is not a neutral cost knob when the metric *is* speaker count.
- **Confusion rate was the wrong headline.** It improved in both arms, which made
  the change look good, but with a one-to-one truth mapping extra clusters can make
  each *mapped* cluster purer while the transcript degrades. Weight **count error
  and name recall** above confusion.

**Never A/B from the live working tree.** The first attempt was discarded because
arm 1 was pinned to its process-start code while arm 2 would have begun six commits
later. Use a detached worktree:
```bash
git worktree add -q --detach /tmp/valtree HEAD
cd /tmp/valtree && <repo>/transcription-pipeline/.venv/bin/python3 -u -m shared.diarisation_eval …
```

## Open thread — Rec82: James says two voices, the pipeline finds one

**This is the live investigation and the best place to resume.**

`2026Jul28-160112-Rec82` diarises to **one** speaker holding 3,590 s of 3,590 s.
James states plainly that Jenny Helland does speak and there are two voices in the
audio, so the diarisation is wrong — not merely conservative.

What is already known:

- Sortformer produced **6 labels**; merging to the calendar's attendee count of 2
  left `Speaker 1=3594.0s, Speaker 3=0.4s, Speaker 4=3.4s`. Effectively everything
  collapsed into one label.
- A zero-duration word ("to" at 2034.4 s) was manufacturing a phantom "Speaker 2".
  Fixed, and Rec82's sidecar was repaired by hand (snapshot taken first).
- The `_calendar.json` correctly lists both attendees, and
  `calendar_candidate_names` reaches the diarizer.
- **Jenny is enrolled** in the voice library.

**That test has now finished, and James is right.** 40 segments ≥4 s sampled across
22–3535 s, embedded with ReDimNet2:

```
pairwise cosine: min=0.042  mean=0.811  max=0.965
two-means:       separation=0.738   group sizes {A: 37, B: 3}
group B:  01:51  "together and to like continue the conver…"
          03:21  "while but then I didn't use it and then…"
          52:26  "the ground? So let's find this. It's lik…"
```

A **separation of 0.738** is enormous — the two-means split is unambiguous, and the
minimum pairwise cosine of 0.042 means some segment pairs are almost orthogonal.
There are certainly two voices. So **the embeddings are fine and the fault is
upstream**: either Sortformer's turns or our merging. `_merge_labels_to_count` is
the prime suspect — it is single-linkage and collapsed 6 labels into one.

Note the second voice is a **small minority** (3 of 40 sampled segments, spread
across the hour: 01:51, 03:21, 52:26). That is the same shape as Rec79's Jeevan —
a minority speaker inside a dominant one — which two-means splitting *within a
label* structurally cannot recover.

**Suggested next step:** those three timestamps are effectively free anchors. Label
one of them as Jenny in the viewer, then run `anchor-sweep` (below) and see how much
it reclaims. In parallel, check whether Sortformer ever emitted a distinct label for
those spans, by logging the raw turns before `_merge_labels_to_count` — if it did,
the merge is destroying a correct answer and that is the bug to fix.

**Precedent to follow:** Rec79 Part 2 was the same shape and the diagnosis was
inverted by measurement. Jeevan Dulai spoke 3.8% of a 56-minute meeting and was the
**most** distinct voice present (≤0.335 to anyone, where the pair that *did*
separate sat at 0.449). The failure was structural, not acoustic: two-means
splitting looks for two *balanced* voices inside one label, so a minority speaker
inside a dominant one never separates.

That produced `shared/anchor_sweep.py` — label a passage by hand, and it reclaims
the rest:
```bash
transcription-pipeline/.venv/bin/python3 transcribe.py anchor-sweep \
  "$HOME/HiDock/Raw Transcripts/2026Jul28-160112-Rec82_diarized.json" \
  --speaker "Jenny Helland"        # add --apply once the plan looks right
```
It prints a plan and changes nothing without `--apply`. Note the known limit: it is
segment-granular, so a segment containing *both* voices cannot be fixed by
reassignment — that needs a split.

## What was fixed today (all in #66 unless noted)

- **Zero-duration word became a speaker.** Repair is on the segment: a zero-length
  segment is reattached to the nearest speaker who actually spoke. Pruning the
  speaker map alone did not work — the orphaned id survived on the segment and
  renumbering recreated the phantom. (#65, Python)
- **Sparse speaker ids with mismatched names** (`{"0": "Speaker 1", "2": "Speaker 4"}`).
  Ids are now dense from 0 after pruning, generic labels restated to match, real
  names untouched. (#65)
- **A refusal parsed as a calendar event.** The Markdown parser accepts any bold
  span followed by a time range, so a model saying "no event overlaps that window"
  and then volunteering the *nearest* event's time produced a suggestion titled
  "No event overlaps that window." Three defences: a `NO_MATCH` contract in the
  prompt, negative-phrase short-circuit (including "nearest event"), and
  `looksLikeEventTitle` rejecting prose.
- **Transcript rollback never worked.** `git init` without `--bare` meant every
  `--git-dir` call failed, so no snapshot was ever taken and History stayed blank.
  Fixed in **#64** (deliberately, so that PR is correct in isolation). Also resolves
  git by *running* it — `/usr/bin/git` is Apple's shim and only works with Command
  Line Tools installed.
- **CLI edits had no rollback.** `shared/transcript_history.py` snapshots from the
  Python side into the same repo the app's History reads. (#65)
- **Auto-tagging promoted** to the already-calibrated ReDimNet2 candidate library:
  measured **2.1% → 62%** correct naming. The `review_only` flag in
  `active.json` was *hardcoded True* in the loader, so it was decorative. Licence
  facts now live in `shared/models.speaker_embed_licence` — ReDimNet2 is
  **CC BY-NC-SA, `distributable: False`**; pyannote/CAM++/WeSpeaker are the
  shippable options.
- **A suggestion appeared then vanished.** `refreshSuggestions` assigned wholesale,
  so a second pass with no opinion erased the first's proposal. Merges now.
- **Invitees were missing from the picker that needed them.** The rename/add picker
  ignored calendar attendees entirely; they now form an "Invited to this meeting"
  tier above the library.
- **Viewer went stale after calendar-triggered rework**, and the main window
  duplicated the sidecar's progress. Both depended on an "is the viewer open?"
  check that was keyed on the `.md` path while the tab is keyed on
  `transcript:<..._diarized.json>` — so both silently no-opped through two rounds
  of "fixed". Now keyed correctly.
- **Declined meetings** show an orange `calendar.badge.minus` + "Ad-hoc call", with
  a refresh arrow to re-check. Historic recordings get an on-demand
  **Check calendar** affordance — the automatic gate only ever fires once, just
  after transcription.
- **`_whisper.json` → `_asr.json`.** Parakeet is the transcriber; the old name
  misdescribed its own contents. Reads accept both, routed through one chokepoint
  (`shared/asr_sidecar.py`) because a missed *read* site would silently degrade
  re-diarisation rather than error.
- **One-speaker is an outcome, not a failure.** `cmd_rediarize` exited non-zero, so
  the app showed raw stderr ("Calendar context: …") as the cause. Returns a
  structured `unchanged / single_speaker` result now.
- **Removed the pooled-rematch demotion.** It discarded a correct name twice
  (Rec79's confirmed name; James Whiting at 83% on Rec82, because his speech was
  split across window labels so pooling moved the centroid below threshold) and
  never once helped. The name is unverified at that point — a suggestion to
  confirm — so keeping a slightly-off match costs one click while discarding a good
  one costs the tagging work the feature exists to save.

## pyannote — implemented, blocked on nothing now

`shared/diarize_pyannote.py`, wired as `backend_key: pyannote`. Tries
`speaker-diarization-community-1` (which claims exactly the improvement this
pipeline needs — speaker assignment and counting) then falls back to 3.1. MIT and
free for commercial use, so unlike ReDimNet2 it could ship.

James has **accepted the gate**, and the Models page now has a
**Hugging Face access** section storing a token in the **Keychain** (never a
`.env`; injected into the subprocess environment). Once a token is saved:

```bash
transcription-pipeline/.venv/bin/python3 -m shared.diarisation_eval \
    --sample 40 --max-seconds 0 --backend pyannote --out pyannote.json \
    --baseline sortformer.json
```

This is the highest-value experiment outstanding: Sortformer caps at **4 speakers**
and predicts a fixed set per 300 s window, which is structurally why a minority
speaker straddling a window boundary never gets a cluster.

## Outstanding work

| # | Item | Why it matters |
|---|---|---|
| — | **Rec82 / Jenny** | **Confirmed bug.** Two voices proven (two-means separation 0.738); merging collapses them |
| — | **Evaluate pyannote community-1** | Addresses the root architecture, licence-clean |
| #14 | Partition search scores labels that cannot reach the output | Correctness of shipped code |
| #10 | Stage 4: surface ambiguity as a confirm/reject decision | The Jeevan/Jenny case is the argument for it |
| #11 | Stage 5: per-model threshold calibration | Constants were tuned in a cosine space the pipeline wasn't using |
| #15 | Model page: enable/disable identity model + licence badge | Registry has the facts; UI doesn't show them |
| #16 | Onboard when git is missing; warn before unprotected edits | Detection exists, no user-visible consequence |
| — | Three-arm A/B (off / two-sided only / both) | Which refinement caused the over-counting is unknown |
| — | `unchanged` path should still clean degenerate segments | Rec82 needed a manual repair |

## Local analysis docs (gitignored, James's machine only)

- `PLAN-diarisation-graph-and-loop-architecture-2026-07-27.md` — graph audit, five
  verified structural gaps, staged design, all measurements, revisit triggers
- `PLAN-test-checklist-prs-62-to-66-2026-07-28.md` — the 56 test checks
- `PLAN-transcript-safety-and-speaker-count-2026-07-27.md` — Rec79 incident

## Verification commands

```bash
transcription-pipeline/.venv/bin/python3 -m pytest shared/tests/ -q        # 702
(cd transcription-pipeline && .venv/bin/python3 -m pytest tests/ -q)       # 69
(cd usb-extractor && ../transcription-pipeline/.venv/bin/python3 -m pytest tests/ -q)  # 157
transcription-pipeline/.venv/bin/python3 -m ruff check --select E,F,W --ignore E501,E402,E741 .
cd hidock-mic-trigger && xcodegen generate && \
  GITHUB_ACTIONS=true xcodebuild -project hidock-mic-trigger.xcodeproj \
  -scheme hidock-mic-trigger -configuration Debug -derivedDataPath /tmp/hidock-check
```

`xcodegen generate` is **mandatory** before building — `Info.plist` and the project
are generated, and a hand-edit to `Info.plist` is silently discarded (that cost a
calendar-permission key once).

`GITHUB_ACTIONS=true` is the compile-only path: it exits the post-build script
before any deployment, so no dialog and the installed app is untouched.
