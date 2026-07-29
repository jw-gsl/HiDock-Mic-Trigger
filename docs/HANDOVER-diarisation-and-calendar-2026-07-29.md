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

**2. Deploy needed for the Swift change.** `TranscriptViewerView.swift` changed on this
branch (naming-library write gate). It compiles clean but the **installed app does not
have it** — speaker confirmations will keep failing to teach the naming library until
someone rebuilds and deploys. The deploy dialog is approval-gated and genuinely
busy-checks; do not deploy while a transcription is running. Compile-only check:
`GITHUB_ACTIONS=true xcodebuild …` (exits before deployment, dialog never shown).

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

**Test checklist: `docs/PLAN-test-checklist-prs-62-to-66-2026-07-28.md`** — 56 checks.
Start with **5.18–5.24** and **5.17** (start a transcription, then rebuild — the dialog
must say BUSY).

Verification commands:
```bash
transcription-pipeline/.venv/bin/python3 -m pytest shared/tests/ -q               # 719
(cd transcription-pipeline && .venv/bin/python3 -m pytest tests/ -q)              # 69
(cd usb-extractor && ../transcription-pipeline/.venv/bin/python3 -m pytest tests/ -q)  # 157
transcription-pipeline/.venv/bin/python3 -m ruff check --select E,F,W --ignore E501,E402,E741 .
cd hidock-mic-trigger && xcodegen generate && \
  GITHUB_ACTIONS=true xcodebuild -project hidock-mic-trigger.xcodeproj \
  -scheme hidock-mic-trigger -configuration Debug -derivedDataPath /tmp/hidock-check
```
`xcodegen generate` is **mandatory** before building — the project and `Info.plist` are
generated, and a hand-edit to `Info.plist` is silently discarded.

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
| Jenny Helland, Adam Mohamedally, Johan Nystrom | every clip rejected as contaminated — need a clean sample |
| Emma Thorne | **alias, not contamination.** Scores **1.0** against candidate-library `Emma`, and both are built from the same two transcripts (HiD33, HiD41). Awaiting James's decision on `merge_candidate_speakers("Emma", "Emma Thorne")` |

**Jenny's clean path is now open** and is the recommended next step: Rec82 is correctly
diarised, so naming its Speaker 2 in the viewer produces an uncontaminated 666 s
exemplar — but only *after* the Swift deploy, or the confirmation will go to the live
library and not the naming one.

Note `candidate_only` holds 20 names the live library lacks, mostly pre-canonicalisation
first-name forms (`Adam`, `Andy`, `Emma`, `John`, `Lucy R`, `Oster`) from the 25 July
bulk build. **Do not infer aliases from shared provenance** — `Adam` and `Andy` share
transcripts with `Emma Thorne` merely by attending the same meetings. Embedding
similarity is the only trustworthy signal, which is why the guard uses it.

## IN FLIGHT — the A/B that must be read before this branch merges

Two detached worktrees are running `diarisation_eval`, 24 meetings, **full length**,
started ~11:05, roughly **4.3 min/meeting → ~3.4 h for both arms** (expect ~14:30):

```
/private/tmp/.../scratchpad/eval-before.json   # 9f7f610, pre-fix
/private/tmp/.../scratchpad/eval-after.json    # 5f547b3, post-fix
                            eval-before.log / eval-after.log
```

**The default eval does not exercise the merge path at all** — it passes
`n_speakers=None`, so `_merge_labels_to_count` never runs. `--n-speakers-from-truth` is
the arm that measures this change, and that is what is running.

Judge it on **count error and name recall above confusion.** Confusion rate is a trap:
with a one-to-one truth mapping, extra clusters make each *mapped* cluster purer while
the transcript degrades. Expect the fix to leave *more* labels surviving the merge than
before, so watch count bias for over-splitting. If counts regressed, the likely cause is
the micro-label absorption, which can be reverted independently of the budget fix.

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

## Outstanding work

| # | Item | Why it matters |
|---|---|---|
| — | **Read the A/B above** | Gates merging `feature/rec82-merge-count-budget` |
| — | **Deploy the Swift change** | Confirmations do not teach the naming library until then |
| — | Decide `merge_candidate_speakers("Emma", "Emma Thorne")` | Alias proven at cosine 1.0 |
| — | Name Rec82's Speaker 2 as Jenny (after deploy) | Gives her a clean 666 s exemplar |
| — | Audit the live library for more mixed-block contamination | Jenny's was human-verified and still wrong |
| — | Give `Garry Clarke` a usable exemplar | Enrolled but inert; no active sample |
| — | **Evaluate pyannote community-1** | Licence-clean, addresses count/assignment; needs the HF token (Models page → Hugging Face access, stored in Keychain) |
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
