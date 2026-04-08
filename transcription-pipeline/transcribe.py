#!/usr/bin/env python3
"""HiDock transcription pipeline — CLI entry point.

Subcommands:
    transcribe <mp3-path>   Transcribe a single audio file
    transcribe-batch        Transcribe all un-transcribed recordings
    status                  JSON report of transcription state
"""
from __future__ import annotations

import argparse
import fcntl
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import config
from state import load_state, save_state

# Add the repo root to sys.path so shared modules are importable
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

LOCK_PATH = Path(config.HIDOCK_ROOT) / "transcription-pipeline" / ".transcribe.lock"


# ── Safe event logging (non-fatal) ───────────────────────────────────────────

def _log(event_type, **kwargs):
    """Log an event, silently ignoring failures."""
    try:
        from shared.event_log import log_event
        log_event(event_type, **kwargs)
    except Exception:
        pass  # Never let logging break the pipeline


def _ET(name: str):
    """Get an EventType by name, or fall back to the string."""
    try:
        from shared.event_log import EventType
        return getattr(EventType, name)
    except Exception:
        return name


def progress(pct: int) -> None:
    """Emit a PROGRESS line on stderr (matches extractor protocol)."""
    print(f"PROGRESS:{pct}", file=sys.stderr, flush=True)


def load_whisper_model():
    """Load Whisper model onto MPS (or CPU fallback)."""
    import torch
    import whisper

    device = config.WHISPER_DEVICE
    if device == "mps" and not torch.backends.mps.is_available():
        print("MPS not available, falling back to CPU", file=sys.stderr)
        device = "cpu"

    progress(5)
    model = whisper.load_model(
        config.WHISPER_MODEL,
        device=device,
        download_root=str(config.MODELS_DIR),
    )
    progress(10)
    return model


def transcribe_file(
    mp3_path: Path, model=None, diarize: bool = False, summarize: bool = False,
) -> dict:
    """Transcribe a single audio file. Returns result dict for JSON output."""
    from shared.transcript_writer import write_transcript

    mp3_path = mp3_path.resolve()
    basename = mp3_path.stem
    transcript_path = config.RAW_TRANSCRIPTS_DIR / f"{basename}.md"

    state = load_state()
    entry_key = mp3_path.name

    # Mark in_progress
    state["transcriptions"][entry_key] = {
        "status": "in_progress",
        "source_path": str(mp3_path),
        "transcript_path": str(transcript_path),
        "model": config.WHISPER_MODEL,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "duration_s": None,
        "last_error": None,
    }
    save_state(state)

    start_time = time.monotonic()
    try:
        _log(_ET("TRANSCRIPTION_STARTED"), file_path=str(mp3_path),
             metadata={"model": config.WHISPER_MODEL})

        if model is None:
            model = load_whisper_model()

        progress(15)
        result = model.transcribe(
            str(mp3_path),
            language=config.WHISPER_LANGUAGE,
            verbose=False,
        )
        progress(85)

        text = result["text"].strip()

        # Run anti-hallucination filtering
        from shared.whisper_guard import clean_transcript
        text, guard_stats = clean_transcript(text, language=config.WHISPER_LANGUAGE or "en")
        if guard_stats.filters_triggered:
            print(f"Whisper-Guard: filters triggered: {guard_stats.filters_triggered}", file=sys.stderr)
            _log(_ET("WHISPER_GUARD_FILTERED"), file_path=str(mp3_path),
                 metadata={"filters": guard_stats.filters_triggered,
                           "original_lines": guard_stats.original_lines,
                           "final_word_count": guard_stats.final_word_count})
        if guard_stats.is_likely_hallucination:
            print(f"Whisper-Guard: transcript flagged as likely hallucination "
                  f"({guard_stats.final_word_count} words)", file=sys.stderr)

        # Optionally run diarization
        diarized_result = None
        if diarize:
            try:
                from shared.diarize_lite import diarize as run_diarize
                diarized_result = run_diarize(
                    str(mp3_path), text, result.get("segments", [])
                )
            except ImportError:
                print("diarize_lite module not available, skipping diarization", file=sys.stderr)
            except Exception as e:
                print(f"Diarization failed: {e}", file=sys.stderr)

        # Optionally run LLM summarization
        summary = None
        if summarize:
            try:
                from shared.summarize import summarize as run_summarize
                progress(90)
                _log(_ET("SUMMARIZATION_STARTED"), file_path=str(mp3_path))
                summ_start = time.monotonic()
                summary = run_summarize(text)
                _log(_ET("SUMMARIZATION_COMPLETED"), file_path=str(mp3_path),
                     duration_s=round(time.monotonic() - summ_start, 1))
            except Exception as e:
                print(f"Summarization failed (non-fatal): {e}", file=sys.stderr)
                _log(_ET("SUMMARIZATION_FAILED"), file_path=str(mp3_path),
                     status="error", error=str(e))

        # Write transcript with frontmatter
        write_transcript(
            transcript_path,
            text,
            source_path=mp3_path,
            model=config.WHISPER_MODEL,
            diarized_result=diarized_result,
            whisper_segments=result.get("segments", []),
            summary=summary,
        )

        # Always save a segments JSON alongside the transcript so the
        # in-app viewer can show timestamped text (with or without speakers).
        import json as _json
        segments_json_path = transcript_path.with_name(
            transcript_path.stem + "_diarized.json"
        )
        if diarized_result and diarized_result.get("segments"):
            # Diarized — save as-is (already has speaker_id + timestamps)
            _json.loads  # ensure json imported
            segments_json_path.write_text(
                _json.dumps(diarized_result, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        else:
            # Non-diarized — build segments from Whisper output with timestamps
            whisper_segments = result.get("segments", [])
            plain_segments = []
            for seg in whisper_segments:
                plain_segments.append({
                    "start": seg.get("start", 0.0),
                    "end": seg.get("end", 0.0),
                    "text": seg.get("text", "").strip(),
                    "speaker_id": 0,
                    "speaker": "",
                })
            plain_result = {
                "version": 1,
                "audio_file": str(mp3_path),
                "segments": plain_segments,
                "speaker_names": {},
            }
            segments_json_path.write_text(
                _json.dumps(plain_result, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        progress(95)

        duration_s = round(time.monotonic() - start_time, 1)

        # Update state
        state = load_state()
        state["transcriptions"][entry_key] = {
            "status": "completed",
            "source_path": str(mp3_path),
            "transcript_path": str(transcript_path),
            "model": config.WHISPER_MODEL,
            "started_at": state["transcriptions"].get(entry_key, {}).get("started_at"),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "duration_s": duration_s,
            "last_error": None,
        }
        save_state(state)

        _log(_ET("TRANSCRIPTION_COMPLETED"), file_path=str(mp3_path),
             duration_s=duration_s, metadata={"model": config.WHISPER_MODEL,
             "transcript_path": str(transcript_path), "summarized": summary is not None})

        # Run post-transcription hooks (non-fatal)
        try:
            from shared.hooks import run_hooks_pipeline
            hook_results = run_hooks_pipeline(transcript_path, source_path=mp3_path, summary=summary)
            for hook_name, hook_ok in hook_results.items():
                if hook_ok is not None:
                    if hook_ok:
                        _log(_ET("HOOK_EXECUTED"), file_path=str(transcript_path),
                             metadata={"hook": hook_name})
                    else:
                        _log(_ET("HOOK_FAILED"), file_path=str(transcript_path),
                             status="error", metadata={"hook": hook_name})
        except Exception as e:
            print(f"Hooks failed (non-fatal): {e}", file=sys.stderr)
            _log(_ET("HOOK_FAILED"), file_path=str(transcript_path),
                 status="error", error=str(e))

        progress(100)

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
        _log(_ET("TRANSCRIPTION_FAILED"), file_path=str(mp3_path),
             status="error", duration_s=duration_s, error=str(e))
        state = load_state()
        state["transcriptions"][entry_key] = {
            "status": "failed",
            "source_path": str(mp3_path),
            "transcript_path": str(transcript_path),
            "model": config.WHISPER_MODEL,
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


def acquire_lock():
    """Acquire a file lock to prevent concurrent GPU contention."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Another transcription is already running. Waiting for lock...", file=sys.stderr)
        fcntl.flock(lock_file, fcntl.LOCK_EX)
    return lock_file


def release_lock(lock_file):
    """Release the file lock."""
    fcntl.flock(lock_file, fcntl.LOCK_UN)
    lock_file.close()


def cmd_transcribe(args):
    """Transcribe a single file."""
    mp3_path = Path(args.mp3_path).resolve()
    if not mp3_path.exists():
        print(json.dumps({"error": f"File not found: {mp3_path}", "transcribed": False}))
        sys.exit(1)

    lock = acquire_lock()
    try:
        result = transcribe_file(
            mp3_path, diarize=args.diarize, summarize=args.summarize,
        )
    finally:
        release_lock(lock)

    print(json.dumps(result))
    sys.exit(0 if result["transcribed"] else 1)


def cmd_transcribe_batch(args):
    """Transcribe all un-transcribed recordings."""
    recordings_dir = config.RECORDINGS_DIR
    if not recordings_dir.exists():
        print(json.dumps({"error": f"Recordings dir not found: {recordings_dir}", "processed": 0}))
        sys.exit(1)

    state = load_state()
    completed = {
        k for k, v in state.get("transcriptions", {}).items()
        if v.get("status") == "completed"
    }

    # Find all audio files not yet transcribed
    audio_files = sorted(
        f for f in recordings_dir.iterdir()
        if f.suffix.lower() in config.WATCH_EXTENSIONS and f.name not in completed
    )

    if not audio_files:
        print(json.dumps({"message": "All recordings already transcribed", "processed": 0}))
        return

    lock = acquire_lock()
    try:
        model = load_whisper_model()
        results = []
        for i, mp3_path in enumerate(audio_files):
            pct_base = 10 + int(85 * i / len(audio_files))
            progress(pct_base)
            result = transcribe_file(
                mp3_path, model=model, diarize=args.diarize, summarize=args.summarize,
            )
            results.append(result)
    finally:
        release_lock(lock)

    progress(100)
    succeeded = sum(1 for r in results if r["transcribed"])
    print(json.dumps({
        "processed": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "results": results,
    }))


def cmd_status(_args):
    """Print transcription state as JSON."""
    state = load_state()

    # Also check for transcript files that exist on disk
    transcripts_dir = config.RAW_TRANSCRIPTS_DIR
    recordings_dir = config.RECORDINGS_DIR

    # Build lookup: mp3 filename -> transcription info
    lookup = {}
    for key, info in state.get("transcriptions", {}).items():
        lookup[key] = {
            "status": info.get("status", "unknown"),
            "transcript_path": info.get("transcript_path"),
            "transcribed": info.get("status") == "completed",
            "duration_s": info.get("duration_s"),
            "model": info.get("model"),
        }

    # Check for any recordings that have transcripts on disk but aren't in state
    if recordings_dir.exists() and transcripts_dir.exists():
        for mp3 in recordings_dir.iterdir():
            if mp3.suffix.lower() in config.WATCH_EXTENSIONS and mp3.name not in lookup:
                # Check for .md (new) or .txt (legacy)
                txt = transcripts_dir / f"{mp3.stem}.md"
                if not txt.exists():
                    txt = transcripts_dir / f"{mp3.stem}.txt"
                if txt.exists():
                    lookup[mp3.name] = {
                        "status": "completed",
                        "transcript_path": str(txt),
                        "transcribed": True,
                        "duration_s": None,
                        "model": None,
                    }

    print(json.dumps(lookup))


def main():
    parser = argparse.ArgumentParser(description="HiDock Transcription Pipeline")
    sub = parser.add_subparsers(dest="command")

    p_transcribe = sub.add_parser("transcribe", help="Transcribe a single audio file")
    p_transcribe.add_argument("mp3_path", help="Path to audio file")
    p_transcribe.add_argument("--diarize", action="store_true", help="Enable speaker diarization")
    p_transcribe.add_argument("--summarize", action="store_true", help="Summarize with LLM after transcription")
    p_transcribe.set_defaults(func=cmd_transcribe)

    p_batch = sub.add_parser("transcribe-batch", help="Transcribe all un-transcribed recordings")
    p_batch.add_argument("--diarize", action="store_true", help="Enable speaker diarization")
    p_batch.add_argument("--summarize", action="store_true", help="Summarize with LLM after transcription")
    p_batch.set_defaults(func=cmd_transcribe_batch)

    p_status = sub.add_parser("status", help="JSON report of transcription state")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
