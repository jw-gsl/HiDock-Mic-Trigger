#!/usr/bin/env python3
"""HiDock transcription pipeline — Parakeet TDT (MLX) variant.

Drop-in alternative to transcribe.py for English recordings on Apple Silicon.
Uses NVIDIA Parakeet TDT 0.6B v2 via parakeet-mlx, which runs on the Apple
GPU through MLX at roughly 60× real-time — a 6-hour recording finishes in
about 6 minutes instead of the ~90 min Whisper would take.

Emits the same JSON contract as transcribe.py so the rest of the pipeline
(Whisper-Guard, diarization, summarisation, state.json, hooks) works unchanged.

Limitations:
  - English only. Use transcribe.py (Whisper) for non-English.
  - Apple Silicon only (MLX dependency).
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

# Route SSL verification through the macOS/Linux system trust store so the
# first-run HuggingFace download works. Python 3.13 + OpenSSL 3.5 is strict
# about X.509 extensions and rejects some HF CDN certs with the certifi
# bundle. truststore falls back to the OS keychain which accepts them.
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass  # truststore is optional; fall back to default SSL if absent

import config
from state import load_state, save_state

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

LOCK_PATH = Path(config.HIDOCK_ROOT) / "transcription-pipeline" / ".transcribe.lock"
PARAKEET_MODEL_ID = "mlx-community/parakeet-tdt-0.6b-v2"

_IN_FLIGHT: dict[str, str] | None = None


def _sigterm_handler(signum, frame):
    """Flip in-progress state to failed on parent-initiated termination."""
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


def progress(pct: int) -> None:
    """Emit a PROGRESS line on stderr (matches transcribe.py — the Swift
    parser reads stderr and expects 'PROGRESS:{pct}' with no space)."""
    print(f"PROGRESS:{pct}", file=sys.stderr, flush=True)


def stage(current: int, total: int, label: str = "") -> None:
    """Emit a STAGE line on stderr ('STAGE:{cur}/{total}:{label}')."""
    print(f"STAGE:{current}/{total}:{label}", file=sys.stderr, flush=True)


def _load_parakeet():
    """Load the Parakeet MLX model, cached by MLX after first call."""
    from parakeet_mlx import from_pretrained
    return from_pretrained(PARAKEET_MODEL_ID)


def _parakeet_result_to_segments(result) -> list[dict]:
    """Convert parakeet-mlx AlignedSentence objects to the shape used by our
    pipeline (matching OpenAI Whisper's segments output: start/end/text)."""
    segments = []
    for sent in result.sentences:
        segments.append({
            "start": float(sent.start),
            "end": float(sent.end),
            "text": sent.text,
        })
    return segments


def transcribe_file(
    mp3_path: Path, model=None, diarize: bool = False, summarize: bool = False,
    n_speakers: int | None = None,
) -> dict:
    from shared.transcript_writer import write_transcript

    mp3_path = mp3_path.resolve()
    basename = mp3_path.stem
    transcript_path = config.RAW_TRANSCRIPTS_DIR / f"{basename}.md"

    state = load_state()
    entry_key = mp3_path.name

    state["transcriptions"][entry_key] = {
        "status": "in_progress",
        "source_path": str(mp3_path),
        "transcript_path": str(transcript_path),
        "model": PARAKEET_MODEL_ID,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "duration_s": None,
        "last_error": None,
    }
    save_state(state)

    global _IN_FLIGHT
    _IN_FLIGHT = {"key": entry_key}

    start_time = time.monotonic()
    strip_map = None            # stripped→original time map (set when silence stripped)
    tmp_strip_path: str | None = None  # temp WAV holding the stripped audio
    try:
        total_stages = 5 if diarize else 4
        stage(1, total_stages, "Loading model")
        if model is None:
            model = _load_parakeet()

        # Silence stripping (same prep step as Whisper path). Parakeet handles
        # silence more gracefully than Whisper but skipping dead air still
        # saves wall-clock.
        stage(2, total_stages, "Transcribing")
        transcribe_path = str(mp3_path)
        import tempfile
        try:
            from shared.audio_utils import load_audio
            from shared.diarize_lite import strip_silence_with_map
            import soundfile as sf
            raw_audio = load_audio(str(mp3_path), sr=16000)
            processed, candidate_map = strip_silence_with_map(raw_audio, sr=16000)
            if len(processed) < len(raw_audio) * 0.95:
                tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                tmp.close()
                sf.write(tmp.name, processed, 16000)
                tmp_strip_path = tmp.name
                transcribe_path = tmp.name
                strip_map = candidate_map
                print(
                    f"Silence stripped: {len(raw_audio) / 16000:.0f}s → "
                    f"{len(processed) / 16000:.0f}s",
                    file=sys.stderr,
                )
        except Exception as e:
            print(f"Silence stripping skipped: {e}", file=sys.stderr)

        progress(15)
        result = model.transcribe(transcribe_path)
        progress(85)

        if tmp_strip_path is not None:
            try:
                Path(tmp_strip_path).unlink()
            except OSError:
                pass
            tmp_strip_path = None

        text = result.text.strip()
        segments = _parakeet_result_to_segments(result)

        # ASR ran on the silence-stripped timeline; remap the timestamps back
        # to the original audio before diarization / sidecar writing, both of
        # which reference the original file.
        if strip_map is not None:
            from shared.diarize_lite import remap_segments
            remap_segments(segments, strip_map)

        # Whisper-Guard still applies — Parakeet's hallucination modes are
        # different but dedup/noise-phrase filtering remains useful.
        from shared.whisper_guard import clean_transcript
        text, guard_stats = clean_transcript(text, language="en")
        if guard_stats.filters_triggered:
            print(
                f"Whisper-Guard: filters triggered: {guard_stats.filters_triggered}",
                file=sys.stderr,
            )

        stage(3, total_stages, "Applying corrections")
        try:
            from shared.corrections import apply_corrections
            text = apply_corrections(text)
            for seg in segments:
                seg["text"] = apply_corrections(seg["text"])
        except ImportError:
            pass

        diarized_result = None
        if diarize:
            stage(4, total_stages, "Diarizing speakers")
            try:
                from shared.diarize_lite import diarize as run_diarize
                diarized_result = run_diarize(
                    str(mp3_path), segments, n_speakers=n_speakers,
                )
            except Exception as e:
                print(f"Diarization failed: {e}", file=sys.stderr)

        summary = None
        if summarize:
            try:
                from shared.summarize import summarize as run_summarize
                progress(90)
                summary = run_summarize(text)
            except Exception as e:
                print(f"Summarization failed (non-fatal): {e}", file=sys.stderr)

        stage(total_stages, total_stages, "Writing transcript")

        import json as _json
        write_transcript(
            output_path=transcript_path,
            transcript_text=text,
            source_path=mp3_path,
            model=PARAKEET_MODEL_ID,
            diarized_result=diarized_result,
            whisper_segments=segments,
            summary=summary,
        )

        whisper_raw_path = transcript_path.with_name(
            transcript_path.stem + "_whisper.json"
        )
        whisper_raw_path.write_text(
            _json.dumps({"audio_file": str(mp3_path), "segments": segments},
                        indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        segments_json_path = transcript_path.with_name(
            transcript_path.stem + "_diarized.json"
        )
        if diarized_result is not None:
            segments_json_path.write_text(
                _json.dumps(diarized_result, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        else:
            plain_segments = [
                {**s, "speaker_id": 0, "speaker": ""} for s in segments
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

        state = load_state()
        state["transcriptions"][entry_key] = {
            "status": "completed",
            "source_path": str(mp3_path),
            "transcript_path": str(transcript_path),
            "model": PARAKEET_MODEL_ID,
            "started_at": state["transcriptions"].get(entry_key, {}).get("started_at"),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "duration_s": duration_s,
            "last_error": None,
        }
        save_state(state)

        # Post-transcription hooks
        try:
            from shared.hooks import run_hooks_pipeline
            run_hooks_pipeline(transcript_path, source_path=mp3_path, summary=summary)
        except Exception as e:
            print(f"Hooks failed (non-fatal): {e}", file=sys.stderr)

        progress(100)

        _IN_FLIGHT = None
        return {
            "file": str(mp3_path),
            "transcript_path": str(transcript_path),
            "duration_s": duration_s,
            "status": "completed",
            "transcribed": True,
            "summarized": summary is not None,
            "backend": "parakeet-mlx",
        }

    except Exception as e:
        duration_s = round(time.monotonic() - start_time, 1)
        state = load_state()
        state["transcriptions"][entry_key] = {
            "status": "failed",
            "source_path": str(mp3_path),
            "transcript_path": str(transcript_path),
            "model": PARAKEET_MODEL_ID,
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
            "backend": "parakeet-mlx",
        }

    finally:
        # Never leak the stripped temp WAV — failed runs previously left it
        # behind (~115 MB per hour-long recording).
        if tmp_strip_path is not None:
            try:
                Path(tmp_strip_path).unlink()
            except OSError:
                pass


def acquire_lock():
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Another transcription is already running. Waiting for lock...", file=sys.stderr)
        fcntl.flock(lock_file, fcntl.LOCK_EX)
    return lock_file


def release_lock(lock_file):
    fcntl.flock(lock_file, fcntl.LOCK_UN)
    lock_file.close()


def cmd_transcribe(args):
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


def main():
    parser = argparse.ArgumentParser(description="HiDock Transcription Pipeline (Parakeet TDT via MLX)")
    sub = parser.add_subparsers(dest="command")

    p_transcribe = sub.add_parser("transcribe", help="Transcribe a single audio file")
    p_transcribe.add_argument("mp3_path", help="Path to audio file")
    p_transcribe.add_argument("--diarize", action="store_true", help="Enable speaker diarization")
    p_transcribe.add_argument("--summarize", action="store_true", help="Summarize with LLM after transcription")
    p_transcribe.add_argument("--n-speakers", type=int, help="Hint: expected number of speakers")
    p_transcribe.set_defaults(func=cmd_transcribe)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
