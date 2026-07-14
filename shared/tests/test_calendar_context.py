import json
from datetime import datetime, timezone

from shared.calendar_context import (
    CalendarAttendee,
    candidate_names_for_voice_library,
    load_context,
    load_context_for_audio,
    parse_context_payload,
)


def event(event_id="1", subject="Planning", start="2026-07-14T10:00:00+01:00", end="2026-07-14T11:00:00+01:00", attendees=()):
    return {"id": event_id, "subject": subject, "start": {"dateTime": start}, "end": {"dateTime": end}, "attendees": list(attendees)}


def test_timezone_aware_timestamps_are_normalized_to_utc():
    parsed = parse_context_payload([event(start="2026-07-14T10:00:00+01:00", end="2026-07-14T11:00:00+01:00")])[0]
    assert parsed.start == datetime(2026, 7, 14, 9, tzinfo=timezone.utc)
    assert parsed.end.tzinfo == timezone.utc


def test_content_text_mcp_wrapper_is_decoded_recursively():
    payload = {"result": {"content": [{"type": "text", "text": json.dumps({"value": [event("wrapped")]})}]}}
    parsed = parse_context_payload(payload)
    assert len(parsed) == 1
    assert parsed[0].id == "wrapped"


def test_attendee_normalization_and_decline_filtering():
    attendees = [
        {"emailAddress": {"name": "  Alice   Smith ", "address": "ALICE@EXAMPLE.COM"}, "status": "accepted"},
        {"displayName": "Declined Person", "email": "no@example.com", "response": "declined"},
    ]
    context = load_context([event(attendees=attendees)], "2026-07-14T09:30:00Z", "2026-07-14T09:45:00Z")
    assert context.candidate_names == frozenset({"Alice Smith"})
    assert CalendarAttendee("x", "x@example.com", "DECLINED").declined


def test_graph_response_status_declined_attendee_is_ignored():
    context = load_context([event(attendees=[{
        "emailAddress": {"name": "Declined Graph Attendee", "address": "declined@example.com"},
        "responseStatus": {"response": "declined"},
    }])], "2026-07-14T09:30:00Z", "2026-07-14T09:45:00Z")
    assert context.candidate_names == frozenset()


def test_one_matching_event_selects_candidates():
    context = load_context([event("meeting", "Team sync", attendees=[{"name": "James Whiting", "email": "james@example.com"}])],
                           "2026-07-14T09:20:00Z", "2026-07-14T09:40:00Z")
    assert context.selected_event_id == "meeting"
    assert context.selected_event_title == "Team sync"
    assert not context.ambiguous


def test_equal_overlapping_events_are_ambiguous():
    payload = [event("a", start="2026-07-14T10:00:00Z", end="2026-07-14T11:00:00Z"),
               event("b", start="2026-07-14T10:00:00Z", end="2026-07-14T11:00:00Z")]
    context = load_context(payload, "2026-07-14T10:15:00Z", "2026-07-14T10:45:00Z")
    assert context.ambiguous
    assert context.selected_event_id is None


def test_no_match_and_voice_library_aliases_and_emails():
    context = load_context([event(attendees=[{"name": "Liz Jones", "email": "liz@calendar.test"}])],
                           "2026-07-14T13:00:00Z", "2026-07-14T13:10:00Z")
    assert context.selected_event_id is None
    assert context.candidate_names == frozenset()
    context = load_context([event(attendees=[{"name": "Elizabeth Jones", "email": "liz@calendar.test"}])],
                           "2026-07-14T09:15:00Z", "2026-07-14T09:30:00Z")
    library = {"speakers": {"Elizabeth Jones": {"aliases": ["Liz Jones"], "calendar_emails": ["LIZ@CALENDAR.TEST"]}}}
    assert candidate_names_for_voice_library(context, library) == frozenset({"Elizabeth Jones"})


def test_load_context_for_audio_discovers_sidecar_and_matches_voice_library(tmp_path, monkeypatch):
    audio_path = tmp_path / "meeting.m4a"
    sidecar = tmp_path / "meeting_calendar.json"
    sidecar.write_text(json.dumps({
        "source": "test-calendar",
        "recording_start": "2026-07-14T09:15:00Z",
        "recording_end": "2026-07-14T09:30:00Z",
        "events": [event(attendees=[{"name": "Liz Jones", "email": "liz@example.com"}])],
    }))
    monkeypatch.setattr("shared.voice_library_lite.load_library", lambda: {
        "speakers": {"Elizabeth Jones": {"aliases": ["Liz Jones"], "calendar_emails": []}}
    })
    context = load_context_for_audio(audio_path)
    assert context.source == "test-calendar"
    assert context.candidate_names == frozenset({"Elizabeth Jones"})
    assert context.to_metadata()["calendar_event_title"] == "Planning"
    assert "Planning" in context.summary()


def test_load_context_for_audio_uses_environment_and_missing_is_optional(tmp_path, monkeypatch):
    audio_path = tmp_path / "recording.wav"
    assert load_context_for_audio(audio_path) is None
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps({"events": [event()]}))
    monkeypatch.setenv("HIDOCK_CALENDAR_CONTEXT", str(context_path))
    context = load_context_for_audio(audio_path)
    assert context is not None
    assert context.selected_event_id is None
    assert context.reason == "recording window not supplied"
