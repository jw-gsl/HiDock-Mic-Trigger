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
import signal
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

# Tracks the currently in-flight transcription so the SIGTERM handler can
# flip its state from "in_progress" to "failed" before the process exits.
# Without this, a timeout kill leaves state stuck in "in_progress" and the
# app can never re-queue the recording. Set by transcribe_file, cleared on
# completion.
_IN_FLIGHT: dict[str, str] | None = None


def _sigterm_handler(signum, frame):
    """Mark the in-flight transcription as failed before exiting.

    Called when the parent process (Swift app) terminates us due to timeout.
    Updating the state here means a re-queue from the UI will actually run,
    instead of seeing stale 'in_progress' and either skipping or deadlocking.
    """
    global _IN_FLIGHT
    try:
        if _IN_FLIGHT is not None:
            state = load_state()
            key = _IN_FLIGHT["key"]
            existing = state["transcriptions"].get(key, {})
            state["transcriptions"][key] = {
                **existing,
                "status": "failed",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "last_error": f"Terminated by signal {signum} (likely timeout)",
            }
            save_state(state)
    except Exception:
        pass
    sys.exit(128 + signum)


signal.signal(signal.SIGTERM, _sigterm_handler)
signal.signal(signal.SIGINT, _sigterm_handler)


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


def stage(current: int, total: int, label: str = "") -> None:
    """Emit a STAGE line on stderr for stage-based progress."""
    print(f"STAGE:{current}/{total}:{label}", file=sys.stderr, flush=True)


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


def _word_timestamps_safe(model) -> bool:
    """Whether it's safe to pass ``word_timestamps=True`` to this Whisper
    model. Whisper's per-word alignment runs DTW on the cross-attention
    matrix in float64, and Apple Silicon MPS doesn't support float64
    ("Cannot convert a MPS Tensor to float64 dtype"). Returns False when
    the model is on MPS so transcription succeeds; the diarizer then
    falls back to segment-level alignment (which is what existing
    cached `_whisper.json` files already use)."""
    try:
        import torch
        device = getattr(model, "device", None)
        if device is None:
            return True
        # `device` is a torch.device on a loaded Whisper model.
        if isinstance(device, torch.device):
            return device.type != "mps"
        # Defensive: some paths set a string.
        return "mps" not in str(device).lower()
    except Exception:
        return True


def transcribe_file(
    mp3_path: Path, model=None, diarize: bool = False, summarize: bool = False,
    n_speakers: int | None = None,
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

    # Register with the SIGTERM handler so state flips to "failed" instead of
    # staying stuck at "in_progress" if the parent app kills us via timeout.
    global _IN_FLIGHT
    _IN_FLIGHT = {"key": entry_key}

    start_time = time.monotonic()
    try:
        _log(_ET("TRANSCRIPTION_STARTED"), file_path=str(mp3_path),
             metadata={"model": config.WHISPER_MODEL})

        total_stages = 5 if diarize else 4
        stage(1, total_stages, "Loading model")
        # Decide ASR backend from pipeline_backends.json. Whisper keeps
        # the Whisper-specific pre (silence stripping) + post
        # (whisper-guard) passes. Parakeet bypasses both — its output
        # doesn't hallucinate in the same way Whisper does on silence,
        # so those passes would at best be no-ops and at worst mangle
        # the output.
        try:
            from shared.pipeline_dispatch import active_pipeline
            _backends = active_pipeline()
        except Exception:
            _backends = {"transcription": "whisper"}
        asr_backend = _backends.get("transcription", "whisper")
        print(f"Transcription backend: {asr_backend}", file=sys.stderr)

        if asr_backend == "whisper" and model is None:
            model = load_whisper_model()

        # Preprocess: strip long silence to prevent Whisper hallucination
        # loops. Skip for Parakeet — it doesn't suffer from the same
        # failure mode and we don't want to introduce one by rewriting
        # the audio.
        stage(2, total_stages, "Transcribing")
        transcribe_path = str(mp3_path)
        if asr_backend == "whisper":
            try:
                from shared.diarize_lite import _replace_silence_with_padding
                from shared.audio_utils import load_audio
                import soundfile as sf
                import tempfile
                raw_audio = load_audio(str(mp3_path), sr=16000)
                processed = _replace_silence_with_padding(raw_audio, sr=16000)
                if len(processed) < len(raw_audio) * 0.95:  # Only use if >5% was stripped
                    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                    sf.write(tmp.name, processed, 16000)
                    transcribe_path = tmp.name
                    print(f"Silence stripped: {len(raw_audio)/16000:.0f}s → {len(processed)/16000:.0f}s", file=sys.stderr)
            except Exception as e:
                print(f"Silence stripping skipped: {e}", file=sys.stderr)

        progress(15)
        if asr_backend == "parakeet":
            # Route through the dispatcher adapter. Result shape matches
            # Whisper's: {"text", "segments": [{start, end, text}]}.
            try:
                from shared.pipeline_dispatch import transcribe_audio as dispatched_transcribe
                result = dispatched_transcribe(transcribe_path, language=config.WHISPER_LANGUAGE)
                # Track which model was actually used so the state file
                # and the frontmatter reflect reality.
                active_model_name = "parakeet-tdt-0.6b-v2"
            except ModuleNotFoundError as e:
                # Parakeet package isn't installed yet — fall back to
                # Whisper so the user still gets output rather than a
                # hard error.
                print(
                    f"Parakeet unavailable ({e}); falling back to Whisper. "
                    "Install parakeet-mlx via the Model Manager to use it.",
                    file=sys.stderr,
                )
                if model is None:
                    model = load_whisper_model()
                result = model.transcribe(
                    transcribe_path,
                    language=config.WHISPER_LANGUAGE,
                    verbose=False,
                    word_timestamps=_word_timestamps_safe(model),
                )
                active_model_name = config.WHISPER_MODEL
        else:
            # word_timestamps lets the diarizer do per-word speaker
            # alignment — see _word_timestamps_safe for the MPS caveat.
            result = model.transcribe(
                transcribe_path,
                language=config.WHISPER_LANGUAGE,
                verbose=False,
                word_timestamps=_word_timestamps_safe(model),
            )
            active_model_name = config.WHISPER_MODEL
        progress(85)

        # Clean up temp file
        if transcribe_path != str(mp3_path):
            try:
                Path(transcribe_path).unlink()
            except OSError:
                pass

        text = result["text"].strip()

        # Whisper-specific post-processing — whisper-guard filters out
        # the specific hallucination patterns Whisper produces ("Thank
        # you for watching", repeated "you you you", etc.). Parakeet
        # doesn't exhibit these and running the filter could strip
        # real content, so skip it.
        if asr_backend == "whisper":
            from shared.whisper_guard import clean_transcript
            text, guard_stats = clean_transcript(text, language=config.WHISPER_LANGUAGE or "en")
        else:
            # Stub stats object so downstream code that reads
            # guard_stats doesn't crash. No filters triggered.
            from types import SimpleNamespace
            guard_stats = SimpleNamespace(
                filters_triggered=[],
                original_lines=0,
                final_word_count=len(text.split()),
                is_likely_hallucination=False,
            )
        if guard_stats.filters_triggered:
            print(f"Whisper-Guard: filters triggered: {guard_stats.filters_triggered}", file=sys.stderr)
            _log(_ET("WHISPER_GUARD_FILTERED"), file_path=str(mp3_path),
                 metadata={"filters": guard_stats.filters_triggered,
                           "original_lines": guard_stats.original_lines,
                           "final_word_count": guard_stats.final_word_count})
        if guard_stats.is_likely_hallucination:
            print(f"Whisper-Guard: transcript flagged as likely hallucination "
                  f"({guard_stats.final_word_count} words)", file=sys.stderr)

        stage(3, total_stages, "Applying corrections")
        # Apply local corrections dictionary (e.g. "volaris" → "VOLARIS")
        try:
            from shared.corrections import apply_corrections
            text = apply_corrections(text)
            # Also apply to individual segments
            for seg in result.get("segments", []):
                if "text" in seg:
                    seg["text"] = apply_corrections(seg["text"])
        except ImportError:
            pass

        # Optionally run diarization. Route through the pipeline
        # dispatcher so the user's active selection (lite or
        # sortformer) is honoured. The dispatcher handles the
        # fallback to lite if the selected backend is missing deps.
        diarized_result = None
        if diarize:
            stage(4, total_stages, "Diarizing speakers")
            try:
                from shared.pipeline_dispatch import diarize as run_diarize, active_pipeline
                backends = active_pipeline()
                print(f"Diarization backend: {backends.get('diarization', 'lite')}", file=sys.stderr)
                diarized_result = run_diarize(
                    str(mp3_path), result.get("segments", []),
                    n_speakers=n_speakers,
                )
            except ModuleNotFoundError as e:
                # Selected backend's pip package missing — keep going
                # with the transcript even if diarization fails, so the
                # user still gets something useful.
                print(f"Diarization backend unavailable: {e}", file=sys.stderr)
            except ImportError:
                print("diarization dispatcher not available, skipping diarization", file=sys.stderr)
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

        stage(total_stages, total_stages, "Writing output")
        # Write transcript with frontmatter — pass the actual ASR model
        # used so the .md frontmatter and state.json reflect the real
        # backend (Whisper or Parakeet), not always "whisper".
        write_transcript(
            transcript_path,
            text,
            source_path=mp3_path,
            model=active_model_name,
            diarized_result=diarized_result,
            whisper_segments=result.get("segments", []),
            summary=summary,
        )

        # Emit a paired .srt alongside the .md whenever we have timed segments.
        # Prefer diarized output (speaker labels in cues) and fall back to the
        # plain Whisper segments when diarization is off or failed. Writing is
        # a no-op if there are no timings at all.
        try:
            from shared.srt_writer import srt_path_for, write_srt
            write_srt(
                srt_path_for(transcript_path),
                diarized_result=diarized_result,
                whisper_segments=result.get("segments", []) if not diarized_result else None,
            )
        except Exception as e:
            # SRT is a best-effort sidecar; never let it break the main transcript.
            print(f"SRT export failed (non-fatal): {e}", file=sys.stderr)

        # Save the original Whisper micro-segments (for re-diarization)
        import json as _json
        whisper_raw_path = transcript_path.with_name(
            transcript_path.stem + "_whisper.json"
        )
        whisper_raw_segs = [
            {"start": seg.get("start", 0.0), "end": seg.get("end", 0.0),
             "text": seg.get("text", "").strip()}
            for seg in result.get("segments", [])
        ]
        whisper_raw_path.write_text(
            _json.dumps({"audio_file": str(mp3_path), "segments": whisper_raw_segs},
                        indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Save the diarized/timestamped JSON for the in-app viewer
        segments_json_path = transcript_path.with_name(
            transcript_path.stem + "_diarized.json"
        )
        if diarized_result and diarized_result.get("segments"):
            segments_json_path.write_text(
                _json.dumps(diarized_result, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        else:
            plain_segments = [
                {**ws, "speaker_id": 0, "speaker": ""} for ws in whisper_raw_segs
            ]
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

        # Update state — record the ACTUAL backend used, not the
        # hardcoded Whisper model name, so state.json is accurate.
        state = load_state()
        state["transcriptions"][entry_key] = {
            "status": "completed",
            "source_path": str(mp3_path),
            "transcript_path": str(transcript_path),
            "model": active_model_name,
            "started_at": state["transcriptions"].get(entry_key, {}).get("started_at"),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "duration_s": duration_s,
            "last_error": None,
        }
        save_state(state)

        _log(_ET("TRANSCRIPTION_COMPLETED"), file_path=str(mp3_path),
             duration_s=duration_s, metadata={"model": active_model_name,
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

        _IN_FLIGHT = None
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

        _IN_FLIGHT = None
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
            n_speakers=getattr(args, "n_speakers", None),
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
                n_speakers=getattr(args, "n_speakers", None),
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

    # Build lookup: mp3 filename -> transcription info.
    # Verify the transcript file still exists on disk before reporting
    # `transcribed: True`. Without this check, removing a recording in
    # the Mac app deletes the .md/.srt/.json files but leaves this
    # state.json entry stuck at status="completed", so the desktop UI
    # keeps flipping the row back to "Transcribed" — overriding the
    # "Removed" status the user just set. This guard reports the row
    # as not-transcribed once the artifact's gone.
    lookup = {}
    stale_keys: list[str] = []
    for key, info in state.get("transcriptions", {}).items():
        completed = info.get("status") == "completed"
        transcript_path = info.get("transcript_path")
        path_present = bool(transcript_path) and Path(transcript_path).exists()
        is_transcribed = completed and path_present
        if completed and not path_present:
            stale_keys.append(key)
        lookup[key] = {
            "status": info.get("status", "unknown"),
            "transcript_path": transcript_path,
            "transcribed": is_transcribed,
            "duration_s": info.get("duration_s"),
            "model": info.get("model"),
        }
    # Opportunistically prune state entries whose transcript files have
    # been deleted out-of-band (most often by the Mac app's Remove
    # action). Keeps state.json from growing forever with stale rows
    # and prevents repeat cmd_status calls re-checking the same gone
    # files. Best-effort — failure is non-fatal because the in-memory
    # `lookup` already reflects truth for this call.
    if stale_keys:
        transcriptions = state.get("transcriptions", {})
        for k in stale_keys:
            transcriptions.pop(k, None)
        try:
            save_state(state)
        except Exception as exc:
            print(f"WARN: could not prune {len(stale_keys)} stale entries: {exc}", file=sys.stderr)

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


def cmd_rediarize(args):
    """Re-run speaker diarization on an existing transcript without re-transcribing."""
    import json as _json

    json_path = Path(args.json_path).resolve()
    if not json_path.exists():
        print(f"File not found: {json_path}", file=sys.stderr)
        sys.exit(1)

    data = _json.loads(json_path.read_text(encoding="utf-8"))
    audio_path = data.get("audio_file", "")
    if not Path(audio_path).exists():
        print(f"Audio file not found: {audio_path}", file=sys.stderr)
        sys.exit(1)

    # Try to load the original Whisper micro-segments for better diarization
    whisper_raw_path = json_path.with_name(
        json_path.stem.replace("_diarized", "_whisper") + ".json"
    )
    if whisper_raw_path.exists():
        whisper_data = _json.loads(whisper_raw_path.read_text(encoding="utf-8"))
        segments = whisper_data.get("segments", [])
        print(f"Using original Whisper segments: {len(segments)}", file=sys.stderr)
    else:
        segments = data.get("segments", [])
        print(f"No _whisper.json found, using existing {len(segments)} segments", file=sys.stderr)
    progress(5)

    # Route through the dispatcher so rediarize respects the active
    # backend from pipeline_backends.json (lite vs sortformer). Without
    # this, flipping the user's backend choice had no effect on this
    # command path.
    from shared.pipeline_dispatch import diarize as run_diarize
    progress(10)

    n_speakers = args.n_speakers if hasattr(args, "n_speakers") else None
    diarized_result = run_diarize(audio_path, segments, n_speakers=n_speakers)
    progress(90)

    # Apply corrections
    try:
        from shared.corrections import apply_corrections
        for seg in diarized_result.get("segments", []):
            if "text" in seg:
                seg["text"] = apply_corrections(seg["text"])
    except ImportError:
        pass

    # Save back
    json_path.write_text(
        _json.dumps(diarized_result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    progress(100)

    n = len(diarized_result.get("segments", []))
    speakers = diarized_result.get("speaker_names", {})
    print(_json.dumps({
        "status": "completed",
        "segments": n,
        "speakers": len(speakers),
        "speaker_names": speakers,
    }))


def cmd_recluster_with_anchors(args):
    """Layer 2 of the voice-training plan. Reads a _diarized.json,
    treats every user-named segment as an anchor centroid, and
    re-assigns the rest of the transcript to its closest anchor by
    cosine similarity. The action is idempotent — running it twice in
    a row is a no-op when nothing has changed."""
    import json as _json
    json_path = Path(args.json_path).resolve()
    if not json_path.exists():
        print(f"File not found: {json_path}", file=sys.stderr)
        sys.exit(1)
    progress(5)
    from shared.recluster_with_anchors import recluster_with_anchors, SIMILARITY_THRESHOLD
    threshold = args.threshold if args.threshold is not None else SIMILARITY_THRESHOLD
    progress(10)
    summary = recluster_with_anchors(json_path, similarity_threshold=threshold)
    progress(95)
    if "error" in summary:
        print(_json.dumps(summary), file=sys.stderr)
        sys.exit(1)

    # Re-render the .md so the user sees the new assignments without
    # having to re-open the viewer manually. Mirrors what cmd_rediarize
    # does — read the diarized JSON we just wrote, regenerate the
    # transcript file alongside it.
    try:
        data = _json.loads(json_path.read_text(encoding="utf-8"))
        from shared.transcript_writer import write_transcript
        md_path = json_path.with_name(json_path.stem.replace("_diarized", "") + ".md")
        body_text = " ".join(
            seg.get("text", "").strip()
            for seg in data.get("segments", [])
            if seg.get("text")
        )
        write_transcript(
            md_path,
            body_text,
            source_path=Path(data.get("audio_file", "")),
            model="recluster-with-anchors",
            diarized_result=data,
        )
    except Exception as exc:
        print(f"WARN: could not refresh .md: {exc}", file=sys.stderr)

    progress(100)
    print(_json.dumps(summary))


def cmd_merge_rediarize(args):
    """Build a merged transcript from existing per-piece transcripts.

    Stitches each piece's `_whisper.json` segments together with cumulative
    timestamp offsets, then re-runs only diarization (and optionally
    summarisation) on the merged audio. ~10–15 min faster than a fresh
    Whisper pass on a 60-min merge for identical text output, because
    Whisper's word-level output is essentially deterministic on the same
    audio. Cross-segment speaker continuity is the only thing the merge
    actually needs that the per-piece outputs don't already have, and
    that's a diarization-only concern.

    Args expected:
        merged_audio: Path to the ffmpeg-merged mp3
        pieces: ordered list of piece mp3 paths
        --summarize: run LLM summary over the merged text
        --n-speakers: hint for diarization
    """
    import json as _json
    from datetime import datetime, timezone

    merged_audio = Path(args.merged_audio).resolve()
    if not merged_audio.exists():
        print(f"Merged audio not found: {merged_audio}", file=sys.stderr)
        sys.exit(1)
    pieces = [Path(p).resolve() for p in args.pieces]
    if len(pieces) < 2:
        print("merge-rediarize needs at least two pieces", file=sys.stderr)
        sys.exit(1)

    transcripts_dir = config.RAW_TRANSCRIPTS_DIR
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    # Compute per-piece audio duration via mutagen (authoritative). We use
    # this for the cumulative offset, NOT the whisper.json's last segment
    # end (which can fall short of the file's true duration when the
    # closing audio is silent). Without this, piece-2 timestamps would
    # land a few seconds early relative to the merged audio.
    from mutagen.mp3 import MP3
    durations: list[float] = []
    for p in pieces:
        try:
            durations.append(float(MP3(str(p)).info.length))
        except Exception as exc:
            print(f"Could not read duration of {p}: {exc}", file=sys.stderr)
            sys.exit(1)

    # Stitch whisper.json segments with cumulative offsets.
    stitched_segments: list[dict] = []
    cumulative_offset = 0.0
    for piece_path, piece_dur in zip(pieces, durations):
        wjson = transcripts_dir / f"{piece_path.stem}_whisper.json"
        if not wjson.exists():
            print(f"Missing per-piece whisper JSON for {piece_path.name}: {wjson}", file=sys.stderr)
            sys.exit(1)
        try:
            wdata = _json.loads(wjson.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"Failed to parse {wjson}: {exc}", file=sys.stderr)
            sys.exit(1)
        for seg in wdata.get("segments", []):
            stitched_segments.append({
                "start": float(seg.get("start", 0.0)) + cumulative_offset,
                "end": float(seg.get("end", 0.0)) + cumulative_offset,
                "text": (seg.get("text", "") or "").strip(),
            })
        cumulative_offset += piece_dur

    if not stitched_segments:
        print("Stitched whisper segments are empty — refusing to merge", file=sys.stderr)
        sys.exit(1)

    progress(10)

    # Persist the stitched whisper.json so a future rediarize can reuse it.
    merged_stem = merged_audio.stem
    merged_md = transcripts_dir / f"{merged_stem}.md"
    merged_whisper_json = transcripts_dir / f"{merged_stem}_whisper.json"
    merged_diarized_json = transcripts_dir / f"{merged_stem}_diarized.json"
    merged_whisper_json.write_text(
        _json.dumps({"audio_file": str(merged_audio), "segments": stitched_segments},
                    indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    progress(20)

    # Run only the diarization stage on the merged audio. Use the
    # pipeline_dispatch helper so this honours the user's selected
    # backend (lite / sortformer) — same as cmd_transcribe.
    from shared.pipeline_dispatch import diarize as run_diarize
    n_speakers = getattr(args, "n_speakers", None)
    diarized_result = run_diarize(str(merged_audio), stitched_segments, n_speakers=n_speakers)
    progress(70)

    # Apply text corrections on diarized segments (matches cmd_transcribe).
    try:
        from shared.corrections import apply_corrections
        for seg in diarized_result.get("segments", []):
            if "text" in seg:
                seg["text"] = apply_corrections(seg["text"])
    except ImportError:
        pass

    diarized_result["audio_file"] = str(merged_audio)

    # Build the plain-text transcript that goes into the .md body and
    # feeds the optional LLM summary. Use diarized segments if we got
    # them, else fall back to stitched whisper.
    body_segments = diarized_result.get("segments") or stitched_segments
    body_text = " ".join(seg.get("text", "").strip() for seg in body_segments if seg.get("text"))

    # Optional summarisation. Re-running this is cheap (one LLM call ~30s),
    # and a merged meeting deserves its own coherent summary instead of
    # whichever piece's summary happened to be longest.
    summary = None
    if getattr(args, "summarize", False):
        try:
            from shared.summarize import summarize as run_summarize
            progress(80)
            summary = run_summarize(body_text)
        except Exception as exc:
            print(f"Summarization failed (non-fatal): {exc}", file=sys.stderr)

    # Write merged outputs (.md + .srt + _diarized.json + _whisper.json
    # already saved above). Mirrors cmd_transcribe's tail.
    from shared.transcript_writer import write_transcript
    write_transcript(
        merged_md,
        body_text,
        source_path=merged_audio,
        model="merge-rediarize",
        diarized_result=diarized_result,
        whisper_segments=stitched_segments,
        summary=summary,
    )
    try:
        from shared.srt_writer import srt_path_for, write_srt
        write_srt(
            srt_path_for(merged_md),
            diarized_result=diarized_result,
            whisper_segments=stitched_segments if not diarized_result else None,
        )
    except Exception as exc:
        print(f"SRT export failed (non-fatal): {exc}", file=sys.stderr)

    if diarized_result and diarized_result.get("segments"):
        merged_diarized_json.write_text(
            _json.dumps(diarized_result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # Mark the merged file as transcribed in state.json so cmd_status
    # reports it correctly and the desktop UI flips its row to
    # "Transcribed".
    state = load_state()
    state.setdefault("transcriptions", {})[merged_audio.name] = {
        "status": "completed",
        "transcript_path": str(merged_md),
        "duration_s": None,
        "model": "merge-rediarize",
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        save_state(state)
    except Exception as exc:
        print(f"WARN: could not persist transcription state: {exc}", file=sys.stderr)

    progress(100)
    n_segs = len(diarized_result.get("segments", [])) if diarized_result else len(stitched_segments)
    speakers = diarized_result.get("speaker_names", {}) if diarized_result else {}
    print(_json.dumps({
        "transcribed": True,
        "transcript_path": str(merged_md),
        "segments": n_segs,
        "speakers": len(speakers),
        "speaker_names": speakers,
        "method": "merge-rediarize",
    }))


def main():
    parser = argparse.ArgumentParser(description="HiDock Transcription Pipeline")
    sub = parser.add_subparsers(dest="command")

    p_transcribe = sub.add_parser("transcribe", help="Transcribe a single audio file")
    p_transcribe.add_argument("mp3_path", help="Path to audio file")
    p_transcribe.add_argument("--diarize", action="store_true", help="Enable speaker diarization")
    p_transcribe.add_argument("--summarize", action="store_true", help="Summarize with LLM after transcription")
    p_transcribe.add_argument("--n-speakers", type=int, help="Hint: expected number of speakers (improves diarization accuracy)")
    p_transcribe.set_defaults(func=cmd_transcribe)

    p_batch = sub.add_parser("transcribe-batch", help="Transcribe all un-transcribed recordings")
    p_batch.add_argument("--diarize", action="store_true", help="Enable speaker diarization")
    p_batch.add_argument("--summarize", action="store_true", help="Summarize with LLM after transcription")
    p_batch.add_argument("--n-speakers", type=int, help="Hint: expected number of speakers (improves diarization accuracy)")
    p_batch.set_defaults(func=cmd_transcribe_batch)

    p_rediarize = sub.add_parser("rediarize", help="Re-run speaker diarization without re-transcribing")
    p_rediarize.add_argument("json_path", help="Path to _diarized.json file")
    p_rediarize.add_argument("--n-speakers", type=int, help="Force number of speakers")
    p_rediarize.set_defaults(func=cmd_rediarize)

    p_recluster = sub.add_parser(
        "recluster-with-anchors",
        help="Re-cluster transcript using user-named segments as anchors (Layer 2 of voice-training plan)",
    )
    p_recluster.add_argument("json_path", help="Path to _diarized.json file")
    p_recluster.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Cosine-similarity threshold (0-1). Below this, segments keep their existing speaker. Defaults to the conservative value tuned in shared.recluster_with_anchors.",
    )
    p_recluster.set_defaults(func=cmd_recluster_with_anchors)

    p_merge = sub.add_parser(
        "merge-rediarize",
        help="Build merged transcript from per-piece whisper.json + re-run diarization (no Whisper pass)",
    )
    p_merge.add_argument("merged_audio", help="Path to merged mp3 (output of ffmpeg concat)")
    p_merge.add_argument("--pieces", nargs="+", required=True, help="Ordered list of piece mp3 paths")
    p_merge.add_argument("--summarize", action="store_true", help="Re-run LLM summary on stitched text")
    p_merge.add_argument("--n-speakers", type=int, help="Hint: expected number of speakers")
    p_merge.set_defaults(func=cmd_merge_rediarize)

    p_status = sub.add_parser("status", help="JSON report of transcription state")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
