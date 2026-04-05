"""Transcript summarization — extracts structured intelligence from transcripts.

Uses the LLM CLI module to send transcripts to an available AI engine
and extract: title, action items, decisions, key points, and tags.

Gracefully returns empty results if no LLM engine is available.
"""
from __future__ import annotations

import sys
from typing import Any

from shared.llm_cli import LLMEngine, get_engine, query_json

# Maximum transcript length to send in a single prompt (chars).
# Longer transcripts are truncated with a note.
_MAX_TRANSCRIPT_CHARS = 30_000

_SUMMARIZE_PROMPT = """You are analyzing a meeting transcript. Extract structured information and respond with ONLY a JSON object (no markdown fences, no other text).

The JSON must have exactly these keys:
{{
  "title": "A concise 3-8 word title for this meeting/recording",
  "action_items": [
    {{"task": "what needs to be done", "assignee": "person name or empty string", "due": "date or empty string", "status": "open"}}
  ],
  "decisions": [
    {{"text": "what was decided", "topic": "topic area or empty string"}}
  ],
  "key_points": ["important point 1", "important point 2"],
  "tags": ["topic1", "topic2"],
  "summary_text": "A 2-4 sentence summary of the meeting/recording"
}}

Rules:
- title: Short, descriptive, no quotes needed
- action_items: Only include clear commitments or tasks. Empty list if none.
- decisions: Only include explicit decisions made. Empty list if none.
- key_points: 3-7 most important points discussed. Empty list for very short recordings.
- tags: 2-5 topic tags (lowercase, no spaces). Empty list if unclear.
- summary_text: Brief overview of what was discussed/recorded.
- If the transcript is a solo voice memo or dictation, adapt accordingly (fewer action items, simpler structure).
- Respond with ONLY the JSON object.

TRANSCRIPT:
{transcript}"""


def summarize(
    transcript_text: str,
    engine_name: str = "auto",
    timeout: int = 120,
) -> dict:
    """Summarize a transcript and extract structured information.

    Args:
        transcript_text: The full transcript text (may include speaker labels).
        engine_name: LLM engine to use ("auto", "claude", "codex", etc.)
        timeout: Max seconds to wait for LLM response.

    Returns:
        Dict with keys: title, action_items, decisions, key_points, tags,
        summary_text. Returns empty/default values if no LLM is available
        or if summarization fails.
    """
    engine = get_engine(engine_name)
    if engine is None:
        print("No LLM engine available, skipping summarization", file=sys.stderr)
        return _empty_summary()

    # Truncate very long transcripts
    text = transcript_text
    if len(text) > _MAX_TRANSCRIPT_CHARS:
        text = text[:_MAX_TRANSCRIPT_CHARS] + "\n\n[... transcript truncated ...]"

    prompt = _SUMMARIZE_PROMPT.format(transcript=text)

    print(f"Summarizing with {engine.name}...", file=sys.stderr)
    result = query_json(prompt, engine=engine, timeout=timeout)

    if result is None:
        print("Summarization failed or returned invalid JSON", file=sys.stderr)
        return _empty_summary()

    # Validate and normalize the result
    return _normalize_summary(result)


def _empty_summary() -> dict:
    """Return an empty summary structure."""
    return {
        "title": "",
        "action_items": [],
        "decisions": [],
        "key_points": [],
        "tags": [],
        "summary_text": "",
    }


def _normalize_summary(raw: dict) -> dict:
    """Normalize and validate a raw LLM summary response."""
    result = _empty_summary()

    result["title"] = str(raw.get("title", ""))[:100]
    result["summary_text"] = str(raw.get("summary_text", ""))

    # Normalize action items
    for item in raw.get("action_items", []):
        if isinstance(item, dict) and item.get("task"):
            result["action_items"].append({
                "task": str(item["task"]),
                "assignee": str(item.get("assignee", "")),
                "due": str(item.get("due", "")),
                "status": str(item.get("status", "open")),
            })

    # Normalize decisions
    for item in raw.get("decisions", []):
        if isinstance(item, dict) and item.get("text"):
            result["decisions"].append({
                "text": str(item["text"]),
                "topic": str(item.get("topic", "")),
            })

    # Normalize key points
    for item in raw.get("key_points", []):
        if isinstance(item, str) and item.strip():
            result["key_points"].append(item.strip())

    # Normalize tags
    for item in raw.get("tags", []):
        if isinstance(item, str) and item.strip():
            result["tags"].append(item.strip().lower())

    return result
