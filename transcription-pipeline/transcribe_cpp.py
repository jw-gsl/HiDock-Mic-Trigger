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
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add the repo root to sys.path so shared modules are importable
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import config  # noqa: E402
from state import load_state, save_state, update_state  # noqa: E402

LOCK_PATH = Path(config.HIDOCK_ROOT) / "transcription-pipeline" / ".transcribe.lock"

# Tracks the currently in-flight transcription so the SIGTERM handler can
# flip its state from "in_progress" to "failed" before the process exits.
# Without this, a timeout kill leaves state stuck in "in_progress" and the
# app can never re-queue the recording. Set by transcribe_file, cleared on
# completion. (Mirrors transcribe.py.)
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
            key = _IN_FLIGHT["key"]

            def _mark_failed(state: dict) -> None:
                existing = state.setdefault("transcriptions", {}).get(key, {})
                state["transcriptions"][key] = {
                    **existing,
                    "status": "failed",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "last_error": f"Terminated by signal {signum} (likely timeout)",
                }

            # Prefer the locked read-modify-write; if the lock is busy fall
            # back to an unlocked write — a possibly-racy "failed" beats a
            # permanently stuck "in_progress".
            if not update_state(_mark_failed, timeout=1.0):
                state = load_state()
                _mark_failed(state)
                save_state(state)
    except Exception:
        pass
    sys.exit(128 + signum)


signal.signal(signal.SIGTERM, _sigterm_handler)
signal.signal(signal.SIGINT, _sigterm_handler)

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
    calendar_context_path: str | Path | None = None,
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

    # Register with the SIGTERM handler so state flips to "failed" instead of
    # staying stuck at "in_progress" if the parent app kills us via timeout.
    global _IN_FLIGHT
    _IN_FLIGHT = {"key": entry_key}

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
                calendar_context = None
                try:
                    from shared.calendar_context import load_context_for_audio
                    calendar_context = load_context_for_audio(
                        mp3_path,
                        context_path=calendar_context_path,
                    )
                    if calendar_context is not None:
                        print(
                            "Calendar context: "
                            f"{calendar_context.summary()}",
                            file=sys.stderr,
                        )
                except Exception as context_error:
                    print(
                        f"Calendar context unavailable (voice-only fallback): {context_error}",
                        file=sys.stderr,
                    )
                from shared.diarize_lite import diarize as run_diarize
                from shared.voice_library_lite import identify_speakers
                from shared.audio_utils import load_audio, extract_embedding, segment_audio

                diarized_result = run_diarize(
                    mp3_path,
                    whisper_dicts,
                    calendar_context=calendar_context,
                )

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
                    ids = identify_speakers(
                        emb_list,
                        allowed_names=getattr(calendar_context, "candidate_names", None),
                    )
                    for i, spk in enumerate(spk_list):
                        name, conf = ids[i]
                        if name is not None:
                            diarized_result["speaker_names"][spk] = name

                if calendar_context is not None and hasattr(calendar_context, "to_metadata"):
                    diarized_result["calendar_context"] = calendar_context.to_metadata()

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
            calendar_context_path=getattr(args, "calendar_context", None),
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


def cmd_rematch(args):
    """Re-match still-generic speakers in an existing transcript against the
    current voice library (used after enrolling new voices). Never overwrites a
    user-confirmed name. Re-derives per-speaker embeddings from audio when the
    sidecar predates embedding storage (unless --no-audio). Mirrors
    transcribe.py's cmd_rematch."""
    import json as _json
    json_path = Path(args.json_path).resolve()
    if not json_path.exists():
        print(f"File not found: {json_path}", file=sys.stderr)
        sys.exit(1)
    progress(5)
    data = _json.loads(json_path.read_text(encoding="utf-8"))
    from shared.speaker_meta import rematch_diarized
    progress(10)
    summary = rematch_diarized(data, audio_fallback=not getattr(args, "no_audio", False))
    progress(90)

    json_path.write_text(
        _json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    if summary.get("rematched", 0) > 0:
        try:
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
                model="rematch",
                diarized_result=data,
            )
        except Exception as exc:
            print(f"WARN: could not refresh .md: {exc}", file=sys.stderr)

    progress(100)
    print(_json.dumps({"status": "completed", **summary}))


def cmd_speaker_confidence(args):
    """Per-speaker confidence that each assigned name is correct (cosine
    similarity of the speaker's stored embedding to the enrolled voice of that
    name). Mirrors transcribe.py. Fast — no audio, no model load."""
    import json as _json
    json_path = Path(args.json_path).resolve()
    if not json_path.exists():
        print(f"File not found: {json_path}", file=sys.stderr)
        sys.exit(1)
    data = _json.loads(json_path.read_text(encoding="utf-8"))
    from shared.speaker_meta import score_speakers
    print(_json.dumps({"status": "completed", "confidence": score_speakers(data)}))


def cmd_summarize(args):
    """Type-aware, template-driven summary of an existing transcript via Claude
    Code -> ~/HiDock/Summaries/. No-ops cleanly if no LLM/templates available."""
    from shared.typed_summarize import summarise_typed
    events = None
    if getattr(args, "events", False):
        from shared.agent_events import EventEmitter
        events = EventEmitter()   # normalized NDJSON on stderr for the desktop app
    res = summarise_typed(
        Path(args.transcript_path).expanduser(),
        engine_name=args.summarize_engine,
        force_template=getattr(args, "template", None),
        events=events,
    )
    print(json.dumps(res))


def cmd_ask(args):
    """One conversational turn for the desktop chat view. Reads the prompt from
    stdin, streams normalized events (NDJSON on stderr), and prints a JSON
    result {ok, session_id, text} on stdout. Multi-turn = re-invoke with
    --resume <session_id>. Mirrors transcribe.cmd_ask."""
    import sys as _sys
    from shared.agent_events import EventEmitter
    from shared.llm_cli import chat_streaming, get_engine
    prompt = _sys.stdin.read()
    em = EventEmitter()
    allowed = None
    if getattr(args, "allowed_tools", None):
        allowed = [t.strip() for t in args.allowed_tools.split(",") if t.strip()]
    text, session_id = chat_streaming(
        prompt,
        engine=get_engine(args.engine or "auto"),
        cwd=getattr(args, "cwd", None),
        resume=getattr(args, "resume", None),
        allowed_tools=allowed,
        on_event=em,
    )
    em.done(ok=text is not None, session_id=session_id)
    print(json.dumps({"ok": text is not None, "session_id": session_id, "text": text}))


def cmd_notes(args):
    """List/search/get/stats over typed meeting summaries (~/HiDock/Summaries).
    The terminal-facing twin of the MCP server's summary tools."""
    import json as _json
    from shared import summaries_index as si
    action = args.action
    if action == "list":
        rows = si.list_summaries(type=args.type, area=args.area, since=args.since, limit=args.limit)
        if args.json:
            print(_json.dumps(rows, indent=2))
        elif not rows:
            print("No summaries found for that filter.")
        else:
            for s in rows:
                print(f"- {s['title']}  [{s['type']} / {s['area']}]  ({s['recorded']})  — {s['filename']}")
    elif action == "search":
        rows = si.search_summaries(args.query, limit=args.limit)
        if args.json:
            print(_json.dumps(rows, indent=2))
        elif not rows:
            print("No matches.")
        else:
            for s in rows:
                print(f"- {s['title']}  [{s['type']} / {s['area']}]  ({s['recorded']})")
                print(f"    {s['snippet']}")
                print(f"    {s['filename']}")
    elif action == "get":
        s = si.get_summary(args.id or args.query)
        if not s:
            print("No summary matched.")
        elif args.json:
            print(_json.dumps(s, indent=2))
        else:
            print(f"# {s['title']}\nType: {s['type']} | Area: {s['area']} | Recorded: {s['recorded']}\n")
            print(s["body"])
    elif action == "stats":
        print(_json.dumps(si.summary_stats(), indent=2))


def cmd_detect_engine(_args):
    """Report which AI CLI 'auto' resolves to (PATH detection, priority order
    claude > codex > gemini > ollama). Used by the desktop app so its
    interactive 'Ask AI' / template commands pick the same engine the auto
    summariser would. Prints {"engine": "<name>"|null}."""
    from shared.llm_cli import get_engine
    eng = get_engine("auto")
    print(json.dumps({"engine": eng.name if eng else None}))


def cmd_activity_stats(_args):
    """Per-transcript speaker / action-item counts for the meeting heatmap's
    Tier-2 tooltip. Prints {"<source_mp3_name>": {"speakers": N,
    "action_items": M}, ...} from ~/HiDock/Raw Transcripts frontmatter."""
    from shared.transcript_stats import transcript_stats
    print(json.dumps(transcript_stats()))


def cmd_status(_args):
    state = load_state()
    transcripts_dir = config.RAW_TRANSCRIPTS_DIR
    recordings_dir = config.RECORDINGS_DIR

    # Build lookup: mp3 filename -> transcription info.
    # Verify the transcript file still exists on disk before reporting
    # `transcribed: True`. Without this check, removing a recording in
    # the Mac app deletes the .md/.srt/.json files but leaves this
    # state.json entry stuck at status="completed", so the desktop UI
    # keeps flipping the row back to "Transcribed" — overriding the
    # "Removed" status the user just set. This guard reports the row
    # as not-transcribed once the artifact's gone. (Mirrors transcribe.py.)
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
    # action). Uses the locked read-modify-write so a concurrent
    # transcription's completion save can't be clobbered (and vice
    # versa); each entry is re-verified under the lock in case it was
    # re-transcribed since the scan above. Best-effort — on lock timeout
    # we skip the prune rather than blocking status; the in-memory
    # `lookup` already reflects truth for this call.
    if stale_keys:
        def _prune(s: dict) -> None:
            transcriptions = s.get("transcriptions", {})
            for k in stale_keys:
                info = transcriptions.get(k)
                if not info or info.get("status") != "completed":
                    continue  # changed since scan (e.g. re-queued) — keep
                tp = info.get("transcript_path")
                if tp and Path(tp).exists():
                    continue  # transcript reappeared — keep
                transcriptions.pop(k, None)

        if not update_state(_prune):
            print(
                f"WARN: state lock busy; skipped pruning {len(stale_keys)} stale entries",
                file=sys.stderr,
            )

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
    p_transcribe.add_argument(
        "--calendar-context",
        help="Optional JSON exported by the Microsoft 365 MCP calendar bridge",
    )
    p_transcribe.set_defaults(func=cmd_transcribe)

    p_batch = sub.add_parser("transcribe-batch", help="Transcribe all un-transcribed recordings")
    p_batch.add_argument("--diarize", action="store_true", help="Enable speaker diarization")
    p_batch.add_argument("--summarize", action="store_true", help="Summarize with LLM after transcription")
    p_batch.add_argument("--summarize-engine", default=None, help="LLM engine for summarization (e.g. claude, ollama). Default: config [summarization].engine / auto.")
    p_batch.set_defaults(func=cmd_transcribe_batch)

    p_status = sub.add_parser("status", help="JSON report of transcription state")
    p_status.set_defaults(func=cmd_status)

    p_rematch = sub.add_parser(
        "rematch",
        help="Re-match still-generic speakers against the current voice library (after enrolling new voices)",
    )
    p_rematch.add_argument("json_path", help="Path to _diarized.json file")
    p_rematch.add_argument(
        "--no-audio",
        action="store_true",
        help="Stored-embedding fast path only; skip the CPU-heavy audio re-embed fallback for legacy sidecars.",
    )
    p_rematch.set_defaults(func=cmd_rematch)

    p_confidence = sub.add_parser(
        "speaker-confidence",
        help="Per-speaker confidence that each assigned name matches the enrolled voice (JSON)",
    )
    p_confidence.add_argument("json_path", help="Path to _diarized.json file")
    p_confidence.set_defaults(func=cmd_speaker_confidence)

    p_summarize = sub.add_parser("summarize", help="Type-aware template summary of an existing transcript -> ~/HiDock/Summaries/")
    p_summarize.add_argument("transcript_path", help="Path to the transcript .md (basename locates the _whisper.json)")
    p_summarize.add_argument("--summarize-engine", default=None, help="LLM engine (e.g. claude). Default: config [summarization].engine / auto.")
    p_summarize.add_argument("--template", default=None, help="Force a specific template by name (skip auto-classification).")
    p_summarize.add_argument("--events", action="store_true", help="Emit normalized agent events (NDJSON on stderr) for the desktop app's formatted view.")
    p_summarize.set_defaults(func=cmd_summarize)

    p_ask = sub.add_parser("ask", help="One conversational turn (prompt on stdin) for the desktop chat view; streams normalized events.")
    p_ask.add_argument("--engine", default=None, help="LLM engine (default: config [summarization].engine / auto).")
    p_ask.add_argument("--cwd", default=None, help="Working directory for the engine (e.g. the transcript's folder).")
    p_ask.add_argument("--resume", default=None, help="Resume a prior claude session id for multi-turn chat.")
    p_ask.add_argument("--allowed-tools", default=None, help="Comma-separated claude tool allow-list (e.g. Read,Grep,Glob).")
    p_ask.set_defaults(func=cmd_ask)

    p_detect = sub.add_parser("detect-engine", help="Report which AI CLI 'auto' resolves to -> JSON {engine}")
    p_detect.set_defaults(func=cmd_detect_engine)

    p_activity = sub.add_parser("activity-stats", help="Per-transcript speaker/action-item counts -> JSON (heatmap Tier-2 tooltip)")
    p_activity.set_defaults(func=cmd_activity_stats)

    p_notes = sub.add_parser("notes", help="List/search/get your typed meeting summaries (~/HiDock/Summaries)")
    p_notes.add_argument("action", choices=["list", "search", "get", "stats"])
    p_notes.add_argument("--query", "-q", default="", help="search text (search) or identifier (get)")
    p_notes.add_argument("--id", default="", help="recording/title/filename substring (get)")
    p_notes.add_argument("--type", default=None, help="filter by classification type, e.g. 'Brainstorming'")
    p_notes.add_argument("--area", default=None, help="filter by area substring")
    p_notes.add_argument("--since", default=None, help="ISO date lower bound, e.g. 2026-06-01")
    p_notes.add_argument("--limit", type=int, default=50)
    p_notes.add_argument("--json", action="store_true")
    p_notes.set_defaults(func=cmd_notes)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
