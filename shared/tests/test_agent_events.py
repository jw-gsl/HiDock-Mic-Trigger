"""Tests for shared.agent_events and the Claude stream-json normalizer.

The fixture lines mirror real ``claude --print --output-format stream-json
--include-partial-messages --verbose`` output (captured 2026-06-26), trimmed to
the fields the normalizer reads.
"""
from __future__ import annotations

import io
import json

from shared.agent_events import EVENT_PREFIX, EventEmitter, NULL_EMITTER, parse_event_line
from shared.llm_cli import _consume_claude_stream, _tool_result_preview


# ---------------------------------------------------------------------------
# EventEmitter wire format
# ---------------------------------------------------------------------------
class TestEventEmitter:
    def _drain(self, buf: io.StringIO) -> list[dict]:
        return [parse_event_line(ln) for ln in buf.getvalue().splitlines() if ln]

    def test_each_line_is_prefixed_ndjson(self):
        buf = io.StringIO()
        em = EventEmitter(stream=buf)
        em.stage("Summarising…")
        em.text("hello")
        for raw in buf.getvalue().splitlines():
            assert raw.startswith(EVENT_PREFIX)
            json.loads(raw[len(EVENT_PREFIX):])  # valid JSON after the prefix

    def test_event_types_and_fields(self):
        buf = io.StringIO()
        em = EventEmitter(stream=buf)
        em.meta(engine="claude", session_id="s1", model="m")
        em.stage("Classifying…")
        em.text("partial")
        em.tool(id="t1", name="Read", input={"file_path": "/x"})
        em.tool_result(id="t1", ok=True, preview="contents")
        em.usage(input_tokens=10, output_tokens=20, cost_usd=0.01)
        em.done(ok=True, session_id="s1")
        events = self._drain(buf)
        assert [e["t"] for e in events] == [
            "meta", "stage", "text", "tool", "tool_result", "usage", "done",
        ]
        assert events[3]["name"] == "Read"
        assert events[3]["input"] == {"file_path": "/x"}
        assert events[4]["ok"] is True
        assert events[6]["ok"] is True

    def test_disabled_emitter_writes_nothing(self):
        buf = io.StringIO()
        em = EventEmitter(stream=buf, enabled=False)
        em.stage("x")
        em.text("y")
        assert buf.getvalue() == ""

    def test_empty_text_delta_suppressed(self):
        buf = io.StringIO()
        EventEmitter(stream=buf).text("")
        assert buf.getvalue() == ""

    def test_parse_event_line_rejects_non_events(self):
        assert parse_event_line("STAGE:1/2:Transcribing") is None
        assert parse_event_line("") is None
        assert parse_event_line(EVENT_PREFIX + "{not json}") is None
        assert parse_event_line('{"no_discriminator": 1}') is None
        # Tolerates a missing prefix as long as it's a valid event object.
        assert parse_event_line('{"t":"stage","label":"x"}') == {"t": "stage", "label": "x"}


# ---------------------------------------------------------------------------
# Claude stream-json normalizer
# ---------------------------------------------------------------------------
def _delta(text: str) -> str:
    return json.dumps({
        "type": "stream_event",
        "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}},
    })


SYSTEM_INIT = json.dumps({
    "type": "system", "subtype": "init",
    "session_id": "sess-123", "model": "claude-opus-4-8",
})
ASSISTANT_TOOL_USE = json.dumps({
    "type": "assistant",
    "message": {"content": [
        {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": "/tmp/x.txt"}},
    ]},
})
USER_TOOL_RESULT = json.dumps({
    "type": "user",
    "message": {"content": [
        {"type": "tool_result", "tool_use_id": "toolu_1", "is_error": None,
         "content": "1\thello from a test file\n"},
    ]},
})
RESULT = json.dumps({
    "type": "result", "subtype": "success", "is_error": False,
    "result": "The file contains a greeting.",
    "session_id": "sess-123",
    "usage": {"input_tokens": 1200, "output_tokens": 45},
    "total_cost_usd": 0.0123,
})


class TestConsumeClaudeStream:
    def test_full_tool_run(self):
        buf = io.StringIO()
        em = EventEmitter(stream=buf)
        lines = [
            SYSTEM_INIT, ASSISTANT_TOOL_USE, USER_TOOL_RESULT,
            _delta("The file "), _delta("contains a greeting."), RESULT,
        ]
        result = _consume_claude_stream(lines, em)
        # Authoritative result text comes from the result event.
        assert result == "The file contains a greeting."

        events = [parse_event_line(ln) for ln in buf.getvalue().splitlines() if ln]
        by_type = {}
        for e in events:
            by_type.setdefault(e["t"], []).append(e)

        meta = by_type["meta"][0]
        assert meta["session_id"] == "sess-123"
        assert meta["model"] == "claude-opus-4-8"

        tool = by_type["tool"][0]
        assert tool["name"] == "Read"
        assert tool["input"] == {"file_path": "/tmp/x.txt"}

        tr = by_type["tool_result"][0]
        assert tr["id"] == "toolu_1"
        assert tr["ok"] is True
        assert "hello from a test file" in tr["preview"]

        assert "".join(e["delta"] for e in by_type["text"]) == "The file contains a greeting."

        usage = by_type["usage"][0]
        assert usage["input_tokens"] == 1200
        assert usage["cost_usd"] == 0.0123

    def test_forwards_on_text_and_falls_back_to_deltas(self):
        seen: list[str] = []
        # No result event → return accumulated deltas; on_text gets each delta.
        result = _consume_claude_stream(
            [_delta("foo"), _delta("bar")], NULL_EMITTER, on_text=seen.append,
        )
        assert seen == ["foo", "bar"]
        assert result == "foobar"

    def test_tool_result_error_flag(self):
        buf = io.StringIO()
        em = EventEmitter(stream=buf)
        err = json.dumps({
            "type": "user",
            "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t9", "is_error": True, "content": "boom"},
            ]},
        })
        _consume_claude_stream([err], em)
        tr = [parse_event_line(ln) for ln in buf.getvalue().splitlines() if ln][0]
        assert tr["t"] == "tool_result"
        assert tr["ok"] is False

    def test_ignores_non_json_noise(self):
        result = _consume_claude_stream(
            ["\x1b[2K terminal escape noise", "", _delta("ok")], NULL_EMITTER,
        )
        assert result == "ok"


class TestToolResultPreview:
    def test_string_content(self):
        assert _tool_result_preview("hello") == "hello"

    def test_block_list_content(self):
        blocks = [{"type": "text", "text": "line1"}, {"type": "text", "text": "line2"}]
        assert _tool_result_preview(blocks) == "line1\nline2"

    def test_truncation(self):
        out = _tool_result_preview("x" * 500, limit=200)
        assert out is not None and len(out) == 201 and out.endswith("…")

    def test_none_and_empty(self):
        assert _tool_result_preview(None) is None
        assert _tool_result_preview("   ") is None
