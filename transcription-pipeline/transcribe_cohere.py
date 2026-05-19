#!/usr/bin/env python3
"""HiDock transcription pipeline — Cohere Transcribe + forced alignment.

Cohere Transcribe 03-2026 is SOTA for accuracy (5.42% WER, #1 HF Open ASR
Leaderboard) but emits plain text only: no timestamps, no diarization, no
automatic language detection. Our pipeline needs per-segment timestamps to
align VAD speech boundaries to the transcript for diarization.

This module handles that by running a forced-alignment pass after Cohere
produces text: a wav2vec2 CTC model aligns each word back to its position
in the audio, giving us the timing data the rest of the pipeline needs.

Status: PROTOTYPE. Not yet benchmarked end-to-end. Requires Cohere weights
(~4 GB) and per-language wav2vec2 CTC aligner weights (~1.2 GB each) to
actually run.

Licence requirements satisfied:
  - Cohere Transcribe: Apache-2.0 ✓
  - torchaudio.functional.forced_align: BSD-2-Clause ✓
  - wav2vec2 CTC aligners (jonatasgrosman/wav2vec2-large-xlsr-53-*): Apache-2.0 ✓
  - NOT using MMS-FA (CC-BY-NC 4.0, non-commercial — blocker)
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

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

LOCK_PATH = Path(config.HIDOCK_ROOT) / "transcription-pipeline" / ".transcribe.lock"

COHERE_MODEL_ID = "CohereLabs/cohere-transcribe-03-2026"

# Commercial-friendly wav2vec2 CTC aligners, keyed by Cohere's language codes.
# All Apache-2.0. Loaded lazily — only downloaded when the user transcribes
# audio in that language.
ALIGNER_MODELS = {
    "en": "jonatasgrosman/wav2vec2-large-xlsr-53-english",
    "de": "jonatasgrosman/wav2vec2-large-xlsr-53-german",
    "fr": "jonatasgrosman/wav2vec2-large-xlsr-53-french",
    "it": "jonatasgrosman/wav2vec2-large-xlsr-53-italian",
    "es": "jonatasgrosman/wav2vec2-large-xlsr-53-spanish",
    "pt": "jonatasgrosman/wav2vec2-large-xlsr-53-portuguese",
    "el": "jonatasgrosman/wav2vec2-large-xlsr-53-greek",
    "nl": "jonatasgrosman/wav2vec2-large-xlsr-53-dutch",
    "pl": "jonatasgrosman/wav2vec2-large-xlsr-53-polish",
    "ar": "jonatasgrosman/wav2vec2-large-xlsr-53-arabic",
    "zh": "jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn",
    "ja": "jonatasgrosman/wav2vec2-large-xlsr-53-japanese",
    "ko": "kresnik/wav2vec2-large-xlsr-korean",  # no jonatasgrosman for ko
    "vi": "nguyenvulebinh/wav2vec2-base-vietnamese-250h",  # Apache-2.0
}

_IN_FLIGHT: dict[str, str] | None = None


def _sigterm_handler(signum, frame):
    """Mirror the Whisper path — flip state to failed on parent-initiated kill."""
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
    print(f"PROGRESS: {pct}", flush=True)


def stage(current: int, total: int, label: str) -> None:
    print(f"STAGE: {current}/{total} {label}", flush=True)


def _load_cohere_model():
    """Load Cohere Transcribe via the transformers library.

    Uses MPS on Apple Silicon with CPU fallback for any ops MPS doesn't
    implement (PYTORCH_ENABLE_MPS_FALLBACK=1).
    """
    import os
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    import torch
    from transformers import AutoProcessor
    try:
        from transformers import CohereAsrForConditionalGeneration
    except ImportError as e:
        raise RuntimeError(
            "Cohere Transcribe requires transformers>=5.4.0. "
            "Install with: pip install 'transformers>=5.4.0'"
        ) from e

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    processor = AutoProcessor.from_pretrained(COHERE_MODEL_ID)
    model = CohereAsrForConditionalGeneration.from_pretrained(
        COHERE_MODEL_ID,
        torch_dtype=torch.float16 if device == "mps" else torch.float32,
    ).to(device)
    return processor, model, device


def _cohere_transcribe_text(audio, sr, processor, model, device, language: str) -> str:
    """Run Cohere on a loaded audio array, return plain text (no timestamps)."""
    import torch
    inputs = processor(audio, sampling_rate=sr, return_tensors="pt").to(device)
    with torch.no_grad():
        generated_ids = model.generate(
            **inputs,
            language=language,
            max_new_tokens=2048,
        )
    text = processor.batch_decode(generated_ids, skip_special_tokens=True)[0]
    return text.strip()


def _align_text_to_audio(
    text: str, audio, sr: int, language: str,
) -> list[dict]:
    """Produce word- and sentence-level timestamps by forced-aligning Cohere's
    text back to the audio.

    Uses torchaudio.functional.forced_align with a language-specific wav2vec2
    CTC model. Returns segments in our standard {start, end, text} shape, with
    sentences grouped at punctuation boundaries.
    """
    aligner_id = ALIGNER_MODELS.get(language)
    if aligner_id is None:
        raise ValueError(
            f"No forced-aligner configured for language '{language}'. "
            f"Supported: {sorted(ALIGNER_MODELS.keys())}"
        )

    import torch
    import torchaudio
    from transformers import AutoProcessor, AutoModelForCTC

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    processor = AutoProcessor.from_pretrained(aligner_id)
    model = AutoModelForCTC.from_pretrained(aligner_id).to(device)
    model.eval()

    # Tokenise text into the aligner's vocabulary
    with processor.as_target_processor():
        labels = processor(text).input_ids

    # Run CTC forward pass
    input_values = processor(audio, sampling_rate=sr, return_tensors="pt").input_values.to(device)
    with torch.no_grad():
        logits = model(input_values).logits
    log_probs = torch.log_softmax(logits, dim=-1).cpu()

    # Forced align
    targets = torch.tensor([labels], dtype=torch.int32)
    alignments, scores = torchaudio.functional.forced_align(
        log_probs, targets, blank=model.config.pad_token_id or 0
    )

    # Convert frame-level alignment to word-level timestamps.
    # Aligner frame stride depends on model config — typically 20ms for
    # wav2vec2-large (320 samples at 16 kHz).
    frame_stride_s = 0.02
    token_timings = []
    prev = -1
    for i, t in enumerate(alignments[0].tolist()):
        if t != prev and t != 0:  # new non-blank token
            token_timings.append((i * frame_stride_s, processor.tokenizer.decode([t])))
        prev = t

    # Group tokens into words using the tokenizer's word-boundary markers,
    # then group words into sentences using punctuation.
    # Simplified heuristic: split Cohere's text on sentence punctuation,
    # distribute token timings proportionally. Precise word-level alignment
    # is left as a later refinement.
    import re
    sentences = re.split(r"(?<=[.!?。！？])\s+", text.strip())
    if not sentences:
        return []

    total_tokens = max(len(token_timings), 1)
    audio_duration = len(audio) / sr
    segments = []
    tokens_per_sentence = total_tokens / len(sentences)
    for i, sent in enumerate(sentences):
        start_token = int(i * tokens_per_sentence)
        end_token = int((i + 1) * tokens_per_sentence) - 1
        start_token = min(start_token, total_tokens - 1)
        end_token = max(start_token, min(end_token, total_tokens - 1))
        start_s = token_timings[start_token][0] if token_timings else 0.0
        end_s = token_timings[end_token][0] if token_timings else audio_duration
        segments.append({
            "start": float(start_s),
            "end": float(end_s),
            "text": sent,
        })
    return segments


def transcribe_file(
    mp3_path: Path, model=None, diarize: bool = False, summarize: bool = False,
    n_speakers: int | None = None, language: str = "en",
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
        "model": COHERE_MODEL_ID,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "duration_s": None,
        "last_error": None,
    }
    save_state(state)

    global _IN_FLIGHT
    _IN_FLIGHT = {"key": entry_key}

    start_time = time.monotonic()
    try:
        total_stages = 6 if diarize else 5  # extra stage for forced alignment
        stage(1, total_stages, "Loading Cohere model")
        processor, cohere_model, device = _load_cohere_model()

        stage(2, total_stages, "Loading and preparing audio")
        from shared.audio_utils import load_audio
        from shared.diarize_lite import _replace_silence_with_padding
        audio = load_audio(str(mp3_path), sr=16000)
        audio = _replace_silence_with_padding(audio, sr=16000)

        progress(20)
        stage(3, total_stages, "Transcribing (Cohere)")
        text = _cohere_transcribe_text(audio, 16000, processor, cohere_model, device, language)
        progress(65)

        # Free Cohere model memory before loading the aligner
        del cohere_model
        import gc
        gc.collect()

        stage(4, total_stages, "Aligning timestamps")
        segments = _align_text_to_audio(text, audio, 16000, language)
        progress(80)

        # Whisper-Guard is model-agnostic text cleanup, safe to apply to Cohere output too
        from shared.whisper_guard import clean_transcript
        text, guard_stats = clean_transcript(text, language=language)
        if guard_stats.filters_triggered:
            print(
                f"Whisper-Guard: filters triggered: {guard_stats.filters_triggered}",
                file=sys.stderr,
            )

        try:
            from shared.corrections import apply_corrections
            text = apply_corrections(text)
            for seg in segments:
                seg["text"] = apply_corrections(seg["text"])
        except ImportError:
            pass

        diarized_result = None
        if diarize:
            stage(5, total_stages, "Diarizing speakers")
            from shared.diarize_lite import diarize as run_diarize
            diarized_result = run_diarize(str(mp3_path), segments, n_speakers=n_speakers)

        summary = None
        if summarize:
            try:
                from shared.summarize import summarize as run_summarize
                summary = run_summarize(text)
            except Exception as e:
                print(f"Summarization failed (non-fatal): {e}", file=sys.stderr)

        stage(total_stages, total_stages, "Writing transcript")
        import json as _json
        write_transcript(
            output_path=transcript_path,
            transcript_text=text,
            source_path=mp3_path,
            model=COHERE_MODEL_ID,
            diarized_result=diarized_result,
            whisper_segments=segments,
            summary=summary,
        )

        whisper_raw_path = transcript_path.with_name(transcript_path.stem + "_whisper.json")
        whisper_raw_path.write_text(
            _json.dumps({"audio_file": str(mp3_path), "segments": segments}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        segments_json_path = transcript_path.with_name(transcript_path.stem + "_diarized.json")
        if diarized_result is not None:
            segments_json_path.write_text(
                _json.dumps(diarized_result, indent=2, ensure_ascii=False), encoding="utf-8",
            )
        else:
            plain_segments = [{**s, "speaker_id": 0, "speaker": ""} for s in segments]
            plain_result = {
                "version": 1, "audio_file": str(mp3_path),
                "segments": plain_segments, "speaker_names": {},
            }
            segments_json_path.write_text(
                _json.dumps(plain_result, indent=2, ensure_ascii=False), encoding="utf-8",
            )

        duration_s = round(time.monotonic() - start_time, 1)
        state = load_state()
        state["transcriptions"][entry_key] = {
            "status": "completed",
            "source_path": str(mp3_path),
            "transcript_path": str(transcript_path),
            "model": COHERE_MODEL_ID,
            "started_at": state["transcriptions"].get(entry_key, {}).get("started_at"),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "duration_s": duration_s,
            "last_error": None,
        }
        save_state(state)

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
            "backend": "cohere+align",
        }

    except Exception as e:
        duration_s = round(time.monotonic() - start_time, 1)
        state = load_state()
        state["transcriptions"][entry_key] = {
            "status": "failed",
            "source_path": str(mp3_path),
            "transcript_path": str(transcript_path),
            "model": COHERE_MODEL_ID,
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
            "backend": "cohere+align",
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
            mp3_path, diarize=args.diarize, summarize=args.summarize,
            n_speakers=getattr(args, "n_speakers", None),
            language=args.language,
        )
    finally:
        release_lock(lock)

    print(json.dumps(result))
    sys.exit(0 if result["transcribed"] else 1)


def main():
    parser = argparse.ArgumentParser(
        description="HiDock Transcription Pipeline (Cohere Transcribe + forced alignment)"
    )
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("transcribe", help="Transcribe a single audio file")
    p.add_argument("mp3_path", help="Path to audio file")
    p.add_argument("--diarize", action="store_true", help="Enable speaker diarization")
    p.add_argument("--summarize", action="store_true", help="Summarize with LLM after transcription")
    p.add_argument("--n-speakers", type=int, help="Hint: expected number of speakers")
    p.add_argument(
        "--language", default="en",
        choices=sorted(ALIGNER_MODELS.keys()),
        help="Cohere requires pre-specified language (one of its 14 supported).",
    )
    p.set_defaults(func=cmd_transcribe)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
