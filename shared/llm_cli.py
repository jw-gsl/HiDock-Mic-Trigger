"""LLM CLI detection and invocation — uses existing AI subscriptions.

Detects installed LLM command-line tools (claude, codex, gemini, ollama)
and provides a unified interface for querying them. This avoids API key
management and leverages the user's existing subscriptions.

Detection priority: claude > codex > gemini > ollama
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass

# Detection order — first available wins for "auto" mode
_CLI_PRIORITY = ["claude", "codex", "gemini", "ollama"]

# How to invoke each CLI with a prompt via stdin
_CLI_CONFIGS: dict[str, dict] = {
    "claude": {
        "command": ["claude", "--print"],
        "description": "Claude (Anthropic) — requires Claude Pro/Max subscription",
    },
    "codex": {
        "command": ["codex", "--quiet"],
        "description": "Codex (OpenAI) — requires OpenAI subscription",
    },
    "gemini": {
        "command": ["gemini"],
        "description": "Gemini (Google) — requires Gemini subscription",
    },
    "ollama": {
        "command": ["ollama", "run", "llama3.2"],
        "description": "Ollama (local) — free, runs locally, requires ollama install",
    },
}


@dataclass
class LLMEngine:
    """An available LLM engine."""

    name: str
    command: list[str]
    description: str


def _get_ollama_command() -> list[str]:
    """Get the ollama command with the configured model name."""
    try:
        from shared.config_store import get_config
        model = get_config().get("summarization", "ollama_model", "llama3.2")
    except Exception:
        model = "llama3.2"
    return ["ollama", "run", model]


def detect_engines() -> list[LLMEngine]:
    """Detect all available LLM CLI tools on the system.

    Returns:
        List of available engines in priority order.
    """
    available = []
    for name in _CLI_PRIORITY:
        cfg = _CLI_CONFIGS[name]
        binary = cfg["command"][0]
        if shutil.which(binary):
            command = _get_ollama_command() if name == "ollama" else list(cfg["command"])
            available.append(LLMEngine(
                name=name,
                command=command,
                description=cfg["description"],
            ))
    return available


def get_engine(name: str = "auto") -> LLMEngine | None:
    """Get a specific engine by name, or the best available one.

    Args:
        name: Engine name ("claude", "codex", "gemini", "ollama") or "auto".
              "auto" returns the first available engine in priority order.
              "none" always returns None.

    Returns:
        LLMEngine or None if no engine is available.
    """
    if name == "none":
        return None

    if name == "auto":
        engines = detect_engines()
        return engines[0] if engines else None

    if name in _CLI_CONFIGS:
        cfg = _CLI_CONFIGS[name]
        binary = cfg["command"][0]
        if shutil.which(binary):
            command = _get_ollama_command() if name == "ollama" else list(cfg["command"])
            return LLMEngine(
                name=name,
                command=command,
                description=cfg["description"],
            )
    return None


def query(
    prompt: str,
    engine: LLMEngine | None = None,
    timeout: int = 120,
) -> str | None:
    """Send a prompt to an LLM CLI and return the response.

    The prompt is piped via stdin to avoid OS argument length limits.

    Args:
        prompt: The full prompt text to send.
        engine: Engine to use. If None, auto-detects.
        timeout: Maximum seconds to wait for response.

    Returns:
        The LLM's response text, or None if no engine available or on error.
    """
    if engine is None:
        engine = get_engine("auto")
    if engine is None:
        return None

    try:
        result = subprocess.run(
            engine.command,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            print(
                f"LLM CLI ({engine.name}) failed: {result.stderr[:200]}",
                file=sys.stderr,
            )
            return None
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        print(f"LLM CLI ({engine.name}) timed out after {timeout}s", file=sys.stderr)
        return None
    except FileNotFoundError:
        print(f"LLM CLI ({engine.name}) not found: {engine.command[0]}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"LLM CLI ({engine.name}) error: {e}", file=sys.stderr)
        return None


def _content_blocks(ev: dict) -> list:
    """The content-block list from an assistant/user stream-json event."""
    msg = ev.get("message")
    if isinstance(msg, dict):
        blocks = msg.get("content")
        if isinstance(blocks, list):
            return blocks
    return []


def _tool_result_preview(content, limit: int = 200) -> str | None:
    """A short text preview of a tool_result's content (str or block list)."""
    if content is None:
        return None
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and isinstance(b.get("text"), str):
                parts.append(b["text"])
        text = "\n".join(parts)
    else:
        text = str(content)
    text = text.strip()
    if not text:
        return None
    return text if len(text) <= limit else text[:limit] + "…"


def _start_watchdog(proc, timeout: int) -> tuple[threading.Timer, threading.Event]:
    """Start a wall-clock deadline for a streaming subprocess.

    The blocking stdout iteration has no per-read timeout, so a hung CLI
    would block forever. Killing the process at the deadline closes its
    stdout, which terminates the iteration. Returns (timer, timed_out_event);
    the caller must cancel the timer on completion and treat a set event as
    a failed (partial) stream.
    """
    timed_out = threading.Event()

    def _kill():
        timed_out.set()
        try:
            proc.kill()
        except Exception:
            pass

    timer = threading.Timer(timeout, _kill)
    timer.daemon = True
    timer.start()
    return timer, timed_out


def _consume_claude_stream(lines, ev_out, on_text=None, emit_text=True) -> str | None:
    """Parse Claude ``stream-json`` NDJSON lines into normalized events.

    Pure over an iterable of strings so it can be unit-tested with captured
    fixtures. Forwards text deltas to ``on_text`` and emits ``meta`` / ``text``
    / ``tool`` / ``tool_result`` / ``usage`` on ``ev_out``. Returns the final
    result text (authoritative ``result`` event), the accumulated deltas when
    no result event arrived, or None when the result event signals an error.

    When ``emit_text`` is False, raw text deltas are NOT emitted as ``text``
    events on ``ev_out`` — the caller is expected to forward cleaned text via
    ``on_text`` (e.g. the summary flow strips a machine header before display).
    Structured events (meta / tool / tool_result / usage) are emitted either way.
    """
    chunks: list[str] = []
    result_text: str | None = None
    errored = False
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue                            # skip terminal-escape / non-JSON noise
        etype = ev.get("type")
        if etype == "stream_event":
            inner = ev.get("event") or {}
            if inner.get("type") == "content_block_delta":
                delta = inner.get("delta") or {}
                txt = delta.get("text")
                if txt:
                    chunks.append(txt)
                    if emit_text:
                        ev_out.text(txt)
                    if on_text:
                        try:
                            on_text(txt)
                        except Exception:
                            pass
        elif etype == "system" and ev.get("subtype") == "init":
            ev_out.meta(
                engine="claude",
                session_id=ev.get("session_id"),
                model=ev.get("model"),
            )
        elif etype == "assistant":
            # Full tool_use blocks (with complete input) arrive here.
            for block in _content_blocks(ev):
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    ev_out.tool(
                        id=block.get("id") or "",
                        name=block.get("name") or "tool",
                        input=block.get("input"),
                    )
        elif etype == "user":
            # tool_result blocks come back as a user turn.
            for block in _content_blocks(ev):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    ev_out.tool_result(
                        id=block.get("tool_use_id") or "",
                        ok=not bool(block.get("is_error")),
                        preview=_tool_result_preview(block.get("content")),
                    )
        elif etype == "result":
            if ev.get("is_error"):
                errored = True
            else:
                result_text = ev.get("result")
            usage = ev.get("usage") or {}
            ev_out.usage(
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                cost_usd=ev.get("total_cost_usd"),
            )
    if result_text is not None:
        return result_text
    if errored:
        # The stream ended in an error (rate limit / usage cap / internal
        # failure): any accumulated deltas are partial — don't return them
        # as if they were a complete response.
        return None
    return "".join(chunks)


def query_streaming(
    prompt: str,
    engine: LLMEngine | None = None,
    timeout: int = 240,
    on_text=None,
    on_event=None,
    emit_text=True,
) -> str | None:
    """Stream an LLM response, invoking ``on_text(delta)`` as text arrives.

    For the ``claude`` engine this uses ``--output-format stream-json
    --include-partial-messages`` and forwards each ``content_block_delta``
    text fragment to ``on_text`` as it streams, returning the full text from
    the final ``result`` event (authoritative) or the accumulated deltas.

    For other engines (no realtime stream-json support) it falls back to a
    single blocking ``query()`` and calls ``on_text`` once with the whole
    response. Returns the full text, or None on failure.

    ``on_event`` is an optional :class:`shared.agent_events.EventEmitter`. When
    given, normalized events are emitted alongside ``on_text``: ``meta`` (model
    / session id), ``text`` deltas, ``tool`` / ``tool_result`` (claude only),
    and ``usage``. The desktop apps render this stream in their formatted CLI
    view. For non-claude engines only a single ``text`` event is emitted.
    """
    from shared.agent_events import NULL_EMITTER
    ev_out = on_event if on_event is not None else NULL_EMITTER

    if engine is None:
        engine = get_engine("auto")
    if engine is None:
        return None

    if engine.name != "claude":
        ev_out.meta(engine=engine.name)
        full = query(prompt, engine=engine, timeout=timeout)
        if full:
            if on_text:
                on_text(full)
            if emit_text:
                ev_out.text(full)
        return full

    cmd = [
        "claude", "--print",
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--verbose",
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,   # claude's own banner/escapes — discard
            text=True,
            bufsize=1,                   # line-buffered so deltas arrive promptly
        )
    except FileNotFoundError:
        print("LLM CLI (claude) not found", file=sys.stderr)
        return None
    except Exception as e:
        print(f"LLM CLI (claude) streaming error: {e}", file=sys.stderr)
        return None

    # Wall-clock deadline: without it, a hung CLI blocks the stdout
    # iteration below forever. Killing the proc at the deadline makes
    # the iteration terminate and the timeout is reported as a failure.
    watchdog, timed_out = _start_watchdog(proc, timeout)
    try:
        try:
            if proc.stdin:
                proc.stdin.write(prompt)
                proc.stdin.close()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            return None

        try:
            assert proc.stdout is not None
            full = _consume_claude_stream(proc.stdout, ev_out, on_text, emit_text=emit_text)
        except Exception as e:
            print(f"LLM CLI (claude) streaming read error: {e}", file=sys.stderr)
            try:
                proc.kill()
            except Exception:
                pass
            return None
    finally:
        watchdog.cancel()

    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        # Stream already completed — a slow-exiting child shouldn't discard it.
        try:
            proc.kill()
        except Exception:
            pass
    else:
        if proc.returncode != 0:
            if timed_out.is_set():
                print(f"LLM CLI (claude) timed out after {timeout}s", file=sys.stderr)
            else:
                print(f"LLM CLI (claude) exited with code {proc.returncode}", file=sys.stderr)
            full = None

    return full.strip() if full else None


class _SessionSniffer:
    """Wraps an EventEmitter to record the claude session id from ``meta``
    events (so the chat caller can resume) while passing everything through."""

    def __init__(self, inner):
        self._inner = inner
        self.session_id = None

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def meta(self, **fields):
        if fields.get("session_id"):
            self.session_id = fields["session_id"]
        self._inner.meta(**fields)


def chat_streaming(
    prompt: str,
    engine: LLMEngine | None = None,
    cwd: str | None = None,
    resume: str | None = None,
    allowed_tools: list[str] | None = None,
    timeout: int = 600,
    on_event=None,
) -> tuple[str | None, str | None]:
    """A conversational streaming turn for the desktop chat view.

    For ``claude`` this runs headless ``stream-json`` in ``cwd`` with an
    optional tool allow-list and ``--resume`` for multi-turn, emits normalized
    events on ``on_event`` (incl. ``tool`` / ``tool_result``), and returns
    ``(text, session_id)``. For other engines it is a single-shot text turn
    (no tools, no session): returns ``(text, None)``.
    """
    from shared.agent_events import NULL_EMITTER
    ev = on_event if on_event is not None else NULL_EMITTER

    if engine is None:
        engine = get_engine("auto")
    if engine is None:
        ev.error("No LLM engine available (is the `claude` CLI installed and signed in?)")
        return None, None

    if engine.name != "claude":
        ev.meta(engine=engine.name)
        full = query(prompt, engine=engine, timeout=timeout)
        if full:
            ev.text(full)
        return full, None

    cmd = [
        "claude", "--print",
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--verbose",
    ]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    if resume:
        cmd += ["--resume", resume]

    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
            cwd=cwd or None,
        )
    except FileNotFoundError:
        ev.error("claude CLI not found")
        return None, None
    except Exception as e:
        ev.error(f"claude chat error: {e}")
        return None, None

    # Wall-clock deadline — same rationale as query_streaming: a hung CLI
    # would otherwise block the stdout iteration forever.
    watchdog, timed_out = _start_watchdog(proc, timeout)
    sniffer = _SessionSniffer(ev)
    try:
        try:
            if proc.stdin:
                proc.stdin.write(prompt)
                proc.stdin.close()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            return None, None

        try:
            assert proc.stdout is not None
            text = _consume_claude_stream(proc.stdout, sniffer)
        except Exception as e:
            ev.error(f"claude chat read error: {e}")
            try:
                proc.kill()
            except Exception:
                pass
            return None, sniffer.session_id
    finally:
        watchdog.cancel()

    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        # Stream already completed — a slow-exiting child shouldn't discard it.
        try:
            proc.kill()
        except Exception:
            pass
    else:
        if proc.returncode != 0:
            if timed_out.is_set():
                ev.error(f"claude timed out after {timeout}s")
            else:
                ev.error(f"claude exited with code {proc.returncode}")
            text = None
    return (text.strip() if text else None), sniffer.session_id


def query_json(
    prompt: str,
    engine: LLMEngine | None = None,
    timeout: int = 120,
) -> dict | None:
    """Send a prompt and parse the response as JSON.

    Extracts JSON from the response even if surrounded by markdown fences
    or other text.

    Args:
        prompt: The prompt (should instruct the LLM to respond with JSON).
        engine: Engine to use.
        timeout: Maximum seconds to wait.

    Returns:
        Parsed dict, or None if unavailable or parse error.
    """
    raw = query(prompt, engine=engine, timeout=timeout)
    if raw is None:
        return None

    return _extract_json(raw)


def _extract_json(text: str) -> dict | None:
    """Extract a JSON object from text that may contain markdown fences."""
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON inside markdown code fences
    import re
    patterns = [
        r"```json\s*\n(.*?)\n```",
        r"```\s*\n(.*?)\n```",
        r"\{.*\}",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            candidate = match.group(1) if match.lastindex else match.group(0)
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

    return None
