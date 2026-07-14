"""Provider-neutral calendar context for recording speaker matching.

The public boundary deliberately accepts ordinary JSON-shaped Microsoft 365
MCP responses, while keeping the matching logic independent of that provider.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


DEFAULT_PADDING = timedelta(minutes=15)
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class CalendarAttendee:
    name: str = ""
    email: str = ""
    response: str = ""

    @property
    def declined(self) -> bool:
        return self.response.casefold() in {"declined", "decline", "no"}


@dataclass(frozen=True)
class CalendarEvent:
    id: str
    title: str
    start: datetime
    end: datetime
    attendees: tuple[CalendarAttendee, ...] = ()


@dataclass(frozen=True)
class CalendarContext:
    candidate_names: frozenset[str] = field(default_factory=frozenset)
    selected_event_id: str | None = None
    selected_event_title: str | None = None
    ambiguous: bool = False
    source: str = "calendar"
    reason: str = ""
    events: tuple[CalendarEvent, ...] = ()

    def summary(self) -> str:
        """Return a short deterministic human-readable description."""
        if self.ambiguous:
            return "Ambiguous calendar context"
        if self.selected_event_id is None:
            return "No matching calendar event"
        title = self.selected_event_title or "Untitled event"
        names = ", ".join(sorted(self.candidate_names, key=normalize_name)) or "no matched attendees"
        return f"{title}: {names}"

    def to_metadata(self) -> dict[str, Any]:
        """Return JSON-serializable calendar metadata for transcript sidecars."""
        return {
            "calendar_event_id": self.selected_event_id,
            "calendar_event_title": self.selected_event_title,
            "calendar_candidate_names": sorted(self.candidate_names, key=normalize_name),
            "calendar_ambiguous": self.ambiguous,
            "calendar_source": self.source,
            "calendar_reason": self.reason,
        }


def normalize_name(value: Any) -> str:
    """Collapse whitespace and case-fold a name for exact comparisons."""
    return _WHITESPACE.sub(" ", str(value or "").strip()).casefold()


def normalize_email(value: Any) -> str:
    return str(value or "").strip().casefold()


def _timestamp(value: Any, timezone_name: str | None = None) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        if timezone_name:
            try:
                parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
            except (KeyError, ValueError):
                parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _value_and_timezone(value: Any) -> tuple[Any, str | None]:
    if isinstance(value, dict):
        return value.get("dateTime", value.get("value")), value.get("timeZone")
    return value, None


def _attendee(raw: Any) -> CalendarAttendee | None:
    if not isinstance(raw, dict):
        return None
    address = raw.get("emailAddress") if isinstance(raw.get("emailAddress"), dict) else {}
    name = raw.get("displayName") or raw.get("name") or address.get("name") or ""
    email = raw.get("address") or raw.get("email") or address.get("address") or ""
    status = (raw.get("response") or raw.get("status") or raw.get("responseStatus")
              or raw.get("participationStatus"))
    if isinstance(status, dict):
        status = status.get("response") or status.get("status") or ""
    result = CalendarAttendee(_WHITESPACE.sub(" ", str(name).strip()), normalize_email(email), str(status or "").strip())
    return result if result.name or result.email else None


def _event(raw: Any, index: int) -> CalendarEvent | None:
    if not isinstance(raw, dict):
        return None
    start_value, start_tz = _value_and_timezone(raw.get("start"))
    end_value, end_tz = _value_and_timezone(raw.get("end"))
    if not start_value or not end_value:
        return None
    try:
        start, end = _timestamp(start_value, start_tz), _timestamp(end_value, end_tz)
    except (TypeError, ValueError, OverflowError):
        return None
    if end <= start:
        return None
    attendees = tuple(a for a in (_attendee(item) for item in raw.get("attendees", [])) if a)
    return CalendarEvent(str(raw.get("id") or raw.get("iCalUId") or index),
                         str(raw.get("subject") or raw.get("title") or "").strip(),
                         start, end, attendees)


def _event_items(payload: Any) -> list[Any]:
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith(("{", "[")):
            try:
                return _event_items(json.loads(text))
            except json.JSONDecodeError:
                return []
        return []
    if isinstance(payload, list):
        expanded = []
        for item in payload:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                expanded.extend(_event_items(item["text"]))
            else:
                expanded.append(item)
        return expanded
    if not isinstance(payload, dict):
        return []
    if payload.get("type") == "text" and isinstance(payload.get("text"), str):
        return _event_items(payload["text"])
    for key in ("value", "events", "items", "results"):
        if isinstance(payload.get(key), list):
            return payload[key]
    # Some MCP wrappers put the provider result under content/data.
    for key in ("data", "content", "result"):
        if isinstance(payload.get(key), (dict, list, str)):
            items = _event_items(payload[key])
            if items:
                return items
    return []


def parse_context_payload(payload: Any) -> tuple[CalendarEvent, ...]:
    """Parse a JSON object/list or a path to one into normalized events."""
    if isinstance(payload, (str, Path)):
        if isinstance(payload, str) and payload.lstrip().startswith(("{", "[")):
            payload = json.loads(payload)
        else:
            payload = json.loads(Path(payload).read_text(encoding="utf-8"))
    events = tuple(event for i, raw in enumerate(_event_items(payload)) if (event := _event(raw, i)))
    return tuple(sorted(events, key=lambda item: (item.start, item.end, item.id)))


def _overlap(event: CalendarEvent, start: datetime, end: datetime) -> float:
    return max(0.0, (min(event.end, end) - max(event.start, start)).total_seconds())


def load_context(payload_or_path: Any, recording_start: Any = None,
                 recording_end: Any = None, padding: timedelta | int | float = DEFAULT_PADDING,
                 source: str | None = None) -> CalendarContext:
    """Load calendar events and select the best event around a recording.

    ``padding`` is a timedelta or number of minutes. Without a recording
    window, all parsed events are returned but no event is selected.
    """
    payload = payload_or_path
    payload_source = None
    if isinstance(payload, dict):
        payload_source = payload.get("source")
    events = parse_context_payload(payload)
    context_source = source or payload_source or "calendar"
    if recording_start is None or recording_end is None:
        return CalendarContext(events=events, source=context_source, reason="recording window not supplied")
    start = _timestamp(recording_start)
    end = _timestamp(recording_end)
    pad = padding if isinstance(padding, timedelta) else timedelta(minutes=float(padding))
    window_start, window_end = start - pad, end + pad
    matches = [(event, _overlap(event, window_start, window_end)) for event in events
               if _overlap(event, window_start, window_end) > 0]
    if not matches:
        return CalendarContext(events=events, source=context_source, reason="no event overlaps recording window")
    best_score = max(score for _, score in matches)
    best = [event for event, score in matches if score == best_score]
    if len(best) != 1:
        return CalendarContext(ambiguous=True, events=events, source=context_source,
                               reason="multiple events are equally plausible")
    selected = best[0]
    candidates = frozenset(a.name for a in selected.attendees if not a.declined and a.name)
    return CalendarContext(candidates, selected.id, selected.title, False, context_source,
                           "selected event by maximum overlap", events)


def load_context_for_audio(audio_path: Any, context_path: Any = None) -> CalendarContext | None:
    """Load an optional calendar sidecar associated with an audio recording.

    Candidate paths are checked in explicit, stem-suffixed, then environment
    order. Missing or malformed optional sidecars return ``None``.
    """
    audio = Path(audio_path)
    candidates = []
    if context_path:
        candidates.append(Path(context_path))
    candidates.extend((audio.with_name(f"{audio.stem}_calendar.json"),
                       audio.with_name(f"{audio.stem}.calendar.json")))
    import os
    environment_path = os.environ.get("HIDOCK_CALENDAR_CONTEXT")
    if environment_path:
        candidates.append(Path(environment_path))
    selected_path = next((path for path in candidates if path.is_file()), None)
    if selected_path is None:
        return None
    try:
        payload = json.loads(selected_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        recording_start = payload.get("recording_start")
        recording_end = payload.get("recording_end")
        context = load_context(payload, recording_start, recording_end,
                               source=payload.get("source") or "microsoft365-mcp")
        try:
            from shared.voice_library_lite import load_library
            library = load_library()
        except Exception:
            library = {"speakers": {}}
        matched = candidate_names_for_voice_library(context, library)
        return CalendarContext(matched, context.selected_event_id, context.selected_event_title,
                               context.ambiguous, context.source, context.reason, context.events)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def summary(context: CalendarContext) -> str:
    return context.summary()


def to_metadata(context: CalendarContext) -> dict[str, Any]:
    return context.to_metadata()


def _library_entries(library: Any) -> Iterable[tuple[str, dict[str, Any]]]:
    speakers = library.get("speakers", {}) if isinstance(library, dict) else {}
    if isinstance(speakers, dict):
        return ((str(name), entry if isinstance(entry, dict) else {}) for name, entry in speakers.items())
    return ()


def candidate_names_for_voice_library(context: CalendarContext, library: Any) -> frozenset[str]:
    """Return library display names matching calendar names or email aliases."""
    names = {normalize_name(name) for name in context.candidate_names}
    emails = {normalize_email(attendee.email) for event in context.events
              if event.id == context.selected_event_id for attendee in event.attendees
              if not attendee.declined and attendee.email}
    result = set()
    for display_name, entry in _library_entries(library):
        aliases = entry.get("aliases", [])
        if isinstance(aliases, str):
            aliases = [aliases]
        calendar_emails = entry.get("calendar_emails", [])
        if isinstance(calendar_emails, str):
            calendar_emails = [calendar_emails]
        if normalize_name(display_name) in names or any(normalize_name(a) in names for a in aliases):
            result.add(display_name)
        elif emails.intersection(normalize_email(e) for e in calendar_emails):
            result.add(display_name)
    return frozenset(result)


__all__ = ["CalendarAttendee", "CalendarEvent", "CalendarContext", "parse_context_payload",
           "load_context", "load_context_for_audio", "candidate_names_for_voice_library",
           "summary", "to_metadata", "normalize_name", "normalize_email"]
