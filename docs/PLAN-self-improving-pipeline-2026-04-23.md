# Self-improving pipeline — in-app model-research + PR-creation loop
Research date: 2026-04-23
Suggested by: Chris Laidler
Status: plan only — not implemented

## The idea

STT and speaker-diarization models improve constantly. Whisper, Parakeet, Sortformer and TEN VAD are the current best-of-class; some of them didn't exist six months ago. Manually tracking the state of the art for every stage of our pipeline (transcription / diarization / VAD / speaker embeddings) is slow and easy to miss.

Build a **"Check for Model Updates"** function inside the app. Clicking it:

1. Spawns a Claude Code agent against this repo, in a terminal embedded in the app (we already ship SwiftTerm — the Terminal… menu item exists in commit `52c76d4`).
2. The agent reads `shared/models.py` MODEL_REGISTRY to understand the current backends at each stage.
3. The agent researches what has changed in the field — browsing papers/leaderboards, HuggingFace model cards, the colleague's `transcribe.py` (already part of our reference set), GitHub trending for speech tooling, etc.
4. For any model that looks like a credible upgrade for an existing stage, the agent:
   a. Creates a feature branch;
   b. Adds the new entry to `MODEL_REGISTRY` with the `stage`/`backend_key`/`pip_package` metadata the current schema expects;
   c. Writes the integration wiring (e.g. a new `shared/asr_<backend>.py` module or a new diarizer adapter);
   d. Updates `pipeline_backends.json` semantics if the new model has unusual requirements;
   e. Opens a PR with:
      - A summary of *why* this model is an upgrade (benchmark numbers, paper citations);
      - The cost to add (pip package size, extra disk footprint, runtime cost on Apple Silicon);
      - A comparison matrix against the incumbent;
      - A test-plan checklist the human reviewer can walk through.
5. The PR is **not auto-merged** — a human (James, for now) reviews and signs off.

The end state: the pipeline continuously trends toward the state of the art without a person having to periodically trawl the ASR leaderboard.

## Why this works for *this* repo specifically

The Model Manager restructure (committed 0a9bdaa and follow-ups) already gave every pipeline stage a uniform contract:

- Stages are discrete (transcription / diarization / vad / embedding) with a per-stage `backend_key` picker.
- Install flavours are plumbed: file-download, pip-package, built-in, nemo-managed.
- Persistence lives in `pipeline_backends.json`; switching backends is a one-line user action.

An agent can *add a new backend by editing exactly one data structure plus one adapter file.* The shape of the work is narrow enough that an agent can produce a reviewable diff reliably. This is the key — if model-adoption required rewriting the transcription orchestrator every time, the agent would never converge on something safe.

## What the agent needs

**Access + permissions:**
- Read/write access to the repo (already has it when run inside Claude Code).
- Ability to run extractor/transcription tests to verify nothing regresses (`pytest` in both venvs).
- `gh` CLI configured for PR creation (already present in this machine's session).
- HuggingFace / web access for model-card research (WebFetch tool).

**Boundaries we need to enforce (plan before implementing):**
- Must not auto-install or pip-install anything into the user's venvs during *research*. Testing happens only after the user agrees and runs the PR's install action.
- Must open a PR, never push directly to `main`.
- Must respect the CLAUDE.md rule: new feature branches, no direct-to-main commits.
- Must cap PR frequency — running weekly is fine; daily is too noisy.

**Inputs we feed the agent:**
- This plan file, as the standing brief.
- `shared/models.py` and the Model Manager UI code, as the integration contract.
- `docs/PLAN-sortformer-diarization-2026-04-23.md` and `docs/BENCH-whisper-vs-parakeet-2026-04-20.md` as examples of how to structure a model-adoption proposal.
- The latest `pipeline_backends.json` as context for what the user currently runs.

## UX sketch

Under Model Manager, a new small button at the top:

  **[ Check for Updates ]  last run: 3 days ago  •  2 open PRs**

Clicking opens a terminal sheet (SwiftTerm is already embedded) with the Claude Code agent running live. Agent output streams into the sheet. The user can watch the research, cancel mid-research, or walk away and come back to find either "no new models worth adopting" logged, or one/more PRs ready for review on GitHub.

A separate background scheduler (not this button) runs the same flow weekly on a cron — opt-in.

## Why the human stays in the loop

Model selection for this app isn't a pure quality optimization:

- **Privacy / license constraints** — commercial models (Cobra VAD etc.) are disqualified regardless of benchmark scores.
- **Runtime cost** — a model that's 2% more accurate but 10× slower on Apple Silicon is worse for meeting transcription.
- **Dependency weight** — NeMo added 2 GB to the transcription-pipeline venv. A few more of those and the app becomes unshippable for non-dev users.
- **Windows parity** — anything Mac-only creates cross-platform debt.

These are judgement calls the human needs to make. The agent's job is to *surface candidates with the context needed to decide*, not to decide unilaterally.

## Implementation sketch (not yet started)

**Phase 1 — Manual trigger:**
- Add "Check for Updates" button to Model Manager.
- On click, launch `claude-code` CLI in the existing SwiftTerm sheet, passing a brief prompt file + this repo's root as the working directory.
- Brief file lives at `docs/AGENT-BRIEF-model-research.md` (separate from this plan) — tight, instructional, written for the agent's consumption not humans.
- Agent sandboxing: run with read-only sandbox for the research phase; only the PR creation step needs write access.

**Phase 2 — Scheduled trigger:**
- CronCreate-style scheduled trigger, opt-in, weekly cadence.
- Same brief, same output — just fires without user intervention.
- Results accumulate in GitHub PRs; user gets notified via their normal GitHub email/Slack.

**Phase 3 — Feedback loop:**
- When the user merges or closes a PR with a reason ("too slow", "license incompatible"), the agent reads the closure reason and learns which kinds of models are worth proposing next time.
- Stored as a small `docs/AGENT-MEMORY-model-selection.md` that grows over time.

## Open questions

- Cost: each agent run consumes tokens. Weekly research = predictable cost; manual-trigger = user-controlled.
- Authentication: inside SwiftTerm the agent needs the user's Anthropic API credentials. Pass via env var from the Mac app? Prompt the user to log in first?
- What prevents the agent from getting enthusiastic and proposing every new model it finds? A max-open-PRs-per-week cap in the brief, and an explicit "don't propose models with <X% leaderboard improvement" guideline.

## Rejected alternatives

- **Auto-merge with a delay.** Too risky for things that touch transcription quality — a bad model silently replacing Whisper would be discovered only after the user notices their transcripts getting worse.
- **Just subscribe to the HF leaderboard.** Works for ASR but not the other stages (VAD, diarization, embeddings) — and doesn't handle license / platform / cost filtering.
- **Manual model-adoption in the roadmap.** What we do today. Doesn't scale and relies on noticing things.

## Planned

- [ ] Draft `docs/AGENT-BRIEF-model-research.md` — the instruction set for the Claude Code agent.
- [ ] Add "Check for Updates" button to ModelManagerView (Swift).
- [ ] Wire the button to open the Terminal sheet with `claude-code` pre-prompted.
- [ ] Decide the auth story (env-var pass-through vs agent-side login prompt).
- [ ] Phase 2: CronCreate schedule for weekly runs (opt-in).
- [ ] Phase 3: learn-from-closures memory file.

## References

- Claude Code CLI: https://claude.com/claude-code
- The existing embedded Terminal: commit `52c76d4` ("Add embedded PTY terminal (SwiftTerm)").
- Current Model Manager contract: `shared/models.py` MODEL_REGISTRY + `shared/tests/test_models.py`.
- Model-adoption precedent structure: `docs/PLAN-sortformer-diarization-2026-04-23.md`.
