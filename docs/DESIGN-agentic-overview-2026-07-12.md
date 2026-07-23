# Agentic Architecture — Overview & Roadmap

Research date: 2026-07-12
Sources: every.to (Compound Engineering guide, "After Automation", Cora), github.com/EveryInc/compound-engineering-plugin, github.com/silverstein/minutes, AWS Well-Architected Generative AI Lens (Responsible AI), code.claude.com/docs/en/headless, huggingface.co/docs/hub/en/api, github.com/jitsi/jiwer
Companion docs: `DESIGN-agentic-capabilities-2026-07-12.md` · `DESIGN-self-improving-pipeline-2026-07-12.md` · `DESIGN-cover-layer-ui-2026-07-12.md`

## Vision

Stop making the transcript the product; make an **agentic loop with memory** the product.

Today the app records, syncs, transcribes and summarises — and then waits for the user to come back and read. The next stage is an app that *works between meetings*: agents that extract commitments and chase them, write the user a daily brief, prepare them for the next meeting, notice when a decision contradicts an earlier one, and continuously improve the transcription pipeline itself — all local-first, all through the user's existing `claude` CLI subscription (no API keys), and all governed by a visible, responsible-AI control surface.

Three workstreams, one backbone:

1. **Agentic capabilities** — multistep autonomous agents (Doc 2).
2. **Self-improving pipeline** — scheduled model-update checks, evaluated by the CLI AI before adoption (Doc 3).
3. **Cover-layer UI** — a radio-toggled natural-language assistant layer mapping visibly to AWS's 8 Responsible-AI pillars (Doc 4).

## Current state (verified 2026-07-12)

`main` tip = `c7a38f6` (2026-07-03). The relevant scaffolding already exists and is load-bearing:

- `shared/llm_cli.py` — keyless LLM layer (claude → codex → gemini → ollama); `query_streaming()` already parses `claude --print --output-format stream-json`.
- `shared/models.py` — `MODEL_REGISTRY` (transcription/diarization/VAD/embedding backends); active backends persisted in `~/HiDock/pipeline_backends.json`.
- `shared/hooks.py` — `run_hooks_pipeline()` fires post-transcription (called from `transcription-pipeline/transcribe.py`).
- `shared/event_log.py` (SQLite event log), `shared/intelligence.py` (relationship scoring, consistency reports, `research_topic`), `shared/knowledge.py` (FTS5 knowledge graph).
- `mcp-server/server.py` — ~18 MCP tools over summaries/knowledge — already the exact toolset agents need.
- Embedded Claude Code terminal on both platforms (`TerminalPane.swift` / `terminal_pane.py`).
- Release self-update pollers (`UpdateChecker.swift` / `core/update_checker.py`) — the pattern to clone for model watching.

### In-flight work this design must sequence behind

A five-branch stack sits ahead of `main` (~8,185 insertions at tip, 2026-07-09):
`feature/formatted-cli-view` → `fix/audit-bugfixes` (PR #49) → `fix/audit-deferred` (PR #50) → `fix/voice-library-pythonpath` → `feature/ux-windows-voicelib`.

It contains the **speaker-tagging loop + Re-cluster UI**, diarization stitching fixes, the LED dot-matrix ticker, a large robustness/audit pass (739 tests), `llm_cli` stream hardening, and a compound-engineering example config. This design treats all of that as **in-flight, not re-proposed**. Two consequences:

- **Merge the stack first.** PRs below that touch `llm_cli.py`, `diarize_*`, `voice_library_lite.py`, or the main windows must land after the stack merges, or be rebased onto it.
- **The tagging loop is our ground-truth factory.** User-tagged speakers and corrected transcripts become the eval golden set (Doc 3) and the fairness evidence in the cover layer (Doc 4).

## Architectural spine

One decision everything hangs on: **a Python agent runtime in `shared/agents/`, invoked as a subprocess CLI, communicating with both UIs exclusively through append-only files in `~/HiDock/Agent Activity/`.**

```
python -m shared.agents {run,tick,list,pending,approve,deny,intent,tail}
```

- **No daemon, no new IPC.** This mirrors how every existing feature works — the Swift app and PyQt6 app already shell out to venv Python and parse JSON. The UIs become dumb renderers of two file contracts (activity stream + pending-actions queue), so **cross-platform parity is nearly free**.
- **Agents execute via the user's CLI subscription.** An extended `llm_cli.query_agent()` runs `claude --print --output-format stream-json --include-partial-messages`, with `--allowedTools` scoped per agent definition and `--mcp-config` pointing at the existing `mcp-server/server.py`. No API keys; never `--bare` (it skips subscription OAuth).
- **Graceful degradation.** Non-claude engines (codex/gemini/ollama) can't drive tools, so agents degrade to *propose-only* with context pre-fetched by Python — same philosophy `llm_cli` uses today.
- **Files are the bus.** `activity.jsonl` (append-only, rotated) + one JSON file per pending action (atomic rename `pending/` → `decided/`). No shared mutable blob, no lock contention between Swift, PyQt6 and Python workers.

Full contracts, agent definitions and autonomy tiers are in Doc 2.

## Design principles (from the research)

- **Compound engineering** (every.to / EveryInc): plan → work → review → **compound**. Roughly 80% of the value is in planning and review; every user decision must teach the system. Durable, inspectable **lesson files** (`~/HiDock/Lessons/`) are the memory substrate — the app-domain equivalent of the plugin's `docs/solutions/`. Agents review agents before output reaches the user.
- **Cora's ambient-briefing pattern** (every.to): the agent works in the background and writes you a scannable **Brief**; the primary surface holds only items needing a human response, with pre-drafted outputs. Not a chat app.
- **minutes** (silverstein/minutes, the project's existing muse — see `gap-analysis-vs-minutes.md`): CLI-subscription LLM access, MCP surface, markdown+frontmatter as source of truth. We extend where it stops: a governed agent runtime with approval queues and evals.
- **Atomic, verifiable agent actions** (EveryInc hands-on-deck / proof-sdk): agents emit typed proposals with provenance, never freeform side effects. Every action is inspectable, reversible, and carries who/what/why.
- **AWS Responsible AI 8 dimensions** rendered as *features, not claims* (Doc 4): Fairness, Explainability, Privacy & Security, Safety, Controllability, Veracity & Robustness, Governance, Transparency.

## Combined roadmap — nine PR-sized milestones

Each PR: both platforms, `PARITY.md` updated, tests, per CLAUDE.md workflow.

| # | PR | Ships | Doc |
|---|----|-------|-----|
| 1 | Agent foundations | `shared/agents/` core (activity, actions, registry, lessons, CLI), `llm_cli.query_agent`, config sections, event types — headless end-to-end | 2 |
| 2 | Cover layer v1 | Read-only natural-language feed + radio toggle, both UIs; existing transcribe/summarise flows instrumented as `pipeline` events | 4 |
| 3 | Runner + post-meeting agent | First real agent, approval cards (approve/edit/deny + "what should happen instead"), lessons on every decision | 2, 4 |
| 4 | NL input + intent router | Bottom input line; deterministic intents (<100 ms) with agent fallback over MCP, streamed | 2, 4 |
| 5 | Eval harness | `shared/evals/` headless, golden-set builder from corrected transcripts, first baseline doc | 3 |
| 6 | Model watch | `shared/model_watch.py`, `watch` metadata in `MODEL_REGISTRY`, "Check for Updates" in both Model Managers (research-only cards) | 3 |
| 7 | Assessment + adoption | model-scout agent, sandbox evals, adopt/rollback, launchd/schtasks scheduling | 3 |
| 8 | daily-brief + steward | Briefings directory, Cora-style Brief, commitment chasing, opt-in M365 read integration | 2 |
| 9 | corrections-learner | Edit/denial diffing → corrections + lesson distillation — closes the compound loop | 2 |

## Risks

- **claude CLI flag drift** (`--allowedTools`, `--mcp-config`, output formats): detect `claude --version` at startup, pin a minimum, degrade to propose-only.
- **File contention** across three processes: solved structurally (append-only JSONL, one-file-per-action, atomic rename).
- **Windows `claude` discovery** is less reliable than macOS's login-shell PATH: reuse `terminal_pane.py` shell detection; add a `health_check.py` probe.
- **Sandbox eval cost**: NeMo-class candidates mean multi-GB venvs; unattended assessment is opt-in, otherwise user-initiated per candidate.
- **Golden-set privacy**: user recordings/corrections never leave `~/HiDock/Evals/`; only aggregate metrics are committed to repo docs.
- **Noise and token budgets**: per-agent run-time/token caps and max-actions-per-week in config; an agent that spams cards trains the user to ignore them.
- **Sequencing**: the in-flight stack must merge before PRs touching its files.

## Planned

- [ ] PR 1–2 (foundations + cover layer v1) — first visible slice
- [ ] PR 3–4 (first agent + steering)
- [ ] PR 5–7 (self-improving pipeline)
- [ ] PR 8–9 (ambient agents + compound loop)

## Rejected / Not Applicable

- **A resident agent daemon** — rejected; subprocess + files matches the app's architecture and avoids lifecycle/permissions complexity.
- **Chat-first UI** — rejected; the 2025–26 agent-UX literature and Cora both point to briefing-first ambient surfaces with approval queues.
- **API-key integrations** — rejected; the CLI-subscription pattern (from minutes) stays the only LLM path.
- **Auto-send for email/calendar** — rejected permanently by user decision; drafts are approve/edit/deny cards only.

