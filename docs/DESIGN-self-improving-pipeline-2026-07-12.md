# Self-Improving Pipeline — Model Watch, Eval Harness & CLI-Assessed Adoption

Research date: 2026-07-12
Sources: huggingface.co/docs/hub/en/api (`GET /api/models/{repo}` → `sha`/`lastModified`), docs.github.com/en/rest/releases/releases, pypi.org JSON API (`/pypi/{pkg}/json`), github.com/jitsi/jiwer (WER/CER), Hugging Face Open ASR Leaderboard, code.claude.com/docs/en/headless (`--output-format json --json-schema`), `transcription-pipeline/eval/wer.py` (in-tree Levenshtein), `transcription-pipeline/bench_backends.py`
Supersedes: `PLAN-self-improving-pipeline-2026-04-23.md` (local-only/git history `c7a38f6~1`). This doc carries forward its model-scout + eval-harness design, updated for the June–July codebase (typed summarisation suite, agent runtime from `DESIGN-agentic-capabilities-2026-07-12.md`, and the in-flight speaker-tagging loop).

## Goal

Local ASR/diarization models improve monthly (Whisper turbo revisions, Parakeet/Canary releases, whisper.cpp runtime gains). Today the app never notices: `MODEL_REGISTRY` is static and adoption is manual. The self-improving pipeline makes the app **check on a schedule, assess with the user's own CLI AI subscription against the user's own recordings, and adopt only through an approve/deny card with one-click rollback** — so the app is always getting better without ever getting worse unreviewed.

Three parts: a watcher (detect), an eval harness (measure), and the model-scout agent (research + verdict).

## 1. Model watch — `shared/model_watch.py`

`MODEL_REGISTRY` entries in `shared/models.py` gain optional `watch` metadata:

```python
"watch": {"hf_repo": "mlx-community/parakeet-tdt-0.6b-v2"}      # weights
"watch": {"github_releases": "ggerganov/whisper.cpp"}            # runtimes
"watch": {"pypi": "nemo-toolkit"}                                # pip backends
```

- Poll endpoints (cloned from the proven `UpdateChecker.swift` / `core/update_checker.py` pattern):
  - HF Hub: `GET https://huggingface.co/api/models/{repo}` — diff `sha` / `lastModified`
  - GitHub: `GET https://api.github.com/repos/{owner}/{repo}/releases/latest` — diff `tag_name`
  - PyPI: `GET https://pypi.org/pypi/{pkg}/json` — diff `.info.version` vs installed
- Last-seen state in `~/HiDock/model_watch.json`; a new finding emits a `proposal_created` activity event and spawns the model-scout assessment.
- **Field-scan mode** (the April plan's original idea, kept): periodically the scout also researches *beyond* known repos — leaderboards, release announcements — so genuinely new model families (a "Parakeet v3", a new Moonshine) get discovered, not just version bumps of what we already track.

## 2. Eval harness — `shared/evals/` (the missing dependency, and the bigger half)

No trustworthy adoption without measurement. Today only `transcription-pipeline/eval/wer.py` exists (a small Levenshtein WER). The harness generalises it:

```
shared/evals/
  golden.py    # build golden set from user-corrected transcripts +
               # *_diarized.json speaker tags → ~/HiDock/Evals/golden/<stem>.json
               #   {audio, ref_text, ref_turns:[{start,end,speaker}], corrected_at}
  metrics.py   # WER/CER (port + extend eval/wer.py; jiwer optional), DER via
               # greedy speaker mapping, RTF (audio-sec ÷ processing-sec)
  judge.py     # LLM-as-judge for summary quality via llm_cli — rubric:
               # faithfulness/no-hallucination, action-item recall, decision
               # capture, conciseness → structured JSON scores
  runner.py    # run active-or-candidate backend over the golden set → results
               # JSON: ~/HiDock/Evals/results/<stage>-<backend>-<date>.json
  compare.py   # baseline-vs-candidate diff report (markdown) — feeds the
               # verdict card and, when relevant, a PR body
  sandbox.py   # create/teardown candidate venv under ~/HiDock/Evals/sandbox/
```

- **The golden set is the user's own data**: every transcript the user corrects and every speaker they tag (the in-flight `feature/ux-windows-voicelib` tagging loop is the ground-truth factory) becomes a benchmark clip. The set grows as a free by-product of normal use — the compound principle applied to evaluation.
- **Privacy boundary**: golden clips and per-clip results never leave `~/HiDock/Evals/`. Only aggregate metrics may be committed to repo docs (`docs/EVAL-baseline-<combo>.md`). This tightens the April plan, which put results in-repo.
- **Adoption gate**: candidate must improve (or match within epsilon) WER/DER *and* keep RTF within budget — "more accurate but too slow" is a rejection, logged with numbers.
- The harness doubles as a **regression suite for our own pipeline changes** (silence-strip remapping, stitching fixes, VAD swaps), independent of model updates.

## 3. The model-scout agent (autonomy: act_with_approval)

Runs via the agent runtime (`DESIGN-agentic-capabilities-2026-07-12.md`), using the user's `claude` CLI subscription — this is the "inbuilt CLI AI assesses the quality of a new model release" step:

1. **Detect** — `model_watch.check()` finding, or field-scan discovery, or the user pressing "Check for Updates".
2. **Research** — `claude -p` with web tools: license (Whisper MIT vs Parakeet/Canary CC-BY attribution), size, claimed benchmarks, runtime requirements, community signal. Structured output via `--output-format json --json-schema`.
3. **Assess** — install into the sandbox venv (`sandbox.py`), run `evals.runner` over the golden set **per pipeline stage** it affects (transcription → WER/CER/RTF; diarization → DER; summarisation engine changes → `judge.py` scores).
4. **Verdict** — `compare.py` report distilled into an `adopt_model` card:

   > **Adopt Parakeet v3 for transcription?**
   > +2.1% WER on your 12-clip golden set · 1.4× faster · CC-BY-4.0 · 1.2 GB
   > [Approve] [Deny — tell it what instead] · full report →

5. **Adopt (on approval)** — `models.apply_backend_adoption(stage, backend_key)`:
   - snapshot `pipeline_backends.json` → `pipeline_backends.prev.json` (**one-click rollback**)
   - known backend → `set_active_backend`; new version of a known family → registry-overlay file `~/HiDock/model_registry_overrides.json` merged into `MODEL_REGISTRY` at load
   - **genuinely new adapter code** (new model family needing a new `transcribe_*.py`) escalates to the existing embedded Claude Code terminal for a supervised, PR-based flow — never auto-merged, per the April plan's hard rule.
6. **Learn** — deny reasons ("too big", "English-only, I need Dutch") land in `~/HiDock/Lessons/models.md` and pre-filter future candidates.

## 4. Scheduling

- `shared/agents/schedule.py install` writes `~/Library/LaunchAgents/com.hidock.agents.plist` (macOS) / `schtasks /create` (Windows) running `python -m shared.agents tick` hourly. `tick` is idempotent — per-agent last-run in `~/HiDock/Agent Activity/schedule_state.json` — and fires whatever is due (weekly model check, daily brief, weekly steward).
- In-app fallback: hourly `Timer` in `AppDelegate` / `QTimer` in `main_window.py` calling the same `tick`, for users who never install the LaunchAgent/task.
- UI entry point: **"Check for Updates"** button in `ModelManagerView.swift` and `Windows-App/ui/model_manager_dialog.py` headers → `agents run model-scout` immediately.

## v1 decisions

- **Never mutate live venvs.** Adoption that needs pip installs targets the sandbox first and applies to the app venv only via a restart-and-install step (mutating a venv while transcriptions are queued is the riskiest write in the whole design).
- **Unattended sandbox assessment is opt-in** — NeMo-class candidates mean multi-GB downloads and long runs; default is a research-only card with an "Assess" button.
- Budget caps: one assessment at a time (reuse the serial-queue pattern from summarisation), max candidates/week, token budget per run.

## Planned

- [ ] PR 5 — eval harness headless + `golden.py` builder + first `docs/EVAL-baseline-whisper-lite.md`
- [ ] PR 6 — `model_watch.py` + `watch` metadata + "Check for Updates" buttons (research-only cards)
- [ ] PR 7 — model-scout + `sandbox.py` + adoption/rollback + scheduling

## Rejected / Not Applicable

- **Auto-adoption on green evals** — rejected; adoption is always an approve/deny card (small golden sets can flatter a bad model; license/size judgement stays human).
- **Cloud benchmark upload** — rejected; evals run entirely on-device.
- **jiwer as a hard dependency** — optional; the in-tree Levenshtein WER is the fallback so evals work with zero new installs.
