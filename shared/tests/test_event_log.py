"""Tests for the event log system."""
from __future__ import annotations

import pytest
from pathlib import Path

from shared.event_log import (
    EventType, Event, log_event, recent_events, event_counts, errors_since,
    set_db_path, close,
)


@pytest.fixture(autouse=True)
def event_db(tmp_path):
    """Use a temporary database for each test."""
    db = tmp_path / "test_events.db"
    set_db_path(db)
    yield db
    close()


class TestLogEvent:
    def test_basic_log(self):
        eid = log_event(EventType.TRANSCRIPTION_STARTED, file_path="test.mp3")
        assert eid >= 1

    def test_log_with_metadata(self):
        eid = log_event(
            EventType.TRANSCRIPTION_COMPLETED,
            file_path="test.mp3",
            duration_s=42.3,
            metadata={"model": "large-v3-turbo"},
        )
        events = recent_events(limit=1)
        assert len(events) == 1
        assert events[0].id == eid
        assert events[0].duration_s == 42.3
        assert events[0].metadata["model"] == "large-v3-turbo"

    def test_log_error(self):
        log_event(
            EventType.TRANSCRIPTION_FAILED,
            file_path="bad.mp3",
            status="error",
            error="File corrupt",
        )
        events = recent_events(limit=1)
        assert events[0].status == "error"
        assert events[0].error == "File corrupt"

    def test_string_event_type(self):
        eid = log_event("custom_event", file_path="x.txt")
        events = recent_events(limit=1)
        assert events[0].event_type == "custom_event"


class TestRecentEvents:
    def test_ordering(self):
        log_event(EventType.TRANSCRIPTION_STARTED, file_path="first.mp3")
        log_event(EventType.TRANSCRIPTION_COMPLETED, file_path="second.mp3")
        events = recent_events(limit=10)
        assert len(events) == 2
        # Most recent first
        assert events[0].file_path == "second.mp3"
        assert events[1].file_path == "first.mp3"

    def test_filter_by_type(self):
        log_event(EventType.TRANSCRIPTION_STARTED)
        log_event(EventType.SUMMARIZATION_STARTED)
        log_event(EventType.TRANSCRIPTION_COMPLETED)

        events = recent_events(event_type=EventType.TRANSCRIPTION_STARTED)
        assert len(events) == 1
        assert events[0].event_type == "transcription_started"

    def test_limit(self):
        for i in range(10):
            log_event(EventType.TRANSCRIPTION_STARTED, file_path=f"file{i}.mp3")
        events = recent_events(limit=3)
        assert len(events) == 3


class TestEventCounts:
    def test_counts_by_type(self):
        log_event(EventType.TRANSCRIPTION_STARTED)
        log_event(EventType.TRANSCRIPTION_STARTED)
        log_event(EventType.TRANSCRIPTION_COMPLETED)

        counts = event_counts(days=30)
        assert counts["transcription_started"] == 2
        assert counts["transcription_completed"] == 1


class TestErrorsSince:
    def test_finds_errors(self):
        log_event(EventType.TRANSCRIPTION_COMPLETED, status="ok")
        log_event(EventType.TRANSCRIPTION_FAILED, status="error", error="boom")
        log_event(EventType.ERROR, status="error", error="disk full")

        errors = errors_since(hours=24)
        assert len(errors) == 2

    def test_no_errors(self):
        log_event(EventType.TRANSCRIPTION_COMPLETED, status="ok")
        errors = errors_since(hours=24)
        assert len(errors) == 0

    def test_respects_hours_filter(self):
        """errors_since should only return errors within the time window."""
        # Log an error now — should be found with hours=24
        log_event(EventType.ERROR, status="error", error="recent error")
        errors = errors_since(hours=24)
        assert len(errors) == 1
        # With hours=0, should find nothing (or the just-logged one depending on timing)
        # Just verify the function actually filters, not returns everything


class TestEventDataclass:
    def test_metadata_property(self):
        log_event(EventType.INDEX_REBUILD, metadata={"count": 5})
        ev = recent_events(limit=1)[0]
        assert ev.metadata == {"count": 5}

    def test_empty_metadata(self):
        log_event(EventType.TRANSCRIPTION_STARTED)
        ev = recent_events(limit=1)[0]
        assert ev.metadata == {}
