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


def query_streaming(
    prompt: str,
    engine: LLMEngine | None = None,
    timeout: int = 240,
    on_text=None,
) -> str | None:
    """Stream an LLM response, invoking ``on_text(delta)`` as text arrives.

    For the ``claude`` engine this uses ``--output-format stream-json
    --include-partial-messages`` and forwards each ``content_block_delta``
    text fragment to ``on_text`` as it streams, returning the full text from
    the final ``result`` event (authoritative) or the accumulated deltas.

    For other engines (no realtime stream-json support) it falls back to a
    single blocking ``query()`` and calls ``on_text`` once with the whole
    response. Returns the full text, or None on failure.
    """
    if engine is None:
        engine = get_engine("auto")
    if engine is None:
        return None

    if engine.name != "claude":
        full = query(prompt, engine=engine, timeout=timeout)
        if full and on_text:
            on_text(full)
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

    chunks: list[str] = []
    result_text: str | None = None
    try:
        assert proc.stdout is not None
        for line in proc.stdout:               # blocks per line; EOF when claude exits
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue                        # skip terminal-escape / non-JSON noise
            etype = ev.get("type")
            if etype == "stream_event":
                inner = ev.get("event") or {}
                if inner.get("type") == "content_block_delta":
                    delta = inner.get("delta") or {}
                    txt = delta.get("text")
                    if txt:
                        chunks.append(txt)
                        if on_text:
                            try:
                                on_text(txt)
                            except Exception:
                                pass
            elif etype == "result":
                if not ev.get("is_error"):
                    result_text = ev.get("result")
        proc.wait(timeout=10)
    except Exception as e:
        print(f"LLM CLI (claude) streaming read error: {e}", file=sys.stderr)
        try:
            proc.kill()
        except Exception:
            pass

    full = result_text if result_text is not None else "".join(chunks)
    return full.strip() if full else None


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
