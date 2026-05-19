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
