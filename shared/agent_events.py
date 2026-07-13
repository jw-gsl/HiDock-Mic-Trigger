"""Normalized agent event stream — shared by the macOS and Windows desktop apps.

The desktop apps render a single normalized event schema so that
engine-specific parsing (Claude's ``stream-json``, plain-text engines, …) lives
in exactly one place: the Python layer. Both platforms consume the same stream
and render it with their native formatted CLI view.

Wire protocol
-------------
Events are emitted as newline-delimited JSON on **stderr**, one object per line,
each line prefixed with :data:`EVENT_PREFIX` (an ASCII Unit-Separator, ``0x1f``)
so the app can cheaply tell event lines apart from ordinary log output. The
pipeline's final structured result still goes to **stdout** unchanged, so
existing callers that parse stdout are unaffected.

Each event object has a ``"t"`` discriminator plus type-specific fields:

    {"t":"stage","label":"Summarising…"}            coarse progress step
    {"t":"text","delta":"partial assistant text"}    streamed assistant prose
    {"t":"tool","id":"toolu_…","name":"Read","input":{…}}   claude only
    {"t":"tool_result","id":"toolu_…","ok":true,"preview":"…"}  claude only
    {"t":"usage","input_tokens":12,"output_tokens":34,"cost_usd":0.01}
    {"t":"meta","session_id":"…","model":"…","engine":"claude"}
    {"t":"error","message":"…"}
    {"t":"done","ok":true,"session_id":"…"}          terminal event

Tool / usage / meta events are populated for the ``claude`` engine; other
engines (codex, gemini, ollama) emit ``stage`` + ``text`` + ``done`` only, since
their CLIs do not expose a structured event stream.
"""
from __future__ import annotations

import json
import sys
from typing import Any, TextIO

# ASCII Unit Separator. Zero collision risk with real prose/log lines, and a
# trivial ``hasPrefix`` check on the app side. NB: 0x1f is deliberately chosen
# over 0x1c–0x1e (which Python's ``str.splitlines()`` treats as line breaks).
# Kept as a module constant so the Swift / Qt sides reference the same byte.
EVENT_PREFIX = "\x1f"


class EventEmitter:
    """Writes normalized events as prefixed NDJSON to a text stream.

    Default stream is ``sys.stderr`` (where the desktop app already line-reads
    pipeline output). When ``enabled`` is False every call is a no-op, so call
    sites can stay unconditional and a single flag toggles the whole stream
    (e.g. legacy callers without ``--events``).
    """

    def __init__(self, stream: TextIO | None = None, enabled: bool = True):
        self.stream = stream if stream is not None else sys.stderr
        self.enabled = enabled

    def emit(self, t: str, **fields: Any) -> None:
        if not self.enabled:
            return
        fields["t"] = t
        try:
            self.stream.write(EVENT_PREFIX + json.dumps(fields, ensure_ascii=False) + "\n")
            self.stream.flush()
        except Exception:
            # Never let telemetry break the actual work.
            pass

    # ── Convenience wrappers (one per event type) ─────────────────────────
    def stage(self, label: str) -> None:
        self.emit("stage", label=label)

    def text(self, delta: str) -> None:
        if delta:
            self.emit("text", delta=delta)

    def tool(self, id: str, name: str, input: Any = None) -> None:
        self.emit("tool", id=id, name=name, input=input)

    def tool_result(self, id: str, ok: bool, preview: str | None = None) -> None:
        self.emit("tool_result", id=id, ok=ok, preview=preview)

    def usage(self, **fields: Any) -> None:
        self.emit("usage", **fields)

    def meta(self, **fields: Any) -> None:
        self.emit("meta", **fields)

    def error(self, message: str) -> None:
        self.emit("error", message=message)

    def done(self, ok: bool = True, **fields: Any) -> None:
        self.emit("done", ok=ok, **fields)


def parse_event_line(line: str) -> dict | None:
    """Decode one stderr line into an event dict, or None if it isn't one.

    Mirrors what the Swift/Qt apps do; provided here for tests and for any
    Python-side consumer. Tolerant of the prefix being absent.
    """
    if not line:
        return None
    if line.startswith(EVENT_PREFIX):
        line = line[len(EVENT_PREFIX):]
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        ev = json.loads(line)
    except Exception:
        return None
    return ev if isinstance(ev, dict) and "t" in ev else None


# Shared no-op emitter for call sites that may or may not have events enabled.
NULL_EMITTER = EventEmitter(enabled=False)
