"""Structured event log — records system events for diagnostics and audit.

All significant system events (transcription, summarization, index operations,
hook execution, errors) are recorded in an SQLite table alongside the
knowledge graph. This enables:
- Post-mortem debugging of failures
- Usage analytics (transcriptions per day, avg duration, etc.)
- Health check diagnostics (stale operations, repeated errors)

Usage:
    from shared.event_log import log_event, EventType, recent_events

    log_event(EventType.TRANSCRIPTION_STARTED, file_path="recording.mp3")
    log_event(EventType.TRANSCRIPTION_COMPLETED, file_path="recording.mp3",
              duration_s=42.3, metadata={"model": "large-v3-turbo"})
    log_event(EventType.ERROR, error="Disk full", metadata={"component": "transcribe"})

    for ev in recent_events(limit=20):
        print(ev)
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


class EventType(str, Enum):
    """Known event types."""

    TRANSCRIPTION_STARTED = "transcription_started"
    TRANSCRIPTION_COMPLETED = "transcription_completed"
    TRANSCRIPTION_FAILED = "transcription_failed"
    SUMMARIZATION_STARTED = "summarization_started"
    SUMMARIZATION_COMPLETED = "summarization_completed"
    SUMMARIZATION_FAILED = "summarization_failed"
    WHISPER_GUARD_FILTERED = "whisper_guard_filtered"
    INDEX_REBUILD = "index_rebuild"
    HOOK_EXECUTED = "hook_executed"
    HOOK_FAILED = "hook_failed"
    HEALTH_CHECK = "health_check"
    ERROR = "error"


@dataclass
class Event:
    """A single logged event."""

    id: int
    timestamp: str
    event_type: str
    file_path: str
    status: str
    duration_s: float | None
    error: str
    metadata_json: str

    @property
    def metadata(self) -> dict:
        if self.metadata_json:
            try:
                return json.loads(self.metadata_json)
            except (json.JSONDecodeError, TypeError):
                pass
        return {}


# ── Database ──────────────────────────────────────────────────────────────────

_DEFAULT_DB_NAME = "knowledge.db"

_conn_cache: sqlite3.Connection | None = None


def _get_conn(db_path: Path | None = None) -> sqlite3.Connection:
    """Get or create a connection to the event log database."""
    global _conn_cache
    if _conn_cache is not None:
        return _conn_cache

    if db_path is None:
        db_path = Path.home() / "HiDock" / "Raw Transcripts" / _DEFAULT_DB_NAME

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_schema(conn)
    _conn_cache = conn
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
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


def set_db_path(db_path: Path) -> None:
    """Override the database path (useful for testing)."""
    global _conn_cache
    if _conn_cache is not None:
        _conn_cache.close()
        _conn_cache = None
    _get_conn(db_path)


def close() -> None:
    """Close the cached connection."""
    global _conn_cache
    if _conn_cache is not None:
        _conn_cache.close()
        _conn_cache = None


# ── Logging ───────────────────────────────────────────────────────────────────


def log_event(
    event_type: EventType | str,
    *,
    file_path: str = "",
    status: str = "ok",
    duration_s: float | None = None,
    error: str = "",
    metadata: dict | None = None,
    db_path: Path | None = None,
) -> int:
    """Record an event in the log.

    Args:
        event_type: Type of event (use EventType enum or string).
        file_path: Related file path (audio or transcript).
        status: "ok", "error", "warning", etc.
        duration_s: How long the operation took (seconds).
        error: Error message if applicable.
        metadata: Extra structured data (stored as JSON).
        db_path: Override database path.

    Returns:
        The event row ID.
    """
    conn = _get_conn(db_path)
    now = datetime.now(timezone.utc).isoformat()
    event_str = event_type.value if isinstance(event_type, EventType) else str(event_type)
    meta_json = json.dumps(metadata or {}, ensure_ascii=False)

    cur = conn.execute(
        """INSERT INTO event_log
           (timestamp, event_type, file_path, status, duration_s, error, metadata_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (now, event_str, str(file_path), status, duration_s, error, meta_json),
    )
    conn.commit()
    return cur.lastrowid


# ── Queries ───────────────────────────────────────────────────────────────────


def recent_events(
    limit: int = 50,
    event_type: EventType | str | None = None,
    db_path: Path | None = None,
) -> list[Event]:
    """Retrieve recent events from the log.

    Args:
        limit: Maximum events to return.
        event_type: Filter by event type (optional).
        db_path: Override database path.

    Returns:
        List of Event objects, most recent first.
    """
    conn = _get_conn(db_path)
    if event_type:
        et = event_type.value if isinstance(event_type, EventType) else str(event_type)
        rows = conn.execute(
            "SELECT * FROM event_log WHERE event_type = ? ORDER BY timestamp DESC LIMIT ?",
            (et, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM event_log ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()

    return [Event(**dict(r)) for r in rows]


def event_counts(days: int = 30, db_path: Path | None = None) -> dict[str, int]:
    """Count events by type over the last N days.

    Returns:
        Dict of event_type -> count.
    """
    conn = _get_conn(db_path)
    cutoff = datetime.now(timezone.utc).isoformat()[:10]  # approximate
    rows = conn.execute(
        """SELECT event_type, COUNT(*) as cnt
           FROM event_log
           WHERE timestamp >= date(?, '-' || ? || ' days')
           GROUP BY event_type
           ORDER BY cnt DESC""",
        (cutoff, days),
    ).fetchall()
    return {r["event_type"]: r["cnt"] for r in rows}


def errors_since(hours: int = 24, db_path: Path | None = None) -> list[Event]:
    """Get error events from the last N hours."""
    conn = _get_conn(db_path)
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    rows = conn.execute(
        """SELECT * FROM event_log
           WHERE status = 'error' AND timestamp >= ?
           ORDER BY timestamp DESC
           LIMIT 100""",
        (cutoff,),
    ).fetchall()
    return [Event(**dict(r)) for r in rows]


# ── CLI ───────────────────────────────────────────────────────────────────────


def _cli() -> None:
    """Simple CLI for viewing the event log."""
    import argparse

    parser = argparse.ArgumentParser(description="HiDock Event Log")
    sub = parser.add_subparsers(dest="command")

    p_recent = sub.add_parser("recent", help="Show recent events")
    p_recent.add_argument("-n", "--limit", type=int, default=20)
    p_recent.add_argument("-t", "--type", help="Filter by event type")

    sub.add_parser("counts", help="Event counts by type")
    sub.add_parser("errors", help="Recent errors")

    args = parser.parse_args()

    if args.command == "recent":
        et = args.type if hasattr(args, "type") else None
        events = recent_events(limit=args.limit, event_type=et)
        for ev in events:
            err = f" ERROR: {ev.error}" if ev.error else ""
            dur = f" ({ev.duration_s:.1f}s)" if ev.duration_s else ""
            print(f"[{ev.timestamp}] {ev.event_type} {ev.file_path}{dur}{err}")

    elif args.command == "counts":
        for et, cnt in event_counts().items():
            print(f"  {et}: {cnt}")

    elif args.command == "errors":
        for ev in errors_since():
            print(f"[{ev.timestamp}] {ev.error} — {ev.file_path}")
            if ev.metadata:
                print(f"  metadata: {ev.metadata}")

    else:
        parser.print_help()


if __name__ == "__main__":
    _cli()
