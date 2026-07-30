# Handover — diarisation quality, calendar safety, and voice-library naming
Last updated: 2026-07-29 (midday), for a fresh session
Branch stack: **#62 → #63 → #64 → #65 → #66 → `feature/rec82-merge-count-budget`** (not yet a PR)
Deployed: `/Applications/HiDock Mic Trigger.app` last rebuilt 2026-07-29 ~10:40 —
**stale, see "Deploy needed" below**

## Read this first — four things that will bite you

**1. Python runs live from the repo working tree.** There is no bundled pipeline in
the app bundle, so the installed app executes `<repo>/transcription-pipeline/transcribe.py`
and `<repo>/shared/*.py` as they are on disk *right now*. Editing Python changes the
running app immediately, and switching git branches changes it too. Swift only changes
on rebuild.

**2. Confirm the Swift change is actually deployed.** `TranscriptViewerView.swift`
changed on this branch (naming-library write gate) and was later refactored further in
the working tree. The installed binary is dated 12:00:48, i.e. built from a tree that
contained the change, so it probably has it — but this is **unverified**: private Swift
methods are not exported, so the binary cannot be grepped for it, and no in-app speaker
confirmation has happened since 10:54 to demonstrate it. To check, confirm a speaker in
the viewer and look for a `proposed_name: null` row in
`Voice Library Candidates/<active>/review-events.jsonl` — the old code could not emit
one. The deploy dialog is approval-gated and genuinely busy-checks; do not deploy while
a transcription is running. Compile-only check: `GITHUB_ACTIONS=true xcodebuild …`
(exits before deployment, dialog never shown).

**3. `docs/PLAN-*.md` is gitignored** by repo policy, so the analysis docs below are
local-only on James's machine. This HANDOVER file is tracked.

**4. Never A/B from the live working tree.** Arm 1 gets pinned to its process-start
code while arm 2 begins later. Use detached worktrees:
```bash
git worktree add -q --detach /tmp/before <commit>
cd /tmp/before && <repo>/transcription-pipeline/.venv/bin/python3 -u -m shared.diarisation_eval …
```
Two are live right now: `/tmp/rec82-before` (9f7f610) and `/tmp/rec82-after` (5f547b3).
Remove with `git worktree remove` when the A/B below is read.

## Where the work stands

| PR | Scope | State |
|---|---|---|
| [#62](https://github.com/jw-gsl/HiDock-Mic-Trigger/pull/62) | Explicit speaker count can split labels **up**, not only merge down | open, green, Python only |
| [#63](https://github.com/jw-gsl/HiDock-Mic-Trigger/pull/63) | Split one recording into two independently transcribable meetings | open, green, **UI never clicked through** |
| [#64](https://github.com/jw-gsl/HiDock-Mic-Trigger/pull/64) | Calendar gating, transcript git history, Meeting column | open, green, **UI never clicked through** |
| [#65](https://github.com/jw-gsl/HiDock-Mic-Trigger/pull/65) | Diarisation quality + measurement harness + pyannote backend | open, green, Python only |
| [#66](https://github.com/jw-gsl/HiDock-Mic-Trigger/pull/66) | HF token, calendar no-match, Meeting column controls, app fixes | open, green, **UI never clicked through** |
| `feature/rec82-merge-count-budget` | **Rec82 merge bug + voice-library naming sync** | **no PR yet**, green |

**Test checklist: `docs/PLAN-test-checklist-prs-62-to-66-2026-07-28.md`** — 49 checks
for open PRs #62–#66, plus 7 Rec82 checks for the unsubmitted follow-on branch.
For the open PR stack, start with **5.17** (start a transcription, then rebuild — the
dialog must say BUSY). Once the follow-on branch is its own PR, start with **6.1–6.7**.

Verification commands:
```bash
transcription-pipeline/.venv/bin/python3 -m pytest shared/tests/ -q               # 719
(cd transcription-pipeline && .venv/bin/python3 -m pytest tests/ -q)              # 69
(cd usb-extractor && ../transcription-pipeline/.venv/bin/python3 -m pytest tests/ -q)  # 157
transcription-pipeline/.venv/bin/python3 -m ruff check --select E,F,W --ignore E501,E402,E741 .
cd hidock-mic-trigger && xcodegen generate && \
  GITHUB_ACTIONS=true xcodebuild test -project hidock-mic-trigger.xcodeproj \
  -scheme hidock-mic-trigger -configuration Debug -derivedDataPath /tmp/hidock-check \
  -destination 'platform=macOS'                                                     # 58 XCTest
```
`xcodegen generate` is **mandatory** before building — the project and `Info.plist` are
generated, and a hand-edit to `Info.plist` is silently discarded. `xcodebuild test`
starts the app test host; `GITHUB_ACTIONS=true` prevents deployment, but only run it
while no real transcription or download is active.

## Rec82 — RESOLVED (commit 5f547b3)

`2026Jul28-160112-Rec82` ("Quick Connect", James + Jenny, 1 hour) came out as **one**
speaker holding all 3590 s. It is now **2 speakers, 296 segments**, and James's real
sidecar has been re-diarised on disk.

**Sortformer was never at fault.** All 14 windows found two speakers cleanly, stitching
produced `spk0=2920.2s / spk1=665.8s`, and the two voices embedded at cosine **0.024** —
orthogonal. Jenny speaks **11 minutes** of the hour.

`_merge_labels_to_count` destroyed it. The loop is `while len(clusters) > count`, but
only labels with an embedding can ever merge, and a label has none when its longest
turn is under a second (`_collect_speaker_audio`). Rec82 had two such scraps (0.4 s and
3.4 s) holding **both** slots of a requested count of 2, so the four embeddable labels
were forced into one cluster — the last merge joining James to Jenny at cosine **0.054**.
General form: with `U` unmergeable labels the real voices are driven to `count - U`, and
`U == count` guarantees one speaker however distinct the people are.

Two fixes: the count is measured against labels that can actually merge, and
unembeddable micro-labels are absorbed into the temporally nearest full-size speaker
(the budget fix alone returned **4** speakers for a requested 2, because a scrap with no
embedding could never clear `_absorb_micro_labels`' similarity threshold either).

**Three claims in the previous handover were wrong — do not act on them:**

- *"two-means separation 0.738 proves a minority speaker"* — that test sampled the
  **diarised output**, whose segments are fixed 30 s chunks containing **both** voices.
  It compared mixtures to mixtures; the 3 outliers were just the chunks Jenny dominated.
- *"Jenny is a minority speaker like Rec79's Jeevan; use `anchor-sweep`"* — she is half
  the conversation by turn count and 18.5% by time. `anchor-sweep` was the wrong tool.
- *"Rec82 shows the root architecture is at fault; evaluate pyannote"* — Sortformer's
  per-window output was correct here, so the 4-speaker cap and window topology were not
  the cause. pyannote is still worth evaluating, but **not on this evidence**.

**Lesson worth keeping:** the pipeline's own stderr log was the fastest route to the
answer (`~/Library/Logs/hidock-menubar.log`, the `Sortformer: merged N labels down to M`
and `talk per label:` lines). Two hours of embedding analysis had pointed the wrong way;
the log gave the arithmetic in one line.

## Voice-library naming — RESOLVED with residue (commit be37383)

Rec82's recovered second speaker stayed `Speaker 2` because **Jenny is not in the
library that names people.** There are two libraries with two independent write paths:

| | live library | naming (candidate) library |
|---|---|---|
| path | `~/HiDock/Voice Library/embeddings.json` | `Voice Library Candidates/ReDimNet2-B6-2026-07-25/voice-library.json` |
| model | TitaNet | ReDimNet2-B6 |
| writer | `voice_library_lite.enroll_*` | `voice_candidate_review.record_suggestion_outcome` |
| fired on | **every** confirmation | only when the model had already *proposed* a name |
| read by naming? | no | **yes** |

That conditional was a **bootstrapping deadlock**: the naming library only learned an
identity it could already suggest, and it can only suggest someone already in it. The
isolation was correct while ReDimNet2 was under evaluation — it is what made the
2.1% → 62% comparison trustworthy — and nobody removed it when the model was promoted
to do the naming. 8 people had drifted out.

Three fixes landed: both confirm paths now record unconditionally (Swift — **needs a
deploy**); `shared/voice_library_sync.py` adds `diff` and `backfill`; and
`shared/health_check.py` warns rather than leaving the gap silent.

```bash
transcription-pipeline/.venv/bin/python3 -m shared.voice_library_sync diff
transcription-pipeline/.venv/bin/python3 -m shared.voice_library_sync backfill          # plan only
transcription-pipeline/.venv/bin/python3 -m shared.voice_library_sync backfill --apply
```

**The backfill cannot trust the live library's labels, and this is the important
finding.** Jenny's only live sample came from a **113 s block the live library had
itself flagged "very long segment may be mixed"** (`2026Jul16-140137-Rec62`, human
verified). The clip inside it was James. The first backfill run enrolled it and taught
the naming library that James is Jenny — **0.857 against his real voice, 0.024 against
hers**. It was reverted from backup. Every clip is now embedded and checked against the
established identities first, at the same threshold naming uses to claim one.

**Rec62 has the same disease Rec82 had** — under-split diarisation producing long mixed
blocks — and the live library is seeded from such blocks. Assume more contamination
exists; the `backfill` collision report is the cheapest way to find it.

State after the run — **3 people genuinely recovered**, 5 still unreachable:

| person | state |
|---|---|
| Hanna Ha, Rebecca Nemaric, Theo Moss | **nameable now** |
| Garry Clarke | enrolled but **inert** — his only clip was archived on quality (0.507), and `_rank_library` drops identities with no active sample, so he is as invisible as before |
| Adam Mohamedally, Johan Nystrom | every clip rejected as contaminated — need a clean sample |
| Emma Thorne | **alias, not contamination.** Scores **1.0** against candidate-library `Emma`, and both are built from the same two transcripts (HiD33, HiD41). Awaiting James's decision on `merge_candidate_speakers("Emma", "Emma Thorne")` |

**Jenny is now fixed, and the loop closed the way it was meant to.** She was named on
the corrected Rec82 in the viewer at 11:54, which enrolled a clean 30 s clip from her
666 s of speech (quality 0.971, active). Rec82's `speaker_names` is now
`{"0": "James Whiting", "1": "Jenny Helland"}`, and her embedding scores **0.902**
against her own entry with the runner-up at 0.500 — a margin of 0.402 against a
requirement of 0.23, so she will auto-name in future meetings. The contaminated Rec62
clip was never used; the guard refused it and the clean path replaced it.

Worth noting *how* that write reached the naming library: the model had proposed
"Heather Kincaid" at 0.500, James corrected it to Jenny, and the correction counted as a
review outcome — so the **pre-existing** gate fired. It only worked because a wrong
proposal happened to clear the 0.5 threshold. Had nothing reached it there would have
been no suggestion, the old gate would have stayed shut, and she would still be stuck.
That is the case the Swift change covers.

Note `candidate_only` holds 20 names the live library lacks, mostly pre-canonicalisation
first-name forms (`Adam`, `Andy`, `Emma`, `John`, `Lucy R`, `Oster`) from the 25 July
bulk build. **Do not infer aliases from shared provenance** — `Adam` and `Andy` share
transcripts with `Emma Thorne` merely by attending the same meetings. Embedding
similarity is the only trustworthy signal, which is why the guard uses it.

## The A/B is done — the fix is measured, broad-based, and regression-free

`diarisation_eval`, **24 meetings, full length**, `--n-speakers-from-truth`, run from two
detached worktrees (9f7f610 pre-fix, 5f547b3 post-fix) on the identical seeded sample.

| metric | before | after | delta |
|---|---|---|---|
| **count exact** | 45.8% | **70.8%** | **+25.0 pts** |
| count MAE | 0.708 | **0.375** | −0.333 |
| count bias | −0.625 | **−0.292** | +0.333 (less under-counting, no overshoot) |
| confusion | 13.26% | **11.08%** | −2.18 pts |
| **name recall** | 50.7% | **61.8%** | **+11.1 pts** |

Per-case, which is how the earlier reversal was caught:

```
COUNT      better  7   worse 0   unchanged 17
RECALL     better  8   worse 0
CONFUSION  better  6   worse 1
```

**Zero count or recall regressions.** The wins are the Rec82 class — meetings whose
speakers had been collapsed:

```
2025Dec04-101300-HiD10   truth 2   1 -> 2   confusion 0.214 -> 0.000   recall 0.00 -> 1.00
2025Apr11-161500-HiD81   truth 2   1 -> 2   confusion 0.071 -> 0.000   recall 0.00 -> 0.50
2025Oct24-122500-HiD95   truth 3   2 -> 3   confusion 0.213 -> 0.011   recall 0.67 -> 0.67
2025Nov11-140000-HiD53   truth 6   4 -> 6   confusion 0.216 -> 0.247   recall 0.50 -> 0.50
```

The single confusion regression is that last one, and it is the correct trade: two
recovered speakers raised confusion slightly on a meeting whose count went from 4 to the
true 6. Count bias moved toward zero without crossing it, so the over-splitting risk did
not materialise — meaning the micro-label absorption does **not** need reverting.

Two notes for whoever repeats this:

- **The default eval does not exercise the merge path at all** — it passes
  `n_speakers=None`, so `_merge_labels_to_count` never runs. `--n-speakers-from-truth`
  is the only arm that measures it.
- Judge on **count error and name recall above confusion.** Confusion rate is a trap:
  with a one-to-one truth mapping, extra clusters make each *mapped* cluster purer while
  the transcript degrades. Here all three moved together, so no trade had to be made.

The worktrees can now be removed:
```bash
git worktree remove /tmp/rec82-before && git worktree remove /tmp/rec82-after
```

## Prior measurement context (still valid)

`shared/diarisation_eval.py` scores against ground truth that already existed: **346
reviewed sidecars** in `~/HiDock/Raw Transcripts` with ≥2 human-confirmed speakers, all
with audio (255 h).

```bash
transcription-pipeline/.venv/bin/python3 -m shared.diarisation_eval --list
transcription-pipeline/.venv/bin/python3 -m shared.diarisation_eval \
    --sample 24 --max-seconds 0 --n-speakers-from-truth --out mine.json --baseline before.json
# --backend {sortformer,pyannote,lite}  --no-two-sided-partition  --no-refine-assignments
```

**Two refinements were enabled on a 16-meeting / 420 s sample, then reverted after a
60-meeting full-length run contradicted it** (`_TWO_SIDED_PARTITION_DEFAULT`,
`_REFINE_ASSIGNMENTS_DEFAULT`, both now off, code retained and gated):

| | OFF | ON |
|---|---|---|
| Count exact | 28.3% | 33.3% (sample claimed +31 pts; really +5) |
| Count bias | −0.483 | **+1.150** |
| Name recall | **35.4%** | **7.8%** |
| Confusion | 16.1% | 7.0% |

Counts got worse in 28/60, recall fell in 39/60, and a 3-speaker meeting was given
**17 speakers**. **The 420 s window hid the failure it was meant to catch** — a short
window caps how many speakers can appear, so runaway splitting was invisible. Window
length is not a neutral cost knob when the metric *is* speaker count.

Earlier, a silently-dead embedding contract had disabled most of the voice logic:
`_ReDimNet2Session` has no ONNX graph but every consumer called
`extract_embedding(..., onnx_session=…)`, and a failed embedding reads as "no evidence",
so cross-window linking, mixed-turn repair and split-to-count were all no-ops in
production since `f0893f0`. Fixing it: confusion 24.3% → 19.0%, exact counts 25% → 37.5%.
TitaNet's space is saturated on this data (every enrolled voice 0.97–0.99 to anyone),
which is why naming moved to ReDimNet2 (**CC BY-NC-SA, `distributable: False`** — see
`shared/models.speaker_embed_licence`; pyannote/CAM++/WeSpeaker are the shippable ones).

## Large meetings (Rec88, 9 people) — 2026-07-30

**The 8-speaker cap was in the app, not the pipeline.**
`TranscriptViewerView.rediarizeSpeakerRange` was `2...max(8, uniqueSpeakerIds.count)`, and
the ceiling only rose above 8 for a transcript that *already* had more than 8 speakers —
unreachable if you cannot request them. Raised to 20. `_split_labels_to_count` has no cap
and already ends with `no voice evidence to reach N speakers; keeping M`.

Also fixed: the speaker-name editor pre-filled the current name and took focus but never
**selected** it, so typing extended the name instead of searching the library
(`selectPrefilledName`). `nameSuggestions` already had a browse mode for the unchanged
name, so selection was the only missing piece.

**Both Swift fixes are in the working tree, NOT committed** — the file also carries an
unrelated in-progress refactor, so committing it would have swept that up too.

Measured on `2026Jul29-135954-Rec88` (`rediarize --n-speakers 9` on a copy): 15 stitched
labels merged to 9 → **8 plausible voices plus one 0.6 s scrap**. Good, for a backend that
predicts only 4 speakers per 300 s window.

**At this size diarisation is not the bottleneck — naming is. Eight voices found, one
named:** the calendar listed 4 attendees of whom 2 were in the library, `Jeff Chow` was
matched at 51% then lost by the merge, and `Chris Wildsmith` matched **two** labels (84%
and 87%) — a dominant speaker over-split while quiet people went undetected.

**Two hard-coded 6-speaker gates disable naming before 9 is reached:**

- `shared/voice_candidate_review.py:378` — appends `crowded_meeting` to `reasons`, and
  `strong = robust and not reasons`, so no suggestion can ever be strong above 6 speakers.
- `shared/speaker_meta.py:406` — `rematch_preflight` holds every candidate for the same
  reason.

Both are backwards with respect to the calendar: the attendee list is the best prior for
who is present and becomes *more* decisive as a meeting grows. The risk `crowded_meeting`
guards against is a wrong name from a large field of candidates, which being a confirmed
invitee collapses.

**Diagnostic gap:** the app logs Python stderr for rediarize only on *failure*. Both Rec82
and Rec88 were diagnosed from those lines (`merged N labels down to M`, `talk per label:`),
so the successful runs had to be reproduced on a copy to see anything. Capturing stderr on
success would have saved an hour each time.

## Outstanding work

| # | Item | Why it matters |
|---|---|---|
| — | **Verify the Swift change is deployed** | Unproven; see trap 2 for the one-step check |
| — | Decide `merge_candidate_speakers("Emma", "Emma Thorne")` | Alias proven at cosine 1.0 |
| — | Audit the live library for more mixed-block contamination | Jenny's was human-verified and still wrong |
| — | Give `Garry Clarke` a usable exemplar | Enrolled but inert; no active sample |
| — | Waive `crowded_meeting` for calendar invitees | Rec88: 8 voices found, 1 named. Cheapest high-value fix for large meetings |
| — | Stop micro-labels consuming the requested-count budget | Rec88's 0.6 s scrap took one of 9 slots, denying a real voice a cluster — the Rec82 defect in a new guise. **Needs an A/B**: it changes behaviour whenever a genuinely quiet participant exists |
| — | Commit the two Swift fixes | Uncommitted, mixed with an in-progress refactor in the same file |
| — | **Evaluate pyannote community-1** | Licence-clean. **Rec88 is the real argument** — the fixed 4-speakers-per-window topology is the ceiling there. Note Rec82 was *not* evidence for this. Needs the HF token (Models page → Hugging Face access, Keychain) |
| #11 | Per-model threshold calibration | **Rec82 is now the concrete argument.** Even with the budget fixed, a wrong external count can force a merge across cosine 0.054. A similarity floor is only safe once thresholds are per-model: the "no fixed threshold is safe" reasoning is right for TitaNet (0.94 between *different* people) and wrong for ReDimNet2 (0.024) |
| #14 | Partition search scores labels that cannot reach the output | Correctness of shipped code |
| #10 | Stage 4: surface ambiguity as a confirm/reject decision | The Jeevan/Jenny case is the argument |
| #15 | Model page: enable/disable identity model + licence badge | Registry has the facts; UI doesn't show them |
| #16 | Onboard when git is missing; warn before unprotected edits | Detection exists, no user-visible consequence |
| — | Three-arm A/B (off / two-sided only / both) | Which refinement caused the over-counting is unknown |
| — | `unchanged` path should still clean degenerate segments | Rec82 needed a manual repair before the fix |

## Local analysis docs (gitignored, James's machine only)

- `PLAN-rec82-merge-budget-bug-2026-07-29.md` — the Rec82 root cause, all measurements,
  the library-drift trace, and what was rejected and why
- `PLAN-diarisation-graph-and-loop-architecture-2026-07-27.md` — graph audit, five
  verified structural gaps, staged design, revisit triggers
- `PLAN-test-checklist-prs-62-to-66-2026-07-28.md` — the 56 test checks
- `PLAN-transcript-safety-and-speaker-count-2026-07-27.md` — Rec79 incident

## Backups taken this session (scratchpad, delete when satisfied)

```
realbackup/2026Jul28-160112-Rec82{_diarized.json,.md,.srt}   # pre-re-diarisation
voice-library.json.bak-before-backfill                        # pre-backfill naming library
```
The pipeline also snapshots transcripts to its own git history before re-diarising, and
`_atomic_write` is used for library writes.
