# Calendar-assisted speaker matching

**Status:** First implementation in progress
**Scope:** read-only Microsoft 365 calendar context used to narrow local Voice Library matching

## Behaviour

Before a diarized speaker is matched against the Voice Library, the pipeline may
load a calendar-context JSON file produced by the Microsoft 365 MCP integration.
The calendar is a candidate filter, not proof of identity:

1. Select the event with the strongest overlap with the recording window.
2. If multiple events are equally plausible, suppress automatic voice labels.
3. Remove declined attendees.
4. Match attendee names and configured email aliases to Voice Library entries.
5. Run the local voice embedding comparison only against those candidates.
6. Fall back to the existing voice-only path when no context is available.

Audio, embeddings, transcripts, and diarized content are never sent to the
calendar provider.

## Context file contract

The file can be written by an MCP bridge next to the recording as
`<recording-stem>_calendar.json`, or passed explicitly with
`transcribe ... --calendar-context /path/to/context.json`:

```json
{
  "source": "microsoft365-mcp",
  "recording_start": "2026-07-14T09:00:00+01:00",
  "recording_end": "2026-07-14T10:00:00+01:00",
  "events": [
    {
      "id": "event-id",
      "subject": "Project review",
      "start": {"dateTime": "2026-07-14T09:00:00+01:00"},
      "end": {"dateTime": "2026-07-14T10:00:00+01:00"},
      "attendees": [
        {
          "emailAddress": {
            "name": "Alex Example",
            "address": "alex@example.com"
          },
          "status": "accepted"
        }
      ]
    }
  ]
}
```

Names are matched case-insensitively. For reliable matching when calendar and
Voice Library display names differ, associate an email with a voice entry:

```sh
python -m shared.voice_library_lite set-calendar-emails \
  --name "Alex Example" --email alex@example.com
```

## Deliberate boundary

The HiDock process does not contain Microsoft Graph credentials or call an MCP
server directly. The provider-specific MCP bridge supplies the small,
read-only context file; the local pipeline validates and applies it. This keeps
the transcription process offline-capable and makes calendar access optional.

## Acceptance cases

- No context file: existing voice-only matching is unchanged.
- One overlapping event: enrolled attendees become the candidate set.
- Declined attendee: excluded.
- Overlapping equally plausible events: no automatic calendar-assisted label.
- No matching Voice Library identity: no automatic label.
- Low voice similarity: no automatic label, leaving normal review UI available.
