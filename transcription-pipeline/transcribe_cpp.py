#!/usr/bin/env python3
"""HiDock transcription pipeline — whisper.cpp variant.

Drop-in replacement for transcribe.py that uses pywhispercpp instead of
openai-whisper + PyTorch. Same CLI interface, same JSON protocol.

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

# Add the repo root to sys.path so shared modules are importable
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import config  # noqa: E402
from state import load_state, save_state  # noqa: E402

LOCK_PATH = Path(config.HIDOCK_ROOT) / "transcription-pipeline" / ".transcribe.lock"

# whisper.cpp GGML model settings
GGML_MODEL_FILENAME = "ggml-large-v3-turbo-q5_0.bin"
GGML_MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin"

_model = None


def progress(pct: int) -> None:
    """Emit a PROGRESS line on stderr (matches extractor protocol)."""
    print(f"PROGRESS:{pct}", file=sys.stderr, flush=True)


def model_path() -> Path:
    return config.MODELS_DIR / GGML_MODEL_FILENAME


def model_ready() -> bool:
    p = model_path()
    return p.exists() and p.stat().st_size > 1_000_000


def download_model_if_needed() -> None:
    """Download the GGML model if not present."""
    if model_ready():
        return

    import ssl
    import urllib.request

    dest = model_path()
    config.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".downloading")

    print(f"Downloading whisper.cpp model to {dest}...", file=sys.stderr)

    # SSL context with fallbacks
    ctx = None
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        try:
            ctx = ssl.create_default_context()
        except ssl.SSLError:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(GGML_MODEL_URL, headers={"User-Agent": "HiDock/1.0"})
    resp = urllib.request.urlopen(req, timeout=30, context=ctx)
    total = int(resp.headers.get("Content-Length", 0))
    downloaded = 0

    with open(tmp, "wb") as f:
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            if total > 0:
                pct = int(downloaded * 100 / total)
                print(f"PROGRESS:model:{pct}", file=sys.stderr, flush=True)

    if dest.exists():
        dest.unlink()
    tmp.rename(dest)
    print("Model download complete.", file=sys.stderr)


def load_whisper_model():
    """Load whisper.cpp model (cached after first call)."""
    global _model
    if _model is not None:
        return _model

    download_model_if_needed()
    progress(5)

    from pywhispercpp.model import Model
    _model = Model(str(model_path()), n_threads=4)
    progress(10)
    return _model


def transcribe_file(
    mp3_path: Path, model=None, diarize: bool = False, summarize: bool = False,
    summarize_engine: str | None = None,
) -> dict:
    """Transcribe a single audio file. Returns result dict for JSON output."""
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
        "model": config.WHISPER_MODEL,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "duration_s": None,
        "last_error": None,
    }
    save_state(state)

    start_time = time.monotonic()
    try:
        if model is None:
            model = load_whisper_model()

        progress(15)
        segments = model.transcribe(str(mp3_path), language=config.WHISPER_LANGUAGE)
        progress(85)

        # Convert pywhispercpp segments to dicts for diarization
        whisper_dicts = []
        for seg in segments:
            whisper_dicts.append({
                "start": seg.t0 / 100.0 if hasattr(seg, "t0") else 0.0,
                "end": seg.t1 / 100.0 if hasattr(seg, "t1") else 0.0,
                "text": seg.text.strip(),
            })

        config.RAW_TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)

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
                diarized_path = config.RAW_TRANSCRIPTS_DIR / f"{basename}_diarized.json"
                diarized_path.write_text(
                    json.dumps(diarized_result, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
            except Exception as e:
                print(f"Diarization failed (non-fatal): {e}", file=sys.stderr)
                diarized_result = None

        # Build plain text for summarization input
        if diarized_result:
            from shared.transcript_writer import format_diarized_transcript
            text = format_diarized_transcript(diarized_result)
        else:
            text = " ".join(seg.text.strip() for seg in segments).strip()

        # Optionally run LLM summarization
        summary = None
        if summarize:
            try:
                from shared.summarize import summarize as run_summarize
                progress(90)
                summary = run_summarize(text, engine_name=summarize_engine)
            except Exception as e:
                print(f"Summarization failed (non-fatal): {e}", file=sys.stderr)

        # Write transcript with frontmatter
        write_transcript(
            transcript_path,
            text,
            source_path=mp3_path,
            model=config.WHISPER_MODEL,
            diarized_result=diarized_result,
            summary=summary,
        )
        progress(95)

        duration_s = round(time.monotonic() - start_time, 1)

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

        # Run post-transcription hooks (non-fatal)
        try:
            from shared.hooks import run_hooks_pipeline
            run_hooks_pipeline(transcript_path, source_path=mp3_path, summary=summary)
        except Exception as e:
            print(f"Hooks failed (non-fatal): {e}", file=sys.stderr)

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
            mp3_path,
            diarize=getattr(args, "diarize", False),
            summarize=getattr(args, "summarize", False),
            summarize_engine=getattr(args, "summarize_engine", None),
        )
    finally:
        release_lock(lock)

    print(json.dumps(result))
    sys.exit(0 if result["transcribed"] else 1)


def cmd_transcribe_batch(args):
    recordings_dir = config.RECORDINGS_DIR
    if not recordings_dir.exists():
        print(json.dumps({"error": f"Recordings dir not found: {recordings_dir}", "processed": 0}))
        sys.exit(1)

    state = load_state()
    completed = {
        k for k, v in state.get("transcriptions", {}).items()
        if v.get("status") == "completed"
    }

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
                mp3_path,
                model=model,
                diarize=getattr(args, "diarize", False),
                summarize=getattr(args, "summarize", False),
                summarize_engine=getattr(args, "summarize_engine", None),
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


def cmd_summarize(args):
    """Type-aware, template-driven summary of an existing transcript via Claude
    Code -> ~/HiDock/Summaries/. No-ops cleanly if no LLM/templates available."""
    from shared.typed_summarize import summarise_typed
    res = summarise_typed(Path(args.transcript_path).expanduser(), engine_name=args.summarize_engine)
    print(json.dumps(res))


def cmd_detect_engine(_args):
    """Report which AI CLI 'auto' resolves to (PATH detection, priority order
    claude > codex > gemini > ollama). Used by the desktop app so its
    interactive 'Ask AI' / template commands pick the same engine the auto
    summariser would. Prints {"engine": "<name>"|null}."""
    from shared.llm_cli import get_engine
    eng = get_engine("auto")
    print(json.dumps({"engine": eng.name if eng else None}))


def cmd_status(_args):
    state = load_state()
    transcripts_dir = config.RAW_TRANSCRIPTS_DIR
    recordings_dir = config.RECORDINGS_DIR

    lookup = {}
    for key, info in state.get("transcriptions", {}).items():
        lookup[key] = {
            "status": info.get("status", "unknown"),
            "transcript_path": info.get("transcript_path"),
            "transcribed": info.get("status") == "completed",
            "duration_s": info.get("duration_s"),
            "model": info.get("model"),
        }

    if recordings_dir.exists() and transcripts_dir.exists():
        for mp3 in recordings_dir.iterdir():
            if mp3.suffix.lower() in config.WATCH_EXTENSIONS and mp3.name not in lookup:
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
    parser = argparse.ArgumentParser(description="HiDock Transcription Pipeline (whisper.cpp)")
    sub = parser.add_subparsers(dest="command")

    p_transcribe = sub.add_parser("transcribe", help="Transcribe a single audio file")
    p_transcribe.add_argument("mp3_path", help="Path to audio file")
    p_transcribe.add_argument("--diarize", action="store_true", help="Enable speaker diarization")
    p_transcribe.add_argument("--summarize", action="store_true", help="Summarize with LLM after transcription")
    p_transcribe.add_argument("--summarize-engine", default=None, help="LLM engine for summarization (e.g. claude, ollama). Default: config [summarization].engine / auto.")
    p_transcribe.set_defaults(func=cmd_transcribe)

    p_batch = sub.add_parser("transcribe-batch", help="Transcribe all un-transcribed recordings")
    p_batch.add_argument("--diarize", action="store_true", help="Enable speaker diarization")
    p_batch.add_argument("--summarize", action="store_true", help="Summarize with LLM after transcription")
    p_batch.add_argument("--summarize-engine", default=None, help="LLM engine for summarization (e.g. claude, ollama). Default: config [summarization].engine / auto.")
    p_batch.set_defaults(func=cmd_transcribe_batch)

    p_status = sub.add_parser("status", help="JSON report of transcription state")
    p_status.set_defaults(func=cmd_status)

    p_summarize = sub.add_parser("summarize", help="Type-aware template summary of an existing transcript -> ~/HiDock/Summaries/")
    p_summarize.add_argument("transcript_path", help="Path to the transcript .md (basename locates the _whisper.json)")
    p_summarize.add_argument("--summarize-engine", default=None, help="LLM engine (e.g. claude). Default: config [summarization].engine / auto.")
    p_summarize.set_defaults(func=cmd_summarize)

    p_detect = sub.add_parser("detect-engine", help="Report which AI CLI 'auto' resolves to -> JSON {engine}")
    p_detect.set_defaults(func=cmd_detect_engine)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
