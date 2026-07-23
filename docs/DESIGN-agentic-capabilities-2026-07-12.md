# Agentic Capabilities — Agent Runtime, Contracts & Shipped Agents

Research date: 2026-07-12
Sources: every.to Compound Engineering guide + github.com/EveryInc/compound-engineering-plugin (lesson files, plan→work→review→compound), every.to "Introducing Cora" (ambient brief + needs-your-response queue), github.com/silverstein/minutes (CLI-subscription + MCP patterns), code.claude.com/docs/en/headless (`--print`, `stream-json`, `--allowedTools`, `--mcp-config`), github.com/EveryInc/proof-sdk (provenance), github.com/EveryInc/hands-on-deck (atomic typed agent actions)
Companion docs: overview in `DESIGN-agentic-overview-2026-07-12.md`; model-scout details in `DESIGN-self-improving-pipeline-2026-07-12.md`; UI in `DESIGN-cover-layer-ui-2026-07-12.md`

## Current State

The app already has every ingredient except the runtime: a keyless LLM layer (`shared/llm_cli.py`), an MCP server with ~18 knowledge tools (`mcp-server/server.py`), post-transcription hooks (`shared/hooks.py`), an audit-grade event log (`shared/event_log.py`), and cross-meeting intelligence (`shared/intelligence.py`). What's missing is the loop that *initiates* work, a queue that *gates* its effects, and memory that makes each run smarter than the last.

## Component architecture

### New package: `shared/agents/`

```
shared/agents/
  runner.py     # load agent def → inject lessons + context into prompt →
                # llm_cli.query_agent() → parse stream → write activity events,
                # artifacts, pending actions. Enforces autonomy tier, timeout,
                # token budget.
  registry.py   # discover defs: shared/agents/defs/*.md (shipped) +
                # ~/HiDock/Agents/*.md (user overrides; same name wins)
  activity.py   # append/tail ~/HiDock/Agent Activity/activity.jsonl (monthly
                # rotation); mirror significant events into event_log.py
  actions.py    # pending-actions queue: one JSON file per action in
                # Agent Activity/pending/, atomic rename to decided/ on
                # decision; per-kind apply handlers
  lessons.py    # ~/HiDock/Lessons/*.md durable memory; record_feedback(),
                # relevant_lessons(scope) for prompt injection
  intents.py    # natural-language router for the cover layer (see Doc 4)
  schedule.py   # launchd plist / schtasks installers; idempotent `tick`
  cli.py        # python -m shared.agents {run,tick,list,pending,approve,
                #   deny,intent,tail}
  defs/         # shipped agent definitions (markdown, §Agents below)
```

### Extensions to existing modules

- **`shared/llm_cli.py`** — add `query_agent(prompt, *, allowed_tools, mcp_config, on_event, timeout, extra_args)`. Claude-only; builds on the existing `query_streaming` Popen loop but forwards **all** stream events (`tool_use`, `content_block_delta`, `result`) to `on_event` so the activity feed can narrate tool use ("Searched meetings for 'Q3 budget'"). Other engines fall back to `query()` → agent runs propose-only with Python-pre-fetched context.
- **`shared/hooks.py`** — `run_hooks_pipeline()` gains a step: if `agents.post_meeting` is enabled, fire-and-forget `shared.agents.runner.trigger("post-meeting", transcript_path=…)` as a detached subprocess, so transcription never blocks on an agent.
- **`shared/event_log.py`** — new event types: `AGENT_RUN_STARTED/COMPLETED/FAILED`, `ACTION_CREATED/APPROVED/DENIED/APPLIED`, `MODEL_CHECK`, `EVAL_RUN`, `INTENT_EXECUTED`. This SQLite log **is** the governance pillar.
- **`shared/config_store.py`** — new sections: `[agents]` (enabled, post_meeting, daily_brief_time, engine, max_runtime_s, token_budget, max_actions_per_week, ms365_mcp_command), `[cover_layer]` (enabled, default_on), `[model_watch]` (enabled, cadence_days).
- **`mcp-server/server.py`** — phase-2 tools: `list_pending_actions`, `recent_agent_activity`, `record_lesson` — so agents can self-report and the intent fallback can answer "what did you do today?".

## Data contracts

### Activity event — `~/HiDock/Agent Activity/activity.jsonl` (append-only, rotated monthly)

```json
{"id":"evt_01J...","ts":"2026-07-12T09:14:03Z","run_id":"run_01J...","agent":"post-meeting",
 "type":"tool_use","title":"Searched meetings for 'Q3 budget'",
 "detail":"3 matches across May–July","refs":["~/HiDock/Summaries/2026-07-08-….md"],
 "provenance":{"engine":"claude","locality":"cloud"},"confidence":0.82}
```

- `type` ∈ `run_started | step | tool_use | artifact_written | proposal_created | run_finished | error | intent | intent_result | pipeline`. The `pipeline` type mirrors existing transcription/summarise progress so the feed narrates *everything* the app does, not just agents.
- `title` is always natural language; the UIs render it verbatim.
- `provenance.locality` (`local`/`cloud`) drives the privacy indicator in the cover layer.

### Pending action — one file per action, `pending/act_<ulid>.json`, atomic rename to `decided/`

```json
{"id":"act_01J...","created":"…","agent":"post-meeting","run_id":"…",
 "kind":"email_draft","title":"Send follow-up to Sam re: Q3 budget?",
 "summary_md":"Draft recaps 2 decisions and assigns 3 action items…",
 "payload":{"to":"sam@…","subject":"…","body_md":"…"},
 "expires":"2026-07-26T00:00:00Z","status":"pending",
 "decision":null,"edited_payload":null,"deny_reason":null,"applied_result":null}
```

- `kind` ∈ `email_draft | calendar_draft | commitment_add | correction_entry | adopt_model | prep_brief | settings_change | generic_proposal`. Each kind has an apply handler in `actions.py` (e.g. `adopt_model` → `models.apply_backend_adoption`; `email_draft` → clipboard / `mailto:` / optional M365 send **only after approval**).
- **Approve / edit / deny.** `edited_payload` holds the user's overwrite when they edit before approving (user decision: AI drafts often read poorly — the edit path is first-class, not an afterthought). `deny_reason` is the free-text "what should happen instead" and feeds `lessons.record_feedback()`.
- Expired actions auto-archive with `status: expired` — stale cards never linger.

### Agent definition — markdown with frontmatter (same one-line-per-key convention `summaries_index.py` already parses)

```markdown
---
name: post-meeting
description: Post-meeting pipeline — commitments, conflicts, follow-up draft
trigger: post_transcription
autonomy: propose
tools: mcp__meetings__*,Read
mcp: meetings
engine: claude
output_dir: ~/HiDock/Briefings
max_runtime_s: 300
lessons_scope: summaries,emails
---
You are the post-meeting agent. Transcript: {{transcript_path}}
1. Read the typed summary. 2. Extract commitments (owner, due).
3. Check each decision against past decisions (consistency_report,
research_topic). 4. Draft a follow-up email. Emit proposals; do not act.
```

- `trigger` ∈ `post_transcription | manual | daily@HH:MM | weekly:DDD@HH:MM | pre_meeting`.
- Shipped defs live in `shared/agents/defs/`; users can override or add in `~/HiDock/Agents/` (same-name wins) — the agent surface is itself steerable in natural language.

### Autonomy tiers (the controllability pillar, enforced by `runner.py`)

1. **observe** — read-only tools; output is artifacts only.
2. **propose** — artifacts + pending actions; nothing applied.
3. **act_with_approval** — apply handlers run only after user approval.
4. **autonomous_low_risk** — whitelisted kinds only (e.g. `correction_entry`), auto-applied but always logged and reversible.

### Lessons — `~/HiDock/Lessons/<scope>.md` + `feedback.jsonl`

Frontmatter (`scope:`, `updated:`, `sources:` action ids) + bullet lessons, e.g. *"Never propose follow-ups for 1:1s with direct reports — user always denies."* `runner.py` injects lessons whose scope intersects the agent's `lessons_scope` into every prompt. Raw approve/edit/deny feedback appends to `feedback.jsonl`; the corrections-learner periodically distils feedback → lessons via `llm_cli.query_json`. This is compound engineering's `docs/solutions/` loop applied to the app's own domain: **every unit of user attention makes the next run cheaper.**

## The six shipped agents

| Agent | Trigger | Autonomy | Steps (tools) | Output |
|---|---|---|---|---|
| **post-meeting** | `post_transcription` via `hooks.py` | propose | typed summary → extract commitments → decision-conflict check (`consistency_report`, `research_topic`) → gated follow-up draft | briefing artifact + `email_draft` / `commitment_add` cards |
| **daily-brief** | `daily@07:00` | propose | recent meetings → open action items → `relationship_map` losing-touch → (optional M365: today's calendar + relevant inbox threads) → Cora-style Brief | `~/HiDock/Briefings/YYYY-MM-DD-daily.md` + "needs your response" cards |
| **meeting-prep** | manual v1 ("prep me for my 2pm"); `pre_meeting` when M365 enabled | observe | attendees/topics → `get_person_profile`, `search_by_person`, `research_topic` → prep card | `prep_brief` artifact ("last spoke 12 days ago; 2 open commitments; they emailed Tuesday about X") |
| **steward** | `weekly:Mon@08:00` | propose | stale commitments (>14 d) + losing-touch (>21 d) → drafted chasers/check-ins, cross-referenced against M365 inbox when enabled ("you promised this in Tuesday's meeting *and* it was chased by email") | one gated card per nudge |
| **model-scout** | weekly + Model Manager button | act_with_approval | see `DESIGN-self-improving-pipeline-2026-07-12.md` | `adopt_model` verdict cards |
| **corrections-learner** | on decided actions + transcript/summary edits | autonomous_low_risk | diff user edit vs original → `corrections.json` entries + lesson distillation | auto-applied corrections (audited), lesson updates |

Multistep and autonomous is the point: post-meeting alone chains four knowledge-graph queries and a draft; daily-brief reads across every meeting plus (opt-in) the user's calendar and inbox; steward closes loops the user would otherwise drop. Together they turn passive archives into chased commitments, warmed relationships and pre-briefed meetings.

## Microsoft 365 integration (opt-in)

- **Read side is the headline value**: calendar (meeting-prep triggers, brief enrichment) and inbox (commitment cross-referencing, "what needs your response"). Configured as an extra MCP server included in the generated `--mcp-config` only when `[agents] ms365_mcp_command` is set. Off by default; every M365-touching event carries `locality: cloud` provenance in the feed.
- **Write side is allowed but always gated**: agents may *recommend* an email or calendar invite as an `email_draft`/`calendar_draft` card with approve / **edit** / deny. Nothing sends or books without explicit action; there is **no auto-send tier** and never will be (user decision — drafts frequently need a human rewrite, hence the first-class edit path).
- All agents degrade gracefully with M365 off: daily-brief skips calendar context, meeting-prep stays manual-only.

## Compound-engineering mapping

| Principle | Where it lands |
|---|---|
| Plan → work → review → compound | proposal cards (plan) → apply handlers (work) → approve/edit/deny (review) → lessons (compound) |
| Durable lesson files | `~/HiDock/Lessons/` — inspectable, editable, injected by scope |
| Agents reviewing agents | a cheap judge pass (via `llm_cli.query_json`) scores briefings for faithfulness before they surface; failures regenerate once, then surface flagged |
| Everything reviewed, teaching artifacts | every card decision logged with reason; the cover layer shows "what I learned from you this week" |
| 80/20 planning-review vs execution | user attention is spent on cards and briefs, never on watching agents run |

## Phasing

- **PR 1** — foundations: `activity.py`, `actions.py`, `registry.py`, `lessons.py`, `cli.py`, `query_agent`, config, event types, tests. Headless `python -m shared.agents` works end-to-end.
- **PR 3** — `runner.py` + post-meeting def + hooks trigger + approval cards + lessons-on-decision.
- **PR 4** — `intents.py` + input line + streaming fallback agent (see Doc 4).
- **PR 8** — daily-brief + steward + `~/HiDock/Briefings/` + opt-in M365.
- **PR 9** — corrections-learner + lesson distillation.

## Planned

- [ ] PR 1 foundations (highest priority — everything depends on the contracts)
- [ ] PR 3 first agent + cards
- [ ] PR 4 intent router
- [ ] PR 8 ambient agents + M365
- [ ] PR 9 compound loop closure

## Rejected / Not Applicable

- **Auto-send of emails/invites** — permanently rejected (user decision); gated cards only.
- **Long-running agent daemon** — rejected; scheduled `tick` + event-triggered subprocesses match the codebase.
- **Freeform agent side effects** — rejected; all effects flow through typed action kinds with apply handlers (hands-on-deck pattern).
- **Embedding-based memory store** — deferred; scoped markdown lessons are inspectable and sufficient at this scale.

