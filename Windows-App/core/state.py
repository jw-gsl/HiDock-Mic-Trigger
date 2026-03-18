"""Transcription state management — atomic load/save.

Mirrors transcription-pipeline/state.py for compatibility.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from core.config import STATE_PATH


def load_state() -> dict:
    """Load the transcription state file."""
    if not STATE_PATH.exists():
        return {"transcriptions": {}}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"transcriptions": {}}


def save_state(state: dict) -> None:
    """Save state atomically via temp file + rename."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(STATE_PATH.parent), suffix=".tmp"
    )
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        Path(tmp_path).replace(STATE_PATH)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise
