"""Local corrections dictionary for transcript post-processing.

Stores word/phrase replacements in ~/HiDock/corrections.json.
Applied automatically after each transcription to fix recurring
misrecognitions (e.g. "volaris" → "VOLARIS", "hyde oates" → "HiDock").
"""
from __future__ import annotations

import json
import re
from pathlib import Path

CORRECTIONS_PATH = Path.home() / "HiDock" / "corrections.json"


def load_corrections() -> dict[str, str]:
    """Load the corrections dictionary. Returns {wrong: right}."""
    if not CORRECTIONS_PATH.exists():
        return {}
    try:
        data = json.loads(CORRECTIONS_PATH.read_text(encoding="utf-8"))
        return data.get("corrections", {})
    except (json.JSONDecodeError, OSError):
        return {}


def save_corrections(corrections: dict[str, str]) -> None:
    """Save the corrections dictionary."""
    CORRECTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CORRECTIONS_PATH.write_text(
        json.dumps({"corrections": corrections}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def add_correction(wrong: str, right: str) -> dict[str, str]:
    """Add a correction and return the updated dictionary."""
    corrections = load_corrections()
    corrections[wrong.lower()] = right
    save_corrections(corrections)
    return corrections


def remove_correction(wrong: str) -> dict[str, str]:
    """Remove a correction and return the updated dictionary."""
    corrections = load_corrections()
    corrections.pop(wrong.lower(), None)
    save_corrections(corrections)
    return corrections


def apply_corrections(text: str, corrections: dict[str, str] | None = None) -> str:
    """Apply all corrections to a text string.

    Uses case-insensitive word-boundary matching so "volaris" matches
    in any context but doesn't match inside other words.
    """
    if corrections is None:
        corrections = load_corrections()
    if not corrections:
        return text

    for wrong, right in corrections.items():
        # Case-insensitive replacement preserving word boundaries
        pattern = re.compile(re.escape(wrong), re.IGNORECASE)
        text = pattern.sub(right, text)

    return text
