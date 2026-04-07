# MCP Server — Meeting Knowledge for AI Agents

Exposes your transcripts, people, action items, and search to AI agents via the [Model Context Protocol](https://modelcontextprotocol.io) (MCP). Works with Claude Desktop, Cursor, Windsurf, and other MCP-compatible clients.

## Setup

### Claude Desktop

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS, `%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "meetings": {
      "command": "python3",
      "args": ["/path/to/HiDock-Mic-Trigger/mcp-server/server.py"]
    }
  }
}
```

### Cursor / Windsurf

Add an MCP server with:
- **Command**: `python3 /path/to/HiDock-Mic-Trigger/mcp-server/server.py`
- **Transport**: stdio

### Manual testing

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","clientInfo":{"name":"test"},"capabilities":{}}}' | python3 server.py
```

## Available tools

| Tool | Description | Parameters |
|------|-------------|------------|
| `search_meetings` | Full-text search across all transcripts | `query` (string), `limit` (int, default 20) |
| `get_meeting` | Get a specific meeting by filename or title | `identifier` (string) |
| `get_recent_meetings` | List recent meetings | `days` (int, default 7), `limit` (int, default 20) |
| `get_person_profile` | Get a person's meeting history, action items, and topics | `name` (string) |
| `list_people` | List all known people with meeting counts | (none) |
| `list_action_items` | List action items across all meetings | `status` (open/completed/all), `assignee` (string) |
| `search_by_person` | Find all meetings involving a person | `name` (string), `limit` (int) |
| `search_by_tag` | Find all meetings with a specific tag | `tag` (string), `limit` (int) |
| `get_stats` | Summary statistics about the knowledge graph | (none) |
| `rebuild_index` | Rebuild the knowledge graph from transcript files | (none) |

## Available resources

| URI | Description |
|-----|-------------|
| `meetings://stats` | JSON summary of meeting count, people, action items |

## Example queries

Once configured, you can ask your AI agent:

- "What did I promise Sarah last week?"
- "List all open action items"
- "Find meetings about the Q2 roadmap"
- "What topics has James been involved in?"
- "Show me recent meetings from the past 3 days"

## Architecture

- **Transport**: JSON-RPC over stdio (newline-delimited)
- **Protocol**: MCP 2024-11-05
- **Backend**: `shared/knowledge.py` SQLite knowledge graph
- **Dependencies**: Python 3.11+, no pip installs needed (uses only shared modules)

The knowledge graph is rebuilt from transcript files on first access. Subsequent calls use the cached index. Call `rebuild_index` to refresh after new transcriptions.

## Tests

18 tests covering protocol, all tool calls, and resources:

```bash
python -m pytest mcp-server/tests/test_server.py -v
```
