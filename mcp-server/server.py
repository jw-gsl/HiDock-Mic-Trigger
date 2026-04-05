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
from shared.transcript_writer import parse_frontmatter

# Server metadata
SERVER_NAME = "meetings"
SERVER_VERSION = "0.1.0"

# Lazy-initialized knowledge graph
_kg: KnowledgeGraph | None = None


def _get_kg() -> KnowledgeGraph:
    global _kg
    if _kg is None:
        _kg = KnowledgeGraph()
        # Auto-rebuild on first access
        _kg.rebuild()
    return _kg


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

    # Try direct file match first
    transcripts_dir = kg.transcripts_dir
    direct = transcripts_dir / identifier
    if direct.exists():
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
        lines.append(f"- {item['task']}{assignee}{due}{status}{meeting}")
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
    global _kg
    _kg = KnowledgeGraph()
    count = _kg.rebuild()
    return f"Index rebuilt. {count} transcripts indexed."


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

    elif method == "notifications/initialized":
        # No response needed for notifications
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
