# Agent access to HiDock transcripts & summaries (MCP + optional CLI)

Research date: 2026-06-20
Trigger: liked the EveryInc **monologue-toolkit** pattern (CLI + MCP/agent skill
giving read access to dictated notes/transcripts) and asked to bring that
thinking to HiDock meeting transcripts.

## Current state — most of it already exists

HiDock already ships an **MCP server** (`mcp-server/server.py`, stdio JSON-RPC)
backed by a knowledge graph (`shared/knowledge.py`). It exposes (richer than
monologue-toolkit):
- `search_meetings`, `get_meeting`, `get_recent_meetings`
- `search_by_person`, `get_person_profile`, `list_people`, `relationship_map`
- `list_action_items`, `search_by_tag`, `research_topic`, `topic_trends`
- `get_stats`, `rebuild_index`, `consistency_report`, `health_check`,
  `recent_events`

Enable it today in Claude Desktop (`claude_desktop_config.json`):
```json
{ "mcpServers": { "meetings": {
  "command": "python3",
  "args": ["/Users/jameswhiting/_git/hidock-tools/mcp-server/server.py"] } } }
```

So the "monologue-for-HiDock" idea is largely **already built** for agent use.

## The gap — it indexes raw transcripts, not the new typed Summaries

`KnowledgeGraph` scans only **`~/HiDock/Raw Transcripts/*.md`**
(`shared/knowledge.py:46, 325`). It does **not** see the newer
**`~/HiDock/Summaries/*.md`** with their classification frontmatter
(`type`, `area`, `title`, `recorded`, `transcript`). So an agent can't yet:
- list/filter summaries by **type** ("my Job Interview summaries", "Brainstorming notes"),
- filter by **area**, or fetch a recording's polished summary directly.

This is exactly where the monologue-notes thinking maps onto HiDock's
summarisation epic.

## Proposed additions

1. **Index the Summaries folder.** Parse each `~/HiDock/Summaries/*.md`
   frontmatter into a lightweight store (reuse the sqlite/knowledge pattern, or
   a simple in-memory scan — there are tens of files, not thousands).
2. **New MCP tools** on the existing server:
   - `search_summaries(query, limit)` — full-text over summary bodies.
   - `list_summaries(type?, area?, since?)` — filter by classification.
   - `get_summary(recording|title)` — return a specific typed summary.
   - `summary_stats()` — counts by type/area.
3. **Optional thin CLI** (the monologue `notes list/search/get` shape) for
   terminal use, if wanted beyond MCP — e.g. `transcribe_cpp.py notes list|search|get`,
   reusing the same index. (MCP already covers agent access, so CLI is additive.)
4. **Easier enable** — a one-liner / helper that writes the
   `claude_desktop_config.json` entry instead of hand-editing.

## Why this is the right shape
- Reuses what exists (server + knowledge graph) — small, additive.
- Connects the summarisation work (typed Summaries) to the agent layer, so
  Claude can act on *curated* meeting summaries, not just raw transcripts.
- No API keys / cloud — everything is local files, same as the rest of HiDock.

## Status
Planned. Next decision: (a) just enable the existing MCP server now; (b) build
the Summaries index + tools; (c) add the optional CLI.
