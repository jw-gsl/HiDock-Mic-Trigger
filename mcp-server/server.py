#!/usr/bin/env python3
"""MCP Server — exposes the knowledge graph to AI agents.

Implements the Model Context Protocol (MCP) over stdio, making
transcripts, people, action items, and search queryable by
Claude Desktop, Cursor, Windsurf, and other MCP-compatible clients.

This is a standalone stdio server that reads JSON-RPC messages from
stdin and writes responses to stdout, following the MCP specification.

Usage:
    python server.py

Configure in Claude Desktop's config:
    {
        "mcpServers": {
            "meetings": {
                "command": "python",
                "args": ["/path/to/mcp-server/server.py"]
            }
        }
    }
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Add repo root to path for shared module imports
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.knowledge import KnowledgeGraph
from shared.intelligence import MeetingIntelligence

# Server metadata
SERVER_NAME = "meetings"
SERVER_VERSION = "0.2.0"

# Lazy-initialized knowledge graph and intelligence
_kg: KnowledgeGraph | None = None
_intel: MeetingIntelligence | None = None


def _get_kg() -> KnowledgeGraph:
    global _kg
    if _kg is None:
        _kg = KnowledgeGraph()
        # Auto-rebuild on first access
        _kg.rebuild()
    return _kg


def _get_intel() -> MeetingIntelligence:
    global _intel
    if _intel is None:
        _intel = MeetingIntelligence(_get_kg())
    return _intel


# ── Tool Definitions ────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "search_meetings",
        "description": "Full-text search across all meeting transcripts. Returns matching meetings with snippets.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (supports AND, OR, NOT, quoted phrases)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 10)",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_meeting",
        "description": "Get the full content of a specific meeting transcript by filename or title search.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "identifier": {
                    "type": "string",
                    "description": "Filename (e.g. 'meeting1.md') or title to search for",
                },
            },
            "required": ["identifier"],
        },
    },
    {
        "name": "get_recent_meetings",
        "description": "List the most recent meetings with titles and dates.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of meetings to return (default 10)",
                    "default": 10,
                },
            },
        },
    },
    {
        "name": "get_person_profile",
        "description": "Get a person's profile: meeting history, open action items, and topics discussed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Person's name (partial match supported)",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "list_people",
        "description": "List all people mentioned across meetings with meeting counts and last contact date.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "list_action_items",
        "description": "List action items across all meetings. Filter by status and assignee.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["open", "completed", "all"],
                    "description": "Filter by status (default: open)",
                    "default": "open",
                },
                "assignee": {
                    "type": "string",
                    "description": "Filter by assignee name (partial match)",
                },
            },
        },
    },
    {
        "name": "search_by_person",
        "description": "Find all meetings involving a specific person.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Person's name (partial match)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 20)",
                    "default": 20,
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "search_by_tag",
        "description": "Find all meetings with a specific topic tag.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tag": {
                    "type": "string",
                    "description": "Topic tag (e.g. 'engineering', 'budget')",
                },
            },
            "required": ["tag"],
        },
    },
    {
        "name": "get_stats",
        "description": "Get summary statistics: total meetings, people, action items, decisions.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "rebuild_index",
        "description": "Rebuild the knowledge graph index from transcript files. Run this if transcripts have been added or modified outside the app.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "research_topic",
        "description": "Deep cross-meeting research on a topic. Aggregates all decisions, action items, key points, and people related to the topic across every meeting.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Topic to research (searched in tags, decisions, and full-text)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max meetings to include (default 20)",
                    "default": 20,
                },
            },
            "required": ["topic"],
        },
    },
    {
        "name": "consistency_report",
        "description": "Detect decision conflicts, stale commitments, and people you're losing touch with across all meetings.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "relationship_map",
        "description": "Compute relationship scores for all known people based on meeting frequency, recency, and topic depth. Flags people you may be losing touch with.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "health_check",
        "description": "Run a system health check — verifies directories, database, models, engines, and recent errors.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "recent_events",
        "description": "View recent system events from the event log (transcriptions, errors, etc.).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max events to return (default 20)",
                    "default": 20,
                },
                "event_type": {
                    "type": "string",
                    "description": "Filter by event type (e.g. 'transcription_completed', 'error')",
                },
            },
        },
    },
    {
        "name": "topic_trends",
        "description": "Analyze topic frequency and recency across all meetings. Shows which topics are trending and how often they appear.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max topics to return (default 20)",
                    "default": 20,
                },
            },
        },
    },
]

# ── Resources ───────────────────────────────────────────────────────────────

RESOURCES = [
    {
        "uri": "meetings://stats",
        "name": "Meeting Statistics",
        "description": "Summary statistics about the meeting knowledge base",
        "mimeType": "application/json",
    },
]

# ── Tool Handlers ───────────────────────────────────────────────────────────


def handle_search_meetings(args: dict) -> str:
    kg = _get_kg()
    results = kg.search(args["query"], limit=args.get("limit", 10))
    if not results:
        return "No meetings found matching your query."
    lines = []
    for r in results:
        lines.append(f"**{r['title']}** ({r['date']})")
        lines.append(f"  {r['snippet']}")
        lines.append(f"  File: {Path(r['file_path']).name}")
        lines.append("")
    return "\n".join(lines)


def handle_get_meeting(args: dict) -> str:
    kg = _get_kg()
    identifier = args["identifier"]

    # Try direct file match first (validate path stays within transcripts_dir)
    transcripts_dir = kg.transcripts_dir
    direct = (transcripts_dir / identifier).resolve()
    if direct.exists() and str(direct).startswith(str(transcripts_dir.resolve())):
        return direct.read_text(encoding="utf-8")

    # Search by title
    results = kg.search(identifier, limit=1)
    if results:
        path = Path(results[0]["file_path"])
        if path.exists():
            return path.read_text(encoding="utf-8")

    return f"No meeting found matching '{identifier}'."


def handle_get_recent_meetings(args: dict) -> str:
    kg = _get_kg()
    conn = kg._get_conn()
    rows = conn.execute(
        """SELECT title, date, file_path, duration, summary_text
           FROM meetings ORDER BY date DESC LIMIT ?""",
        (args.get("limit", 10),),
    ).fetchall()

    if not rows:
        return "No meetings in the knowledge base."

    lines = []
    for r in rows:
        duration = f" ({r['duration']:.0f}s)" if r["duration"] else ""
        lines.append(f"- **{r['title']}** — {r['date']}{duration}")
        if r["summary_text"]:
            lines.append(f"  {r['summary_text'][:150]}")
    return "\n".join(lines)


def handle_get_person_profile(args: dict) -> str:
    kg = _get_kg()
    profile = kg.get_person_profile(args["name"])
    if not profile:
        return f"No person found matching '{args['name']}'."

    lines = [
        f"# {profile['name']}",
        f"**Meetings:** {profile['meeting_count']}",
    ]
    if profile["last_meeting_date"]:
        lines.append(f"**Last meeting:** {profile['last_meeting_date']}")

    if profile["topics"]:
        lines.append("\n**Topics:** " + ", ".join(
            f"{t['tag']} ({t['count']})" for t in profile["topics"]
        ))

    if profile["open_action_items"]:
        lines.append("\n**Open Action Items:**")
        for item in profile["open_action_items"]:
            due = f" (due: {item['due']})" if item.get("due") else ""
            lines.append(f"- {item['task']}{due}")

    if profile["meetings"]:
        lines.append("\n**Recent Meetings:**")
        for m in profile["meetings"][:10]:
            lines.append(f"- {m['title']} — {m['date']}")

    return "\n".join(lines)


def handle_list_people(args: dict) -> str:
    kg = _get_kg()
    people = kg.list_people()
    if not people:
        return "No people found in the knowledge base."
    lines = []
    for p in people:
        lines.append(f"- **{p['name']}** — {p['meeting_count']} meetings, last: {p['last_meeting']}")
    return "\n".join(lines)


def handle_list_action_items(args: dict) -> str:
    kg = _get_kg()
    items = kg.list_action_items(
        status=args.get("status", "open"),
        assignee=args.get("assignee"),
    )
    if not items:
        return f"No {args.get('status', 'open')} action items found."
    lines = []
    for item in items:
        assignee = f" (@{item['assignee']})" if item.get("assignee") else ""
        due = f" [due: {item['due']}]" if item.get("due") else ""
        meeting = f" — from {item['meeting_title']}" if item.get("meeting_title") else ""
        status = f" [{item['status']}]" if item.get("status") != "open" else ""
        conf = f" ({item['confidence']})" if item.get("confidence") and item["confidence"] != "high" else ""
        lines.append(f"- {item['task']}{assignee}{due}{status}{conf}{meeting}")
    return "\n".join(lines)


def handle_search_by_person(args: dict) -> str:
    kg = _get_kg()
    results = kg.search_by_person(args["name"], limit=args.get("limit", 20))
    if not results:
        return f"No meetings found involving '{args['name']}'."
    lines = []
    for r in results:
        lines.append(f"- **{r['title']}** — {r['date']}")
    return "\n".join(lines)


def handle_search_by_tag(args: dict) -> str:
    kg = _get_kg()
    results = kg.search_by_tag(args["tag"])
    if not results:
        return f"No meetings found with tag '{args['tag']}'."
    lines = []
    for r in results:
        lines.append(f"- **{r['title']}** — {r['date']}")
    return "\n".join(lines)


def handle_get_stats(args: dict) -> str:
    kg = _get_kg()
    stats = kg.get_stats()
    return json.dumps(stats, indent=2)


def handle_rebuild_index(args: dict) -> str:
    global _kg, _intel
    import time as _time
    from shared.event_log import log_event, EventType
    start = _time.monotonic()
    _kg = KnowledgeGraph()
    count = _kg.rebuild()
    _intel = None  # Reset intelligence so it picks up new data
    log_event(EventType.INDEX_REBUILD, duration_s=round(_time.monotonic() - start, 1),
              metadata={"count": count})
    return f"Index rebuilt. {count} transcripts indexed."


def handle_research_topic(args: dict) -> str:
    intel = _get_intel()
    result = intel.research_topic(args["topic"], limit=args.get("limit", 20))

    if not result["meetings"]:
        return f"No meetings found related to '{args['topic']}'."

    lines = [f"# Research: {args['topic']}", ""]

    lines.append(f"**{len(result['meetings'])} meetings found:**")
    for m in result["meetings"]:
        duration = f" ({m['duration']:.0f}s)" if m.get("duration") else ""
        lines.append(f"- **{m['title']}** — {m['date']}{duration}")

    if result["decisions"]:
        lines.append(f"\n**Decisions ({len(result['decisions'])}):**")
        for d in result["decisions"]:
            conf = f" [{d['confidence']}]" if d.get("confidence") and d["confidence"] != "high" else ""
            lines.append(f"- {d['text']}{conf} (from {d['meeting_title']}, {d['date']})")

    if result["action_items"]:
        lines.append(f"\n**Action Items ({len(result['action_items'])}):**")
        for a in result["action_items"]:
            assignee = f" @{a['assignee']}" if a.get("assignee") else ""
            status = f" [{a['status']}]" if a.get("status") != "open" else ""
            conf = f" ({a['confidence']})" if a.get("confidence") and a["confidence"] != "high" else ""
            lines.append(f"- {a['task']}{assignee}{status}{conf}")

    if result["key_points"]:
        lines.append(f"\n**Key Points ({len(result['key_points'])}):**")
        for kp in result["key_points"]:
            conf = f" [{kp['confidence']}]" if kp.get("confidence") and kp["confidence"] != "high" else ""
            lines.append(f"- {kp['text']}{conf} (from {kp['meeting_title']})")

    if result["people"]:
        lines.append(f"\n**People involved ({len(result['people'])}):**")
        for p in result["people"]:
            lines.append(f"- {p['name']} ({p['involvement']} meetings)")

    return "\n".join(lines)


def handle_consistency_report(args: dict) -> str:
    intel = _get_intel()
    report = intel.consistency_report()
    summary = report["summary"]

    lines = ["# Consistency Report", ""]
    lines.append(f"**Stale actions:** {summary['stale_count']} | "
                 f"**Conflicts:** {summary['conflict_count']} | "
                 f"**Losing touch:** {summary['losing_touch_count']}")

    if report["stale_actions"]:
        lines.append("\n## Stale Action Items")
        for a in report["stale_actions"]:
            assignee = f" @{a['assignee']}" if a.get("assignee") else ""
            lines.append(f"- {a['task']}{assignee} — from {a['meeting_title']} ({a['meeting_date']})")

    if report["potential_conflicts"]:
        lines.append("\n## Potential Decision Conflicts")
        for c in report["potential_conflicts"]:
            lines.append(f"\n**Topic: {c['topic']}** ({c['count']} decisions)")
            for d in c["decisions"]:
                lines.append(f"  - \"{d['text']}\" — {d['meeting_title']} ({d['date']})")

    if report["people_losing_touch"]:
        lines.append("\n## People Losing Touch")
        for p in report["people_losing_touch"]:
            lines.append(f"- **{p['name']}** — {p['meeting_count']} meetings, "
                         f"last {p['days_since']:.0f} days ago")

    return "\n".join(lines)


def handle_relationship_map(args: dict) -> str:
    intel = _get_intel()
    rel_map = intel.relationship_map()

    if not rel_map:
        return "No people found in the knowledge base."

    lines = ["# Relationship Map", ""]
    for p in rel_map:
        flag = " ⚠️ losing touch" if p["losing_touch"] else ""
        actions = f" ({p['open_actions']} open actions)" if p["open_actions"] else ""
        topics = ", ".join(t["tag"] for t in p["topics"][:3]) if p["topics"] else "—"
        lines.append(
            f"- **{p['name']}** — score: {p['score']}, "
            f"{p['meeting_count']} meetings, "
            f"last {p['days_since']:.0f}d ago, "
            f"topics: {topics}{actions}{flag}"
        )

    return "\n".join(lines)


def handle_health_check(args: dict) -> str:
    from shared.health_check import run_health_check
    report = run_health_check()

    status_icons = {"ok": "+", "warning": "!", "error": "X"}
    lines = [f"# Health Check: {report['overall'].upper()}", ""]
    lines.append(f"**{report['summary']['ok']}** ok, "
                 f"**{report['summary']['warnings']}** warnings, "
                 f"**{report['summary']['errors']}** errors\n")

    for check in report["checks"]:
        icon = status_icons.get(check["status"], "?")
        lines.append(f"[{icon}] **{check['name']}**: {check['message']}")

    return "\n".join(lines)


def handle_recent_events(args: dict) -> str:
    from shared.event_log import recent_events as get_events
    events = get_events(
        limit=args.get("limit", 20),
        event_type=args.get("event_type"),
    )
    if not events:
        return "No events recorded yet."

    lines = ["# Recent Events", ""]
    for ev in events:
        err = f" **ERROR**: {ev.error}" if ev.error else ""
        dur = f" ({ev.duration_s:.1f}s)" if ev.duration_s else ""
        fp = f" — {ev.file_path}" if ev.file_path else ""
        lines.append(f"- `{ev.timestamp}` **{ev.event_type}**{fp}{dur}{err}")

    return "\n".join(lines)


def handle_topic_trends(args: dict) -> str:
    intel = _get_intel()
    trends = intel.topic_trends(limit=args.get("limit", 20))

    if not trends:
        return "No topics found in the knowledge base."

    lines = ["# Topic Trends", ""]
    for t in trends:
        trending = " 🔥 trending" if t["trending"] else ""
        lines.append(
            f"- **{t['tag']}** — {t['meeting_count']} meetings, "
            f"last seen: {t['last_seen']}{trending}"
        )

    return "\n".join(lines)


_TOOL_HANDLERS = {
    "search_meetings": handle_search_meetings,
    "get_meeting": handle_get_meeting,
    "get_recent_meetings": handle_get_recent_meetings,
    "get_person_profile": handle_get_person_profile,
    "list_people": handle_list_people,
    "list_action_items": handle_list_action_items,
    "search_by_person": handle_search_by_person,
    "search_by_tag": handle_search_by_tag,
    "get_stats": handle_get_stats,
    "rebuild_index": handle_rebuild_index,
    "research_topic": handle_research_topic,
    "consistency_report": handle_consistency_report,
    "relationship_map": handle_relationship_map,
    "topic_trends": handle_topic_trends,
    "health_check": handle_health_check,
    "recent_events": handle_recent_events,
}

# ── MCP Protocol Handler ───────────────────────────────────────────────────


def handle_request(request: dict) -> dict:
    """Handle a single MCP JSON-RPC request."""
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params", {})

    if method == "initialize":
        return _success(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
                "resources": {},
            },
            "serverInfo": {
                "name": SERVER_NAME,
                "version": SERVER_VERSION,
            },
        })

    elif method.startswith("notifications/"):
        # No response needed for any notifications
        return None

    elif method == "tools/list":
        return _success(req_id, {"tools": TOOLS})

    elif method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})

        handler = _TOOL_HANDLERS.get(tool_name)
        if not handler:
            return _error(req_id, -32601, f"Unknown tool: {tool_name}")

        try:
            result_text = handler(tool_args)
            return _success(req_id, {
                "content": [{"type": "text", "text": result_text}],
            })
        except Exception as e:
            return _success(req_id, {
                "content": [{"type": "text", "text": f"Error: {e}"}],
                "isError": True,
            })

    elif method == "resources/list":
        return _success(req_id, {"resources": RESOURCES})

    elif method == "resources/read":
        uri = params.get("uri", "")
        if uri == "meetings://stats":
            kg = _get_kg()
            stats = kg.get_stats()
            return _success(req_id, {
                "contents": [{
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(stats, indent=2),
                }],
            })
        return _error(req_id, -32602, f"Unknown resource: {uri}")

    elif method == "ping":
        return _success(req_id, {})

    else:
        return _error(req_id, -32601, f"Method not found: {method}")


def _success(req_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


# ── Stdio Transport ────────────────────────────────────────────────────────


def main():
    """Run the MCP server over stdio."""
    print(f"MCP server '{SERVER_NAME}' v{SERVER_VERSION} starting...", file=sys.stderr)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            response = _error(None, -32700, "Parse error")
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
            continue

        response = handle_request(request)
        if response is not None:  # notifications don't get responses
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
