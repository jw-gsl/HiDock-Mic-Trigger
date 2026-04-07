"""Transcription wrapper — uses whisper.cpp via pywhispercpp.

Replaces the PyTorch/openai-whisper approach with a lightweight
whisper.cpp backend. Same model accuracy, ~50MB binary vs ~2GB.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

# Add the repo root to sys.path so shared modules are importable
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.config import (  # noqa: E402
    RAW_TRANSCRIPTS_DIR,
    WHISPER_LANGUAGE,
    WHISPER_MODEL,
    whisper_model_path,
    whisper_model_ready,
)
from core.state import load_state, save_state  # noqa: E402

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
    diarize: bool = False,
    summarize: bool = False,
) -> dict:
    """Transcribe a single audio file. Returns result dict."""
    from shared.transcript_writer import write_transcript, format_diarized_transcript

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

        # Convert pywhispercpp segments to dicts for diarization
        whisper_dicts = []
        for seg in segments:
            whisper_dicts.append({
                "start": seg.t0 / 100.0 if hasattr(seg, "t0") else 0.0,
                "end": seg.t1 / 100.0 if hasattr(seg, "t1") else 0.0,
                "text": seg.text.strip(),
            })

        RAW_TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

        # Diarization pass
        diarized_result = None
        if diarize:
            try:
                from shared.diarize_lite import diarize as run_diarize
                from shared.voice_library_lite import identify_speakers
                from shared.audio_utils import load_audio, extract_embedding, segment_audio

                diarized_result = run_diarize(mp3_path, whisper_dicts)

                # Try to identify speakers from voice library
                audio = load_audio(mp3_path, sr=16000)
                speaker_segments = {}
                for seg in diarized_result["segments"]:
                    spk = seg["speaker"]
                    if spk not in speaker_segments:
                        speaker_segments[spk] = []
                    speaker_segments[spk].append((seg["start"], seg["end"]))

                speaker_embeddings = {}
                for spk, segs in speaker_segments.items():
                    chunks = segment_audio(audio, 16000, segs)
                    if chunks:
                        import numpy as np
                        combined = np.concatenate(chunks)
                        speaker_embeddings[spk] = extract_embedding(combined, sr=16000)

                if speaker_embeddings:
                    import numpy as np
                    emb_list = list(speaker_embeddings.values())
                    spk_list = list(speaker_embeddings.keys())
                    ids = identify_speakers(emb_list)
                    for i, spk in enumerate(spk_list):
                        name, conf = ids[i]
                        if name is not None:
                            diarized_result["speaker_names"][spk] = name

                # Write diarized JSON sidecar
                diarized_path = RAW_TRANSCRIPTS_DIR / f"{basename}_diarized.json"
                diarized_path.write_text(
                    json.dumps(diarized_result, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
            except Exception as e:
                print(f"Diarization failed (non-fatal): {e}", file=sys.stderr)
                diarized_result = None

        # Build plain text for summarization input
        if diarized_result:
            text = format_diarized_transcript(diarized_result)
        else:
            text = " ".join(seg.text.strip() for seg in segments).strip()

        # Optionally run LLM summarization
        summary = None
        if summarize:
            try:
                from shared.summarize import summarize as run_summarize
                if on_progress:
                    on_progress(90)
                summary = run_summarize(text)
            except Exception as e:
                print(f"Summarization failed (non-fatal): {e}", file=sys.stderr)

        # Write transcript with frontmatter
        write_transcript(
            transcript_path,
            text,
            source_path=mp3_path,
            model=WHISPER_MODEL,
            diarized_result=diarized_result,
            summary=summary,
        )

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

        # Run post-transcription hooks (non-fatal)
        try:
            from shared.hooks import run_hooks_pipeline
            run_hooks_pipeline(transcript_path, source_path=mp3_path, summary=summary)
        except Exception as e:
            print(f"Hooks failed (non-fatal): {e}", file=sys.stderr)

        if on_progress:
            on_progress(100)

        return {
            "file": str(mp3_path),
            "transcript_path": str(transcript_path),
            "duration_s": duration_s,
            "status": "completed",
            "transcribed": True,
            "summarized": summary is not None,
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


def _check_speakers_tagged(transcript_path: str | None) -> bool:
    """Check if all speakers in a diarized transcript have been named."""
    import re
    if not transcript_path:
        return False
    md_path = Path(transcript_path)
    diarized_path = md_path.parent / f"{md_path.stem}_diarized.json"
    if not diarized_path.exists():
        return False
    try:
        data = json.loads(diarized_path.read_text(encoding="utf-8"))
        speaker_names = data.get("speaker_names", {})
        if not speaker_names:
            return False
        return not any(re.match(r"^Speaker \d+$", name) for name in speaker_names.values())
    except (json.JSONDecodeError, OSError):
        return False


def _find_summary_path(mp3_name: str) -> str | None:
    """Find a matching summary file for the given recording."""
    from core.config import HIDOCK_ROOT
    summaries_dir = HIDOCK_ROOT / "Summaries"
    if not summaries_dir.exists():
        return None
    base_name = Path(mp3_name).stem
    for f in summaries_dir.iterdir():
        if f.suffix == ".md" and base_name in f.name:
            return str(f)
    return None


def get_transcription_status() -> dict:
    """Return transcription status keyed by MP3 filename."""
    state = load_state()
    lookup = {}
    for key, info in state.get("transcriptions", {}).items():
        tp = info.get("transcript_path")
        lookup[key] = {
            "status": info.get("status", "unknown"),
            "transcript_path": tp,
            "transcribed": info.get("status") == "completed",
            "speakers_tagged": _check_speakers_tagged(tp),
            "summary_path": _find_summary_path(key),
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
                            tp = str(txt)
                            lookup[mp3.name] = {
                                "status": "completed",
                                "transcript_path": tp,
                                "transcribed": True,
                                "speakers_tagged": _check_speakers_tagged(tp),
                                "summary_path": _find_summary_path(mp3.name),
                            }
                            break
    return lookup
