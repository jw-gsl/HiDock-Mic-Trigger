"""Tests for shared.intelligence — cross-meeting intelligence."""
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from shared.intelligence import MeetingIntelligence
from shared.knowledge import KnowledgeGraph


def _write_meeting(
    transcripts_dir: Path,
    name: str,
    *,
    title: str = "Test Meeting",
    speakers: list[str] | None = None,
    action_items: list[dict] | None = None,
    decisions: list[dict] | None = None,
    tags: list[str] | None = None,
    days_ago: int = 0,
) -> Path:
    """Helper to write a transcript with specific metadata."""
    date = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    path = transcripts_dir / f"{name}.md"
    from shared.transcript_writer import build_frontmatter
    frontmatter = build_frontmatter(
        title=title,
        date=date,
        speakers=speakers or [],
        action_items=action_items or [],
        decisions=decisions or [],
        tags=tags or [],
    )
    content = f"{frontmatter}\n\n## Transcript\n\nSome content about {title}.\n"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def intel_setup():
    """Create a knowledge graph with several meetings for testing."""
    with tempfile.TemporaryDirectory() as d:
        td = Path(d) / "transcripts"
        td.mkdir()

        # Meeting 1: Recent, with Alice
        _write_meeting(td, "meeting1", title="Q2 Planning",
                       speakers=["Alice", "Bob"],
                       action_items=[
                           {"task": "Review roadmap", "assignee": "Alice", "status": "open"},
                           {"task": "Update docs", "assignee": "Bob", "status": "open"},
                       ],
                       decisions=[{"text": "Ship v2.0 in April", "topic": "release"}],
                       tags=["planning", "engineering"],
                       days_ago=2)

        # Meeting 2: Recent, with Alice again
        _write_meeting(td, "meeting2", title="Sprint Review",
                       speakers=["Alice", "Charlie"],
                       action_items=[
                           {"task": "Fix bug #42", "assignee": "Charlie", "status": "open"},
                       ],
                       decisions=[{"text": "Delay v2.0 to May", "topic": "release"}],
                       tags=["engineering", "sprint"],
                       days_ago=5)

        # Meeting 3: Old, with Alice (for relationship scoring)
        _write_meeting(td, "meeting3", title="Kickoff",
                       speakers=["Alice"],
                       tags=["planning"],
                       days_ago=15)

        # Meeting 4: Very old, with Dave (for losing touch)
        _write_meeting(td, "meeting4", title="Design Review",
                       speakers=["Dave"],
                       tags=["design"],
                       days_ago=30)
        _write_meeting(td, "meeting5", title="Design Sprint",
                       speakers=["Dave"],
                       tags=["design"],
                       days_ago=35)
        _write_meeting(td, "meeting6", title="Design Critique",
                       speakers=["Dave"],
                       tags=["design"],
                       days_ago=40)

        # Meeting 5: Stale action item
        _write_meeting(td, "meeting7", title="Old Planning",
                       action_items=[
                           {"task": "Write proposal", "assignee": "Eve", "status": "open"},
                       ],
                       tags=["planning"],
                       days_ago=20)

        kg = KnowledgeGraph(db_path=Path(d) / "test.db", transcripts_dir=td)
        kg.rebuild()

        intel = MeetingIntelligence(kg, losing_touch_days=21, stale_action_days=14)
        yield intel, kg
        kg.close()


class TestRelationshipMap:
    def test_returns_all_people(self, intel_setup):
        intel, _ = intel_setup
        rel_map = intel.relationship_map()
        names = {p["name"] for p in rel_map}
        assert "Alice" in names
        assert "Bob" in names
        assert "Charlie" in names
        assert "Dave" in names

    def test_scores_are_positive(self, intel_setup):
        intel, _ = intel_setup
        rel_map = intel.relationship_map()
        for person in rel_map:
            assert person["score"] >= 0

    def test_sorted_by_score(self, intel_setup):
        intel, _ = intel_setup
        rel_map = intel.relationship_map()
        scores = [p["score"] for p in rel_map]
        assert scores == sorted(scores, reverse=True)

    def test_alice_higher_than_dave(self, intel_setup):
        intel, _ = intel_setup
        rel_map = intel.relationship_map()
        by_name = {p["name"]: p for p in rel_map}
        # Alice: 3 meetings, recent → high score
        # Dave: 3 meetings, old → lower score due to recency decay
        assert by_name["Alice"]["score"] > by_name["Dave"]["score"]

    def test_losing_touch_detected(self, intel_setup):
        intel, _ = intel_setup
        rel_map = intel.relationship_map()
        by_name = {p["name"]: p for p in rel_map}
        # Dave: 3 meetings but last was 30 days ago
        assert by_name["Dave"]["losing_touch"] is True
        # Alice: 3 meetings, recent
        assert by_name["Alice"]["losing_touch"] is False

    def test_includes_topic_info(self, intel_setup):
        intel, _ = intel_setup
        rel_map = intel.relationship_map()
        by_name = {p["name"]: p for p in rel_map}
        alice_topics = [t["tag"] for t in by_name["Alice"]["topics"]]
        assert "planning" in alice_topics or "engineering" in alice_topics


class TestConsistencyReport:
    def test_finds_stale_actions(self, intel_setup):
        intel, _ = intel_setup
        report = intel.consistency_report()
        stale = report["stale_actions"]
        stale_tasks = [a["task"] for a in stale]
        assert "Write proposal" in stale_tasks

    def test_finds_potential_conflicts(self, intel_setup):
        intel, _ = intel_setup
        report = intel.consistency_report()
        conflicts = report["potential_conflicts"]
        # We have two decisions on topic "release": "Ship v2.0 in April" and "Delay v2.0 to May"
        release_conflicts = [c for c in conflicts if c["topic"] == "release"]
        assert len(release_conflicts) == 1
        assert release_conflicts[0]["count"] == 2

    def test_summary_counts(self, intel_setup):
        intel, _ = intel_setup
        report = intel.consistency_report()
        summary = report["summary"]
        assert summary["stale_count"] >= 1
        assert summary["conflict_count"] >= 1
        assert summary["losing_touch_count"] >= 1

    def test_losing_touch_in_report(self, intel_setup):
        intel, _ = intel_setup
        report = intel.consistency_report()
        losing = report["people_losing_touch"]
        names = [p["name"] for p in losing]
        assert "Dave" in names


class TestTopicTrends:
    def test_returns_topics(self, intel_setup):
        intel, _ = intel_setup
        trends = intel.topic_trends()
        tags = [t["tag"] for t in trends]
        assert "planning" in tags
        assert "engineering" in tags

    def test_sorted_by_frequency(self, intel_setup):
        intel, _ = intel_setup
        trends = intel.topic_trends()
        counts = [t["meeting_count"] for t in trends]
        assert counts == sorted(counts, reverse=True)

    def test_includes_dates(self, intel_setup):
        intel, _ = intel_setup
        trends = intel.topic_trends()
        for t in trends:
            assert "last_seen" in t
            assert "first_seen" in t


class TestResearchTopic:
    def test_finds_meetings_by_tag(self, intel_setup):
        intel, _ = intel_setup
        result = intel.research_topic("planning")
        assert len(result["meetings"]) >= 2

    def test_finds_decisions(self, intel_setup):
        intel, _ = intel_setup
        result = intel.research_topic("release")
        # "release" is a decision topic, should find it via tag or FTS
        assert len(result["decisions"]) >= 1 or len(result["meetings"]) >= 1

    def test_finds_people(self, intel_setup):
        intel, _ = intel_setup
        result = intel.research_topic("engineering")
        people_names = [p["name"] for p in result["people"]]
        assert "Alice" in people_names

    def test_empty_topic(self, intel_setup):
        intel, _ = intel_setup
        result = intel.research_topic("nonexistent_topic_xyz")
        assert result["meetings"] == []
        assert result["decisions"] == []


class TestUnknownDates:
    """Meetings with empty/unparseable dates must be treated as unknown —
    not classified as maximally stale (the old behaviour scored them as
    999 days ago and always-losing-touch / always-stale)."""

    @pytest.fixture
    def undated_setup(self):
        with tempfile.TemporaryDirectory() as d:
            td = Path(d) / "transcripts"
            td.mkdir()
            # Three meetings with Frank, all with an EMPTY date.
            for i in range(3):
                path = td / f"undated{i}.md"
                path.write_text(
                    "---\n"
                    f"title: Undated {i}\n"
                    "type: meeting\n"
                    "date: \n"
                    "speakers: [Frank]\n"
                    "action_items: \n"
                    "  - task: Do something\n"
                    "    status: open\n"
                    "decisions: []\n"
                    "key_points: []\n"
                    "tags: [misc]\n"
                    "---\n\n## Transcript\n\nHello.\n",
                    encoding="utf-8",
                )
            kg = KnowledgeGraph(db_path=Path(d) / "test.db", transcripts_dir=td)
            kg.rebuild()
            intel = MeetingIntelligence(kg, losing_touch_days=21, stale_action_days=14)
            yield intel, kg
            kg.close()

    def test_days_since_none_for_empty_and_garbage(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        assert MeetingIntelligence._days_since("", now) is None
        assert MeetingIntelligence._days_since("not-a-date", now) is None

    def test_not_losing_touch_when_dates_unknown(self, undated_setup):
        intel, _ = undated_setup
        rel_map = intel.relationship_map()
        frank = next(p for p in rel_map if p["name"] == "Frank")
        assert frank["losing_touch"] is False
        assert frank["days_since"] is None

    def test_undated_actions_not_stale(self, undated_setup):
        intel, _ = undated_setup
        report = intel.consistency_report()
        assert report["stale_actions"] == []

    def test_topic_trends_unknown_date_not_trending(self, undated_setup):
        intel, _ = undated_setup
        trends = intel.topic_trends()
        misc = next(t for t in trends if t["tag"] == "misc")
        assert misc["trending"] is False
