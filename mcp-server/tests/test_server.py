"""Tests for MCP server tool handlers."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add repo root and mcp-server to path
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "mcp-server"))

from shared.knowledge import KnowledgeGraph
from shared.transcript_writer import write_transcript

import server


@pytest.fixture
def mcp_env(tmp_path):
    """Set up a knowledge graph with sample data and patch the MCP server to use it."""
    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir()
    db_path = tmp_path / "test.db"

    write_transcript(
        transcripts_dir / "budget_review.md",
        "We reviewed the Q2 budget and approved the hiring plan.",
        summary={
            "title": "Q2 Budget Review",
            "action_items": [
                {"task": "Draft hiring plan", "assignee": "Alice", "due": "2026-04-15", "status": "open"},
            ],
            "decisions": [{"text": "Increase headcount by 3", "topic": "hiring"}],
            "key_points": ["Budget on track"],
            "tags": ["budget", "hiring"],
            "summary_text": "Reviewed Q2 budget and approved hiring.",
        },
    )

    write_transcript(
        transcripts_dir / "sprint_planning.md",
        "Sprint planning session for the engineering team.",
        summary={
            "title": "Sprint Planning",
            "action_items": [
                {"task": "Fix login bug", "assignee": "Alice", "status": "open"},
                {"task": "Write tests", "assignee": "Bob", "status": "open"},
            ],
            "decisions": [{"text": "Ship v2.0 by Friday", "topic": "release"}],
            "key_points": ["3 bugs remaining"],
            "tags": ["engineering", "sprint"],
            "summary_text": "Planned sprint work, targeting v2.0 release.",
        },
    )

    kg = KnowledgeGraph(db_path=db_path, transcripts_dir=transcripts_dir)
    kg.rebuild()

    # Patch the server's knowledge graph
    server._kg = kg
    yield kg, transcripts_dir

    kg.close()
    server._kg = None


class TestProtocol:
    def test_initialize(self):
        response = server.handle_request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {},
        })
        assert response["result"]["serverInfo"]["name"] == "meetings"
        assert "tools" in response["result"]["capabilities"]

    def test_tools_list(self):
        response = server.handle_request({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
        })
        tools = response["result"]["tools"]
        names = [t["name"] for t in tools]
        assert "search_meetings" in names
        assert "get_person_profile" in names
        assert "list_action_items" in names

    def test_resources_list(self):
        response = server.handle_request({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "resources/list",
        })
        resources = response["result"]["resources"]
        assert len(resources) >= 1

    def test_unknown_method(self):
        response = server.handle_request({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "unknown/method",
        })
        assert "error" in response

    def test_notification_no_response(self):
        response = server.handle_request({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })
        assert response is None

    def test_ping(self):
        response = server.handle_request({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "ping",
        })
        assert "result" in response


class TestToolCalls:
    def test_search_meetings(self, mcp_env):
        response = server.handle_request({
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {"name": "search_meetings", "arguments": {"query": "budget"}},
        })
        text = response["result"]["content"][0]["text"]
        assert "Budget" in text

    def test_search_no_results(self, mcp_env):
        response = server.handle_request({
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {"name": "search_meetings", "arguments": {"query": "xyznonexistent"}},
        })
        text = response["result"]["content"][0]["text"]
        assert "No meetings" in text

    def test_get_meeting_by_filename(self, mcp_env):
        response = server.handle_request({
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {"name": "get_meeting", "arguments": {"identifier": "budget_review.md"}},
        })
        text = response["result"]["content"][0]["text"]
        assert "budget" in text.lower()

    def test_get_recent_meetings(self, mcp_env):
        response = server.handle_request({
            "jsonrpc": "2.0",
            "id": 13,
            "method": "tools/call",
            "params": {"name": "get_recent_meetings", "arguments": {"limit": 5}},
        })
        text = response["result"]["content"][0]["text"]
        assert "Budget Review" in text or "Sprint Planning" in text

    def test_list_action_items(self, mcp_env):
        response = server.handle_request({
            "jsonrpc": "2.0",
            "id": 14,
            "method": "tools/call",
            "params": {"name": "list_action_items", "arguments": {"status": "open"}},
        })
        text = response["result"]["content"][0]["text"]
        assert "Draft hiring plan" in text

    def test_list_action_items_by_assignee(self, mcp_env):
        response = server.handle_request({
            "jsonrpc": "2.0",
            "id": 15,
            "method": "tools/call",
            "params": {"name": "list_action_items", "arguments": {"assignee": "Alice"}},
        })
        text = response["result"]["content"][0]["text"]
        assert "Alice" in text

    def test_search_by_tag(self, mcp_env):
        response = server.handle_request({
            "jsonrpc": "2.0",
            "id": 16,
            "method": "tools/call",
            "params": {"name": "search_by_tag", "arguments": {"tag": "hiring"}},
        })
        text = response["result"]["content"][0]["text"]
        assert "Budget Review" in text

    def test_get_stats(self, mcp_env):
        response = server.handle_request({
            "jsonrpc": "2.0",
            "id": 17,
            "method": "tools/call",
            "params": {"name": "get_stats", "arguments": {}},
        })
        text = response["result"]["content"][0]["text"]
        stats = json.loads(text)
        assert stats["meetings"] == 2
        assert stats["action_items"] == 3

    def test_rebuild_index(self, mcp_env):
        kg, transcripts_dir = mcp_env
        # Patch KnowledgeGraph to use the test directory
        with patch("server.KnowledgeGraph") as MockKG:
            mock_instance = MockKG.return_value
            mock_instance.rebuild.return_value = 2
            response = server.handle_request({
                "jsonrpc": "2.0",
                "id": 18,
                "method": "tools/call",
                "params": {"name": "rebuild_index", "arguments": {}},
            })
            text = response["result"]["content"][0]["text"]
            assert "2 transcripts" in text

    def test_unknown_tool(self, mcp_env):
        response = server.handle_request({
            "jsonrpc": "2.0",
            "id": 19,
            "method": "tools/call",
            "params": {"name": "nonexistent_tool", "arguments": {}},
        })
        assert "error" in response


class TestResources:
    def test_read_stats_resource(self, mcp_env):
        response = server.handle_request({
            "jsonrpc": "2.0",
            "id": 20,
            "method": "resources/read",
            "params": {"uri": "meetings://stats"},
        })
        contents = response["result"]["contents"]
        assert len(contents) == 1
        stats = json.loads(contents[0]["text"])
        assert "meetings" in stats

    def test_unknown_resource(self, mcp_env):
        response = server.handle_request({
            "jsonrpc": "2.0",
            "id": 21,
            "method": "resources/read",
            "params": {"uri": "unknown://thing"},
        })
        assert "error" in response
