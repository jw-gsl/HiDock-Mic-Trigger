"""Cross-meeting intelligence — relationship scoring, consistency reports,
stale commitment tracking, and topic trends.

Builds on the knowledge graph to surface patterns across meetings that
individual transcripts can't reveal.

Usage:
    from shared.intelligence import MeetingIntelligence

    intel = MeetingIntelligence(knowledge_graph)
    report = intel.consistency_report()
    contacts = intel.relationship_map()
    trends = intel.topic_trends()
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone, timedelta

from shared.knowledge import KnowledgeGraph


class MeetingIntelligence:
    """Cross-meeting analysis engine built on the knowledge graph."""

    def __init__(
        self,
        kg: KnowledgeGraph,
        losing_touch_days: int = 21,
        stale_action_days: int = 14,
    ):
        self.kg = kg
        self.losing_touch_days = losing_touch_days
        self.stale_action_days = stale_action_days

    # ── Relationship Scoring ───────────────────────────────────────────────

    def relationship_map(self) -> list[dict]:
        """Compute relationship scores for all known people.

        Score formula (inspired by minutes):
            score = meeting_count * recency_weight * topic_depth
        where:
            recency_weight = 1.0 / (1.0 + days_since_last / 30.0)
            topic_depth = min(topic_count / 3.0, 1.0)

        Returns:
            List of dicts sorted by score descending:
            {name, score, meeting_count, last_meeting, days_since,
             topics, losing_touch, open_actions}
        """
        conn = self.kg._get_conn()
        now = datetime.now(timezone.utc)

        people = conn.execute("""
            SELECT p.id, p.name,
                   COUNT(DISTINCT mp.meeting_id) as meeting_count,
                   MAX(m.date) as last_meeting
            FROM people p
            JOIN meeting_people mp ON mp.person_id = p.id
            JOIN meetings m ON m.id = mp.meeting_id
            GROUP BY p.id
        """).fetchall()

        results = []
        for person in people:
            person_id = person["id"]
            name = person["name"]
            meeting_count = person["meeting_count"]
            last_meeting = person["last_meeting"] or ""

            # Calculate days since last meeting
            days_since = self._days_since(last_meeting, now)

            # Recency weight: decays over 30 days
            recency_weight = 1.0 / (1.0 + days_since / 30.0)

            # Topic depth: how many distinct topics discussed
            topics = conn.execute("""
                SELECT t.tag, COUNT(*) as count
                FROM tags t
                JOIN meeting_people mp ON mp.meeting_id = t.meeting_id
                WHERE mp.person_id = ?
                GROUP BY t.tag
                ORDER BY count DESC
            """, (person_id,)).fetchall()
            topic_count = len(topics)
            topic_depth = min(topic_count / 3.0, 1.0)

            # Final score
            score = round(meeting_count * recency_weight * topic_depth, 2)

            # Losing touch: 3+ meetings but absent > threshold days
            losing_touch = meeting_count >= 3 and days_since > self.losing_touch_days

            # Open action items for this person
            open_actions = conn.execute("""
                SELECT COUNT(*) FROM action_items
                WHERE assignee LIKE ? AND status = 'open'
            """, (f"%{name}%",)).fetchone()[0]

            results.append({
                "name": name,
                "score": score,
                "meeting_count": meeting_count,
                "last_meeting": last_meeting,
                "days_since": days_since,
                "topics": [{"tag": t["tag"], "count": t["count"]} for t in topics[:5]],
                "losing_touch": losing_touch,
                "open_actions": open_actions,
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    # ── Consistency Report ─────────────────────────────────────────────────

    def consistency_report(self) -> dict:
        """Detect decision conflicts and stale commitments across meetings.

        Returns:
            Dict with:
            - stale_actions: Action items older than threshold with 'open' status
            - potential_conflicts: Decisions on the same topic from different meetings
            - summary: {stale_count, conflict_count, people_losing_touch}
        """
        conn = self.kg._get_conn()
        now = datetime.now(timezone.utc)
        threshold = now - timedelta(days=self.stale_action_days)
        threshold_str = threshold.isoformat()

        # Find stale action items
        stale_actions = conn.execute("""
            SELECT a.id, a.task, a.assignee, a.due, a.status, a.confidence,
                   m.title as meeting_title, m.date as meeting_date, m.file_path
            FROM action_items a
            JOIN meetings m ON m.id = a.meeting_id
            WHERE a.status = 'open' AND m.date < ?
            ORDER BY m.date ASC
        """, (threshold_str,)).fetchall()
        stale_list = [dict(r) for r in stale_actions]

        # Find potential decision conflicts (same topic, different meetings)
        topic_decisions = conn.execute("""
            SELECT d.text, d.topic, d.confidence, m.title as meeting_title, m.date, m.file_path
            FROM decisions d
            JOIN meetings m ON m.id = d.meeting_id
            WHERE d.topic != ''
            ORDER BY d.topic, m.date DESC
        """).fetchall()

        # Group by topic and flag topics with multiple decisions
        conflicts = []
        topic_groups: dict[str, list[dict]] = {}
        for row in topic_decisions:
            topic = row["topic"].lower().strip()
            if topic not in topic_groups:
                topic_groups[topic] = []
            topic_groups[topic].append(dict(row))

        for topic, decisions in topic_groups.items():
            if len(decisions) >= 2:
                conflicts.append({
                    "topic": topic,
                    "decisions": decisions,
                    "count": len(decisions),
                })

        # Count people losing touch
        rel_map = self.relationship_map()
        losing_touch = [p for p in rel_map if p["losing_touch"]]

        return {
            "stale_actions": stale_list,
            "potential_conflicts": conflicts,
            "people_losing_touch": losing_touch,
            "summary": {
                "stale_count": len(stale_list),
                "conflict_count": len(conflicts),
                "losing_touch_count": len(losing_touch),
            },
        }

    # ── Topic Trends ───────────────────────────────────────────────────────

    def topic_trends(self, limit: int = 20) -> list[dict]:
        """Analyze topic frequency and recency across all meetings.

        Returns:
            List of topic dicts sorted by frequency:
            {tag, meeting_count, last_seen, first_seen, trending}
        """
        conn = self.kg._get_conn()
        now = datetime.now(timezone.utc)

        rows = conn.execute("""
            SELECT t.tag,
                   COUNT(DISTINCT t.meeting_id) as meeting_count,
                   MAX(m.date) as last_seen,
                   MIN(m.date) as first_seen
            FROM tags t
            JOIN meetings m ON m.id = t.meeting_id
            GROUP BY t.tag
            ORDER BY meeting_count DESC
            LIMIT ?
        """, (limit,)).fetchall()

        results = []
        for row in rows:
            days_since = self._days_since(row["last_seen"], now)
            # "Trending" if seen in the last 7 days with 2+ mentions
            trending = days_since < 7 and row["meeting_count"] >= 2
            results.append({
                "tag": row["tag"],
                "meeting_count": row["meeting_count"],
                "last_seen": row["last_seen"],
                "first_seen": row["first_seen"],
                "trending": trending,
            })

        return results

    # ── Research Topic ─────────────────────────────────────────────────────

    def research_topic(self, topic: str, limit: int = 20) -> dict:
        """Cross-meeting investigation of a topic.

        Aggregates all decisions, action items, key points, and meetings
        related to a topic.

        Args:
            topic: Topic to research (searched in tags, decisions, and FTS).

        Returns:
            Dict with: meetings, decisions, action_items, key_points, people.
        """
        conn = self.kg._get_conn()

        # Find meetings by tag
        tag_meetings = conn.execute("""
            SELECT DISTINCT m.id, m.file_path, m.title, m.date
            FROM meetings m
            JOIN tags t ON t.meeting_id = m.id
            WHERE t.tag LIKE ?
            ORDER BY m.date DESC
            LIMIT ?
        """, (f"%{topic}%", limit)).fetchall()
        meeting_ids = {r["id"] for r in tag_meetings}

        # Also find meetings via decision topics
        decision_meetings = conn.execute("""
            SELECT DISTINCT m.id, m.file_path, m.title, m.date
            FROM meetings m
            JOIN decisions d ON d.meeting_id = m.id
            WHERE d.topic LIKE ?
            ORDER BY m.date DESC
            LIMIT ?
        """, (f"%{topic}%", limit)).fetchall()
        for r in decision_meetings:
            meeting_ids.add(r["id"])

        # Also find meetings via FTS
        safe_query = '"' + topic.replace('"', '""') + '"'
        try:
            fts_meetings = conn.execute("""
                SELECT m.id, m.file_path, m.title, m.date
                FROM transcript_fts f
                JOIN meetings m ON m.file_path = f.file_path
                WHERE transcript_fts MATCH ?
                LIMIT ?
            """, (safe_query, limit)).fetchall()
            for r in fts_meetings:
                meeting_ids.add(r["id"])
        except sqlite3.OperationalError:
            pass

        if not meeting_ids:
            return {
                "topic": topic,
                "meetings": [],
                "decisions": [],
                "action_items": [],
                "key_points": [],
                "people": [],
            }

        placeholders = ",".join("?" * len(meeting_ids))
        ids = list(meeting_ids)

        # Gather all meetings
        meetings = conn.execute(f"""
            SELECT id, file_path, title, date, duration
            FROM meetings WHERE id IN ({placeholders})
            ORDER BY date DESC
        """, ids).fetchall()

        # Gather decisions from these meetings
        decisions = conn.execute(f"""
            SELECT d.text, d.topic, d.confidence, m.title as meeting_title, m.date
            FROM decisions d
            JOIN meetings m ON m.id = d.meeting_id
            WHERE d.meeting_id IN ({placeholders})
            ORDER BY m.date DESC
        """, ids).fetchall()

        # Gather action items
        action_items = conn.execute(f"""
            SELECT a.task, a.assignee, a.due, a.status, a.confidence,
                   m.title as meeting_title, m.date as meeting_date
            FROM action_items a
            JOIN meetings m ON m.id = a.meeting_id
            WHERE a.meeting_id IN ({placeholders})
            ORDER BY m.date DESC
        """, ids).fetchall()

        # Gather key points
        key_points = conn.execute(f"""
            SELECT k.text, k.confidence, m.title as meeting_title, m.date
            FROM key_points k
            JOIN meetings m ON m.id = k.meeting_id
            WHERE k.meeting_id IN ({placeholders})
            ORDER BY m.date DESC
        """, ids).fetchall()

        # Gather people involved
        people = conn.execute(f"""
            SELECT DISTINCT p.name, COUNT(DISTINCT mp.meeting_id) as involvement
            FROM people p
            JOIN meeting_people mp ON mp.person_id = p.id
            WHERE mp.meeting_id IN ({placeholders})
            GROUP BY p.id
            ORDER BY involvement DESC
        """, ids).fetchall()

        return {
            "topic": topic,
            "meetings": [dict(r) for r in meetings],
            "decisions": [dict(r) for r in decisions],
            "action_items": [dict(r) for r in action_items],
            "key_points": [dict(r) for r in key_points],
            "people": [dict(r) for r in people],
        }

    # ── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _days_since(date_str: str, now: datetime) -> float:
        """Calculate days between a date string and now."""
        if not date_str:
            return 999.0
        try:
            # Handle ISO format with or without timezone
            if "T" in date_str:
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            else:
                dt = datetime.fromisoformat(date_str + "T00:00:00+00:00")
            delta = now - dt
            return max(0.0, delta.total_seconds() / 86400)
        except (ValueError, TypeError):
            return 999.0


# ── CLI ─────────────────────────────────────────────────────────────────────


def _cli():
    """Command-line interface for cross-meeting intelligence."""
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Cross-Meeting Intelligence")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("relationships", help="Show relationship map with scores")
    sub.add_parser("consistency", help="Show consistency report (stale items, conflicts)")
    sub.add_parser("topics", help="Show topic trends")

    p_research = sub.add_parser("research", help="Research a topic across meetings")
    p_research.add_argument("topic", help="Topic to research")

    args = parser.parse_args()

    from shared.knowledge import KnowledgeGraph
    from shared.config_store import get_config

    config = get_config()
    kg = KnowledgeGraph()
    intel = MeetingIntelligence(
        kg,
        losing_touch_days=config.get("knowledge", "losing_touch_days", 21),
        stale_action_days=config.get("knowledge", "stale_action_item_days", 14),
    )

    if args.command == "relationships":
        results = intel.relationship_map()
        print(json.dumps(results, indent=2))
    elif args.command == "consistency":
        report = intel.consistency_report()
        print(json.dumps(report, indent=2))
    elif args.command == "topics":
        trends = intel.topic_trends()
        print(json.dumps(trends, indent=2))
    elif args.command == "research":
        results = intel.research_topic(args.topic)
        print(json.dumps(results, indent=2))
    else:
        parser.print_help()

    kg.close()


if __name__ == "__main__":
    _cli()
