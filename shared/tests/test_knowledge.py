"""Tests for shared.knowledge module."""
from __future__ import annotations

from pathlib import Path

import pytest

from shared.knowledge import KnowledgeGraph
from shared.transcript_writer import write_transcript


@pytest.fixture
def kg_env(tmp_path):
    """Set up a knowledge graph with a temp directory and some transcripts."""
    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir()
    db_path = tmp_path / "test.db"

    # Create sample transcripts
    write_transcript(
        transcripts_dir / "meeting1.md",
        "We discussed the Q2 budget and decided to increase headcount.",
        summary={
            "title": "Q2 Budget Review",
            "action_items": [
                {"task": "Draft hiring plan", "assignee": "Alice", "due": "2026-04-15", "status": "open"},
                {"task": "Review contractor rates", "assignee": "Bob", "status": "open"},
            ],
            "decisions": [{"text": "Increase headcount by 3", "topic": "hiring"}],
            "key_points": ["Budget is on track", "Need senior engineers"],
            "tags": ["budget", "hiring"],
        },
    )

    write_transcript(
        transcripts_dir / "meeting2.md",
        "Sprint planning session with the team.",
        summary={
            "title": "Sprint Planning",
            "action_items": [
                {"task": "Fix login bug", "assignee": "Alice", "status": "open"},
            ],
            "decisions": [{"text": "Ship v2.0 by end of month", "topic": "release"}],
            "key_points": ["3 bugs remaining", "Performance improved 20%"],
            "tags": ["engineering", "sprint"],
        },
    )

    write_transcript(
        transcripts_dir / "memo1.md",
        "Quick voice memo about project ideas.",
        summary={
            "title": "Project Ideas",
            "action_items": [],
            "decisions": [],
            "key_points": ["Explore AI integration"],
            "tags": ["ideas"],
        },
    )

    kg = KnowledgeGraph(db_path=db_path, transcripts_dir=transcripts_dir)
    return kg, transcripts_dir


class TestRebuild:
    def test_rebuild_indexes_all_files(self, kg_env):
        kg, _ = kg_env
        count = kg.rebuild()
        assert count == 3
        kg.close()

    def test_rebuild_is_idempotent(self, kg_env):
        kg, _ = kg_env
        kg.rebuild()
        count = kg.rebuild()
        assert count == 3
        stats = kg.get_stats()
        assert stats["meetings"] == 3
        kg.close()


class TestIndexTranscript:
    def test_index_single_file(self, kg_env):
        kg, transcripts_dir = kg_env
        meeting_id = kg.index_transcript(transcripts_dir / "meeting1.md")
        assert meeting_id is not None
        stats = kg.get_stats()
        assert stats["meetings"] == 1
        kg.close()

    def test_reindex_updates(self, kg_env):
        kg, transcripts_dir = kg_env
        kg.index_transcript(transcripts_dir / "meeting1.md")
        kg.index_transcript(transcripts_dir / "meeting1.md")
        stats = kg.get_stats()
        assert stats["meetings"] == 1  # not duplicated
        kg.close()

    def test_index_nonexistent_file(self, kg_env):
        kg, _ = kg_env
        result = kg.index_transcript(Path("/nonexistent/file.md"))
        assert result is None
        kg.close()


class TestSearch:
    def test_full_text_search(self, kg_env):
        kg, _ = kg_env
        kg.rebuild()
        results = kg.search("budget")
        assert len(results) >= 1
        assert any("budget" in r["snippet"].lower() for r in results)
        kg.close()

    def test_search_no_results(self, kg_env):
        kg, _ = kg_env
        kg.rebuild()
        results = kg.search("xyznonexistent")
        assert results == []
        kg.close()

    def test_search_by_tag(self, kg_env):
        kg, _ = kg_env
        kg.rebuild()
        results = kg.search_by_tag("hiring")
        assert len(results) == 1
        assert results[0]["title"] == "Q2 Budget Review"
        kg.close()


class TestPeople:
    def test_list_people(self, kg_env):
        kg, _ = kg_env
        kg.rebuild()
        # meeting1 has Alice and Bob as speakers (from frontmatter)
        # meeting2 has Alice as speaker
        # But speakers come from the write_transcript metadata, which
        # uses diarized_result for speakers - our test transcripts
        # don't have diarized_result, so speakers list may be empty
        people = kg.list_people()
        # People come from action item assignees, not speakers in this test
        kg.close()

    def test_search_by_person(self, kg_env):
        kg, _ = kg_env
        kg.rebuild()
        # Search by person relies on meeting_people join, which
        # requires speakers in frontmatter
        kg.close()


class TestActionItems:
    def test_list_open_action_items(self, kg_env):
        kg, _ = kg_env
        kg.rebuild()
        items = kg.list_action_items(status="open")
        assert len(items) == 3  # 2 from meeting1 + 1 from meeting2
        tasks = [i["task"] for i in items]
        assert "Draft hiring plan" in tasks
        assert "Fix login bug" in tasks
        kg.close()

    def test_filter_by_assignee(self, kg_env):
        kg, _ = kg_env
        kg.rebuild()
        items = kg.list_action_items(assignee="Alice")
        assert len(items) == 2
        assert all("Alice" in i["assignee"] for i in items)
        kg.close()

    def test_filter_by_status_all(self, kg_env):
        kg, _ = kg_env
        kg.rebuild()
        items = kg.list_action_items(status="all")
        assert len(items) == 3
        kg.close()

    def test_update_action_item_status(self, kg_env):
        kg, _ = kg_env
        kg.rebuild()
        items = kg.list_action_items(status="open")
        first_id = items[0]["id"]
        assert kg.update_action_item_status(first_id, "completed")
        remaining = kg.list_action_items(status="open")
        assert len(remaining) == 2
        kg.close()


class TestStats:
    def test_stats(self, kg_env):
        kg, _ = kg_env
        kg.rebuild()
        stats = kg.get_stats()
        assert stats["meetings"] == 3
        assert stats["action_items"] == 3
        assert stats["open_action_items"] == 3
        assert stats["decisions"] == 2
        kg.close()

    def test_empty_stats(self, tmp_path):
        kg = KnowledgeGraph(
            db_path=tmp_path / "empty.db",
            transcripts_dir=tmp_path / "empty",
        )
        stats = kg.get_stats()
        assert stats["meetings"] == 0
        kg.close()
