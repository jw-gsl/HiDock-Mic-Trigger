"""Atomic state management for transcription pipeline."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from config import HIDOCK_ROOT

STATE_PATH = HIDOCK_ROOT / "transcription-pipeline" / "state.json"

def _default_state() -> dict:
    # Fresh dict per call: a shared module-level default would alias its
    # inner "transcriptions" dict across callers, so mutations would leak
    # between loads.
    return {"transcriptions": {}}


def load_state() -> dict:
    """Load transcription state from disk, returning default if missing/corrupt."""
    if not STATE_PATH.exists():
        return _default_state()
    try:
        return json.loads(STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return _default_state()


def save_state(state: dict) -> None:
    """Atomically write state to disk (write-to-tmp then rename)."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=STATE_PATH.parent, suffix=".tmp", prefix="state-"
    )
    try:
        with open(tmp_fd, "w") as f:
            json.dump(state, f, indent=2)
        Path(tmp_path).replace(STATE_PATH)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise
