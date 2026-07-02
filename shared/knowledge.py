"""Knowledge graph — SQLite index over transcript frontmatter.

Builds a queryable index of people, meetings, action items, decisions,
and topics from YAML frontmatter in transcript markdown files.

The SQLite database is a **rebuildable cache** — the markdown files are
the source of truth. The DB can be deleted and rebuilt at any time.

Usage:
    from shared.knowledge import KnowledgeGraph

    kg = KnowledgeGraph()
    kg.rebuild()  # scan all transcripts and rebuild index
    kg.index_transcript(path)  # index a single new transcript

    results = kg.search("budget review")
    people = kg.get_person_profile("Sarah")
    actions = kg.list_action_items(status="open")
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.transcript_writer import parse_frontmatter

# Default DB location — alongside transcripts
_DEFAULT_DB_NAME = "knowledge.db"


class KnowledgeGraph:
    """SQLite-backed knowledge graph over transcript files."""

    def __init__(self, db_path: Path | None = None, transcripts_dir: Path | None = None):
        """Initialize the knowledge graph.

        Args:
            db_path: Path to SQLite database file. Defaults to
                ``transcripts_dir / knowledge.db``.
            transcripts_dir: Directory containing transcript .md files.
                Defaults to ``~/HiDock/Raw Transcripts/``.
        """
        if transcripts_dir is None:
            transcripts_dir = Path.home() / "HiDock" / "Raw Transcripts"
        self.transcripts_dir = transcripts_dir

        if db_path is None:
            db_path = transcripts_dir / _DEFAULT_DB_NAME
        self.db_path = db_path

        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._ensure_schema()
        return self._conn

    def _ensure_schema(self) -> None:
        conn = self._conn
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS meetings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                doc_type TEXT NOT NULL DEFAULT 'meeting',
                date TEXT,
                duration REAL,
                source_device TEXT DEFAULT '',
                source_file TEXT DEFAULT '',
                model TEXT DEFAULT '',
                summary_text TEXT DEFAULT '',
                indexed_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS people (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            );

            CREATE TABLE IF NOT EXISTS meeting_people (
                meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
                PRIMARY KEY (meeting_id, person_id)
            );

            CREATE TABLE IF NOT EXISTS action_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                task TEXT NOT NULL,
                assignee TEXT DEFAULT '',
                due TEXT DEFAULT '',
                status TEXT DEFAULT 'open',
                confidence TEXT DEFAULT 'medium'
            );

            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                text TEXT NOT NULL,
                topic TEXT DEFAULT '',
                confidence TEXT DEFAULT 'medium'
            );

            CREATE TABLE IF NOT EXISTS key_points (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                text TEXT NOT NULL,
                confidence TEXT DEFAULT 'medium'
            );

            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meeting_id INTEGER NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
                tag TEXT NOT NULL
            );

            -- Full-text search index over transcript content
            CREATE VIRTUAL TABLE IF NOT EXISTS transcript_fts USING fts5(
                file_path,
                title,
                content,
                speakers,
                tokenize='porter unicode61'
            );
        """)
        conn.commit()

        # ── Schema migrations ─────────────────────────────────────────────
        # Add confidence columns if missing (added in v0.2.0)
        for table in ("action_items", "decisions", "key_points"):
            try:
                conn.execute(f"SELECT confidence FROM {table} LIMIT 0")
            except sqlite3.OperationalError:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN confidence TEXT DEFAULT 'medium'")
                conn.commit()

        # Add event_log table (added in v0.2.0)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS event_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                file_path TEXT DEFAULT '',
                status TEXT DEFAULT 'ok',
                duration_s REAL,
                error TEXT DEFAULT '',
                metadata_json TEXT DEFAULT '{}'
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_event_log_type
            ON event_log (event_type)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_event_log_timestamp
            ON event_log (timestamp DESC)
        """)
        conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ── Indexing ────────────────────────────────────────────────────────────

    def index_transcript(self, file_path: Path) -> int | None:
        """Index a single transcript file.

        Parses YAML frontmatter and body, stores in the knowledge graph.
        If the file was previously indexed, it is re-indexed (updated).

        Args:
            file_path: Path to a .md transcript file.

        Returns:
            The meeting ID, or None if the file couldn't be parsed.
        """
        conn = self._get_conn()
        try:
            meeting_id = self._index_transcript_no_commit(file_path)
            conn.commit()
            return meeting_id
        except BaseException:
            conn.rollback()
            raise

    def _index_transcript_no_commit(self, file_path: Path) -> int | None:
        """Index a single transcript without committing — used directly by
        rebuild() so the whole rebuild is one transaction (rollback on
        failure leaves the previous index intact)."""
        file_path = file_path.resolve()
        try:
            text = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

        meta, body = parse_frontmatter(text)

        conn = self._get_conn()

        # Remove old entry if exists
        old = conn.execute(
            "SELECT id FROM meetings WHERE file_path = ?", (str(file_path),)
        ).fetchone()
        if old:
            self._delete_meeting(old["id"])

        # Insert meeting
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            """INSERT INTO meetings
               (file_path, title, doc_type, date, duration, source_device,
                source_file, model, summary_text, indexed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(file_path),
                meta.get("title", file_path.stem),
                meta.get("type", "meeting"),
                meta.get("date", ""),
                meta.get("duration"),
                meta.get("source_device", ""),
                meta.get("source_file", ""),
                meta.get("model", ""),
                meta.get("summary_text", ""),
                now,
            ),
        )
        meeting_id = cur.lastrowid

        # Index speakers/people
        speakers = meta.get("speakers", [])
        if isinstance(speakers, list):
            for name in speakers:
                if name and isinstance(name, str):
                    person_id = self._get_or_create_person(name)
                    conn.execute(
                        "INSERT OR IGNORE INTO meeting_people (meeting_id, person_id) VALUES (?, ?)",
                        (meeting_id, person_id),
                    )

        # Index action items (and their assignees as people)
        for item in meta.get("action_items", []):
            if isinstance(item, dict) and item.get("task"):
                conn.execute(
                    "INSERT INTO action_items (meeting_id, task, assignee, due, status, confidence) VALUES (?, ?, ?, ?, ?, ?)",
                    (meeting_id, item["task"], item.get("assignee", ""),
                     item.get("due", ""), item.get("status", "open"),
                     item.get("confidence", "medium")),
                )
                # Also index assignee as a person linked to this meeting
                assignee = item.get("assignee", "").strip()
                if assignee:
                    person_id = self._get_or_create_person(assignee)
                    conn.execute(
                        "INSERT OR IGNORE INTO meeting_people (meeting_id, person_id) VALUES (?, ?)",
                        (meeting_id, person_id),
                    )

        # Index decisions
        for item in meta.get("decisions", []):
            if isinstance(item, dict) and item.get("text"):
                conn.execute(
                    "INSERT INTO decisions (meeting_id, text, topic, confidence) VALUES (?, ?, ?, ?)",
                    (meeting_id, item["text"], item.get("topic", ""),
                     item.get("confidence", "medium")),
                )

        # Index key points — accept both strings and dicts
        for item in meta.get("key_points", []):
            if isinstance(item, dict) and item.get("text"):
                conn.execute(
                    "INSERT INTO key_points (meeting_id, text, confidence) VALUES (?, ?, ?)",
                    (meeting_id, item["text"].strip(), item.get("confidence", "medium")),
                )
            elif isinstance(item, str) and item.strip():
                conn.execute(
                    "INSERT INTO key_points (meeting_id, text, confidence) VALUES (?, ?, ?)",
                    (meeting_id, item.strip(), "medium"),
                )

        # Index tags
        for tag in meta.get("tags", []):
            if isinstance(tag, str) and tag.strip():
                conn.execute(
                    "INSERT INTO tags (meeting_id, tag) VALUES (?, ?)",
                    (meeting_id, tag.strip()),
                )

        # Full-text search entry
        speakers_text = (
            ", ".join(s for s in speakers if isinstance(s, str))
            if isinstance(speakers, list) else ""
        )
        conn.execute(
            "INSERT INTO transcript_fts (file_path, title, content, speakers) VALUES (?, ?, ?, ?)",
            (str(file_path), meta.get("title", ""), body, speakers_text),
        )

        return meeting_id

    def rebuild(self) -> int:
        """Rebuild the entire knowledge graph from transcript files.

        Scans ``transcripts_dir`` for .md files, parses each, and rebuilds
        the full index. This is idempotent and safe to run at any time.

        The whole rebuild (clear + re-index) runs in a single transaction:
        if anything fails mid-way it is rolled back, so a failed rebuild
        leaves the previous index intact instead of a wiped/partial one.

        Returns:
            Number of transcripts indexed.
        """
        conn = self._get_conn()

        try:
            # Clear all data — plain execute()s (not executescript, which
            # would force an implicit commit) so the deletes stay inside
            # the same transaction as the re-indexing below.
            for table in (
                "meeting_people", "action_items", "decisions",
                "key_points", "tags", "meetings", "transcript_fts",
            ):
                conn.execute(f"DELETE FROM {table}")  # noqa: S608 — fixed table names

            count = 0
            if self.transcripts_dir.exists():
                for md_file in sorted(self.transcripts_dir.glob("*.md")):
                    if self._index_transcript_no_commit(md_file) is not None:
                        count += 1
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

        return count

    def _delete_meeting(self, meeting_id: int) -> None:
        conn = self._get_conn()
        # Get file_path for FTS cleanup
        row = conn.execute(
            "SELECT file_path FROM meetings WHERE id = ?", (meeting_id,)
        ).fetchone()
        if row:
            # FTS5 tables require DELETE by rowid, not by column value
            fts_row = conn.execute(
                "SELECT rowid FROM transcript_fts WHERE file_path = ?", (row["file_path"],)
            ).fetchone()
            if fts_row:
                conn.execute("DELETE FROM transcript_fts WHERE rowid = ?", (fts_row["rowid"],))
        conn.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))

    def _get_or_create_person(self, name: str) -> int:
        conn = self._get_conn()
        row = conn.execute("SELECT id FROM people WHERE name = ?", (name,)).fetchone()
        if row:
            return row["id"]
        cur = conn.execute("INSERT INTO people (name) VALUES (?)", (name,))
        return cur.lastrowid

    # ── Search ──────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict]:
        """Full-text search across all transcripts.

        Args:
            query: Search query (supports FTS5 syntax: AND, OR, NOT, phrases).
            limit: Maximum results to return.

        Returns:
            List of dicts with: file_path, title, date, snippet.
        """
        conn = self._get_conn()
        # Wrap query in quotes to prevent FTS5 operator injection
        # (user searching for "NOT" or "OR" would be misinterpreted)
        safe_query = '"' + query.replace('"', '""') + '"'
        try:
            rows = conn.execute(
                """SELECT f.file_path, f.title,
                          snippet(transcript_fts, 2, '<b>', '</b>', '...', 40) as snippet,
                          m.date
                   FROM transcript_fts f
                   JOIN meetings m ON m.file_path = f.file_path
                   WHERE transcript_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (safe_query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return []

        return [
            {
                "file_path": r["file_path"],
                "title": r["title"],
                "date": r["date"],
                "snippet": r["snippet"],
            }
            for r in rows
        ]

    def search_by_person(self, name: str, limit: int = 20) -> list[dict]:
        """Find all meetings involving a specific person.

        Args:
            name: Person name (case-insensitive partial match).
            limit: Maximum results.

        Returns:
            List of meeting dicts.
        """
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT m.id, m.file_path, m.title, m.date, m.duration
               FROM meetings m
               JOIN meeting_people mp ON mp.meeting_id = m.id
               JOIN people p ON p.id = mp.person_id
               WHERE p.name LIKE ?
               ORDER BY m.date DESC
               LIMIT ?""",
            (f"%{name}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def search_by_tag(self, tag: str, limit: int = 20) -> list[dict]:
        """Find all meetings with a specific tag."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT m.id, m.file_path, m.title, m.date, m.duration
               FROM meetings m
               JOIN tags t ON t.meeting_id = m.id
               WHERE t.tag = ?
               ORDER BY m.date DESC
               LIMIT ?""",
            (tag.lower(), limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── People ──────────────────────────────────────────────────────────────

    def get_person_profile(self, name: str) -> dict | None:
        """Get a profile for a person across all meetings.

        Returns:
            Dict with: name, meeting_count, last_meeting_date, meetings,
            open_action_items, topics. Or None if person not found.
        """
        conn = self._get_conn()
        person = conn.execute(
            "SELECT id, name FROM people WHERE name LIKE ?", (f"%{name}%",)
        ).fetchone()
        if not person:
            return None

        person_id = person["id"]

        # Meetings
        meetings = conn.execute(
            """SELECT m.title, m.date, m.file_path
               FROM meetings m
               JOIN meeting_people mp ON mp.meeting_id = m.id
               WHERE mp.person_id = ?
               ORDER BY m.date DESC""",
            (person_id,),
        ).fetchall()

        # Open action items assigned to this person
        actions = conn.execute(
            """SELECT a.task, a.due, a.status, m.title as meeting_title, m.date as meeting_date
               FROM action_items a
               JOIN meetings m ON m.id = a.meeting_id
               WHERE a.assignee LIKE ? AND a.status = 'open'
               ORDER BY a.due, m.date DESC""",
            (f"%{name}%",),
        ).fetchall()

        # Topics (from tags of their meetings)
        topics = conn.execute(
            """SELECT t.tag, COUNT(*) as count
               FROM tags t
               JOIN meeting_people mp ON mp.meeting_id = t.meeting_id
               WHERE mp.person_id = ?
               GROUP BY t.tag
               ORDER BY count DESC
               LIMIT 10""",
            (person_id,),
        ).fetchall()

        return {
            "name": person["name"],
            "meeting_count": len(meetings),
            "last_meeting_date": meetings[0]["date"] if meetings else None,
            "meetings": [dict(m) for m in meetings],
            "open_action_items": [dict(a) for a in actions],
            "topics": [{"tag": t["tag"], "count": t["count"]} for t in topics],
        }

    def list_people(self) -> list[dict]:
        """List all people with meeting counts."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT p.name, COUNT(mp.meeting_id) as meeting_count,
                      MAX(m.date) as last_meeting
               FROM people p
               JOIN meeting_people mp ON mp.person_id = p.id
               JOIN meetings m ON m.id = mp.meeting_id
               GROUP BY p.id
               ORDER BY last_meeting DESC""",
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Action Items ────────────────────────────────────────────────────────

    def list_action_items(
        self,
        status: str = "open",
        assignee: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """List action items across all meetings.

        Args:
            status: Filter by status ("open", "completed", or "all").
            assignee: Filter by assignee name (partial match).
            limit: Maximum results.

        Returns:
            List of action item dicts with meeting context.
        """
        conn = self._get_conn()
        query = """
            SELECT a.id, a.task, a.assignee, a.due, a.status, a.confidence,
                   m.title as meeting_title, m.date as meeting_date, m.file_path
            FROM action_items a
            JOIN meetings m ON m.id = a.meeting_id
        """
        conditions = []
        params: list[Any] = []

        if status != "all":
            conditions.append("a.status = ?")
            params.append(status)
        if assignee:
            conditions.append("a.assignee LIKE ?")
            params.append(f"%{assignee}%")

        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY a.due, m.date DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def update_action_item_status(self, action_id: int, status: str) -> bool:
        """Update an action item's status.

        Note: This updates the SQLite cache only. To persist the change,
        the caller should also update the source markdown frontmatter.

        Args:
            action_id: Action item ID.
            status: New status ("open", "completed", "cancelled").

        Returns:
            True if updated, False if not found.
        """
        conn = self._get_conn()
        cur = conn.execute(
            "UPDATE action_items SET status = ? WHERE id = ?", (status, action_id)
        )
        conn.commit()
        return cur.rowcount > 0

    # ── Stats ───────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Get summary statistics about the knowledge graph."""
        conn = self._get_conn()
        meetings = conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
        people = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
        action_items = conn.execute("SELECT COUNT(*) FROM action_items").fetchone()[0]
        open_items = conn.execute(
            "SELECT COUNT(*) FROM action_items WHERE status = 'open'"
        ).fetchone()[0]
        decisions = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        return {
            "meetings": meetings,
            "people": people,
            "action_items": action_items,
            "open_action_items": open_items,
            "decisions": decisions,
        }


# ── CLI ─────────────────────────────────────────────────────────────────────


def _cli():
    """Command-line interface for the knowledge graph."""
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Knowledge Graph CLI")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("rebuild", help="Rebuild index from transcript files")
    sub.add_parser("stats", help="Show index statistics")

    p_search = sub.add_parser("search", help="Full-text search")
    p_search.add_argument("query", help="Search query")

    p_person = sub.add_parser("person", help="Get person profile")
    p_person.add_argument("name", help="Person name")

    p_actions = sub.add_parser("actions", help="List action items")
    p_actions.add_argument("--status", default="open", help="Filter: open, completed, all")
    p_actions.add_argument("--assignee", default=None, help="Filter by assignee")

    sub.add_parser("people", help="List all people")

    args = parser.parse_args()

    kg = KnowledgeGraph()

    if args.command == "rebuild":
        count = kg.rebuild()
        print(json.dumps({"rebuilt": True, "transcripts_indexed": count}))
    elif args.command == "stats":
        print(json.dumps(kg.get_stats(), indent=2))
    elif args.command == "search":
        results = kg.search(args.query)
        print(json.dumps(results, indent=2))
    elif args.command == "person":
        profile = kg.get_person_profile(args.name)
        print(json.dumps(profile, indent=2))
    elif args.command == "actions":
        items = kg.list_action_items(status=args.status, assignee=args.assignee)
        print(json.dumps(items, indent=2))
    elif args.command == "people":
        people = kg.list_people()
        print(json.dumps(people, indent=2))
    else:
        parser.print_help()

    kg.close()


if __name__ == "__main__":
    _cli()
