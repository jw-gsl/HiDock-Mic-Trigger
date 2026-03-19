"""Transcription wrapper — uses whisper.cpp via pywhispercpp.

Replaces the PyTorch/openai-whisper approach with a lightweight
whisper.cpp backend. Same model accuracy, ~50MB binary vs ~2GB.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from core.config import (
    MODELS_DIR,
    RAW_TRANSCRIPTS_DIR,
    WHISPER_LANGUAGE,
    WHISPER_MODEL,
    whisper_model_path,
    whisper_model_ready,
)
from core.state import load_state, save_state

# Cached model instance
_model = None


def _load_model():
    """Load whisper.cpp model (cached after first call)."""
    global _model
    if _model is not None:
        return _model

    if not whisper_model_ready():
        raise RuntimeError(
            "Whisper model not downloaded. Use the 'Download Model' button to get it."
        )

    from pywhispercpp.model import Model

    _model = Model(str(whisper_model_path()), n_threads=4)
    return _model


def transcribe_file(
    mp3_path: Path,
    model=None,
    on_progress: Callable[[int], None] | None = None,
) -> dict:
    """Transcribe a single audio file. Returns result dict."""
    mp3_path = mp3_path.resolve()
    basename = mp3_path.stem
    transcript_path = RAW_TRANSCRIPTS_DIR / f"{basename}.md"

    state = load_state()
    entry_key = mp3_path.name

    # Mark in_progress
    state["transcriptions"][entry_key] = {
        "status": "in_progress",
        "source_path": str(mp3_path),
        "transcript_path": str(transcript_path),
        "model": WHISPER_MODEL,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "duration_s": None,
        "last_error": None,
    }
    save_state(state)

    start_time = time.monotonic()
    try:
        if on_progress:
            on_progress(5)

        m = model if model is not None else _load_model()

        if on_progress:
            on_progress(15)

        segments = m.transcribe(str(mp3_path), language=WHISPER_LANGUAGE)

        if on_progress:
            on_progress(85)

        text = " ".join(seg.text.strip() for seg in segments).strip()

        RAW_TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        transcript_path.write_text(text + "\n", encoding="utf-8")

        if on_progress:
            on_progress(95)

        duration_s = round(time.monotonic() - start_time, 1)

        state = load_state()
        state["transcriptions"][entry_key] = {
            "status": "completed",
            "source_path": str(mp3_path),
            "transcript_path": str(transcript_path),
            "model": WHISPER_MODEL,
            "started_at": state["transcriptions"].get(entry_key, {}).get("started_at"),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "duration_s": duration_s,
            "last_error": None,
        }
        save_state(state)
        if on_progress:
            on_progress(100)

        return {
            "file": str(mp3_path),
            "transcript_path": str(transcript_path),
            "duration_s": duration_s,
            "status": "completed",
            "transcribed": True,
        }

    except Exception as e:
        duration_s = round(time.monotonic() - start_time, 1)
        state = load_state()
        state["transcriptions"][entry_key] = {
            "status": "failed",
            "source_path": str(mp3_path),
            "transcript_path": str(transcript_path),
            "model": WHISPER_MODEL,
            "started_at": state["transcriptions"].get(entry_key, {}).get("started_at"),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "duration_s": duration_s,
            "last_error": str(e),
        }
        save_state(state)

        return {
            "file": str(mp3_path),
            "transcript_path": None,
            "duration_s": duration_s,
            "status": "failed",
            "transcribed": False,
            "error": str(e),
        }


def get_transcription_status() -> dict:
    """Return transcription status keyed by MP3 filename."""
    state = load_state()
    lookup = {}
    for key, info in state.get("transcriptions", {}).items():
        lookup[key] = {
            "status": info.get("status", "unknown"),
            "transcript_path": info.get("transcript_path"),
            "transcribed": info.get("status") == "completed",
        }
    # Check for transcript files on disk not in state
    if RAW_TRANSCRIPTS_DIR.exists():
        from core.config import RECORDINGS_DIR, WATCH_EXTENSIONS
        if RECORDINGS_DIR.exists():
            for mp3 in RECORDINGS_DIR.iterdir():
                if mp3.suffix.lower() in WATCH_EXTENSIONS and mp3.name not in lookup:
                    for ext in (".md", ".txt"):
                        txt = RAW_TRANSCRIPTS_DIR / f"{mp3.stem}{ext}"
                        if txt.exists():
                            lookup[mp3.name] = {
                                "status": "completed",
                                "transcript_path": str(txt),
                                "transcribed": True,
                            }
                            break
    return lookup
