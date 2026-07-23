"""Parakeet TDT v2 ASR backend via parakeet-mlx.

Selectable alternative to Whisper. Uses Apple's MLX via the
`parakeet-mlx` pip package (installed through the Model Manager).
English-only, leaderboard-topping accuracy on English meetings,
~60× real-time on Apple Silicon.

Exposes `transcribe(audio_path, language) -> dict` returning the same
shape as Whisper's `model.transcribe()` so `transcribe.py` can swap
backends without caring which one ran:

    { "text": str, "segments": [{"start": float, "end": float, "text": str}] }

If `parakeet-mlx` isn't installed this module raises a helpful
ImportError at call time — never on import — so a user selecting
Whisper still gets a working pipeline even if Parakeet isn't set up.
"""
from __future__ import annotations

from pathlib import Path

from shared.word_timing import aligned_tokens_to_words, words_to_text


def transcribe(audio_path: str | Path, language: str | None = None) -> dict:
    """Transcribe audio with Parakeet TDT v2 via MLX.

    Args:
        audio_path: path to an audio file (any format ffmpeg can decode).
        language: ignored; Parakeet TDT v2 is English-only.

    Returns:
        dict with "text" (full transcript) and "segments" (per-sentence
        windows with start/end/text), matching Whisper's output shape.

    Raises:
        ModuleNotFoundError: if `parakeet-mlx` isn't installed. The user
            should click "Install" on the Parakeet row in the Model
            Manager, which pip-installs the package.
    """
    try:
        from parakeet_mlx import from_pretrained
    except ImportError as e:
        raise ModuleNotFoundError(
            "parakeet-mlx is not installed. Install it via the Model "
            "Manager (Parakeet row > Install) before selecting Parakeet "
            "as the active transcription backend."
        ) from e

    if language and language.lower() not in ("en", "english", ""):
        # Parakeet is English-only. Don't silently produce bad output —
        # the caller should fall back to Whisper for non-English audio
        # at a higher level.
        raise ValueError(
            f"Parakeet TDT v2 supports English only; got language={language!r}. "
            "Switch to Whisper for multilingual transcription."
        )

    model = from_pretrained("mlx-community/parakeet-tdt-0.6b-v2")

    # Chunk long audio. Feeding a multi-hour file to MLX in a single
    # `transcribe()` call builds one enormous Metal command buffer; on
    # Apple Silicon that overflows a GPU limit and the buffer fails at
    # completion time. parakeet-mlx surfaces that via
    # `mlx::core::gpu::check_error`, which THROWS from a Metal completion
    # handler thread — there is no Python frame to catch it, so the
    # process hits std::terminate -> SIGABRT (the crash seen on 3h+
    # recordings). Splitting into overlapping chunks keeps each eval's
    # command buffer bounded; parakeet-mlx stitches the pieces back into a
    # single AlignedResult with globally-correct timestamps, so the output
    # shape below is unchanged.
    #
    # Defaults mirror the parakeet-mlx CLI (chunk 120s, overlap 15s) and
    # honour the same env vars, so behaviour can be tuned without a code
    # change. `chunk_duration=0` disables chunking (whole-file path).
    import os

    def _env_float(name: str, default: float) -> float:
        raw = os.environ.get(name, "").strip()
        try:
            return float(raw) if raw else default
        except ValueError:
            return default

    chunk_duration = _env_float("PARAKEET_CHUNK_DURATION", 120.0)
    overlap_duration = _env_float("PARAKEET_OVERLAP_DURATION", 15.0)

    # `transcribe` in parakeet-mlx returns an AlignedResult with `text`
    # and segment-level alignment. Shape varies across versions; shield
    # ourselves by defensively extracting what we need.
    if chunk_duration and chunk_duration > 0:
        result = model.transcribe(
            str(audio_path),
            chunk_duration=chunk_duration,
            overlap_duration=overlap_duration,
        )
    else:
        result = model.transcribe(str(audio_path))

    text = getattr(result, "text", None) or ""
    segments_raw = getattr(result, "sentences", None) or getattr(result, "segments", None) or []

    segments = []
    for seg in segments_raw:
        start = getattr(seg, "start", None)
        end = getattr(seg, "end", None)
        seg_text = getattr(seg, "text", None) or ""
        if start is None or end is None:
            # Some versions expose .words instead; derive sentence
            # boundaries from word timestamps.
            words = getattr(seg, "words", None) or []
            if not words:
                continue
            start = float(getattr(words[0], "start", 0))
            end = float(getattr(words[-1], "end", start))
        # Parakeet exposes token-level alignment on AlignedSentence.tokens.
        # Keep it: diarization can then change speaker at a word boundary
        # instead of assigning one long sentence to whichever speaker has the
        # most overlap with it.  A few package versions call this collection
        # ``words``, so support both names.
        raw_tokens = []
        for token in (getattr(seg, "tokens", None) or getattr(seg, "words", None) or []):
            raw_tokens.append({
                "text": getattr(token, "text", None) or getattr(token, "word", None),
                "start": getattr(token, "start", None),
                "end": getattr(token, "end", None),
                "confidence": getattr(token, "confidence", None),
            })
        timed_words = aligned_tokens_to_words(
            raw_tokens,
            default_start=float(start),
            default_end=float(end),
        )

        if timed_words:
            # Some releases omit sentence text or expose slightly different
            # whitespace. Reconstructing from the aligned tokens gives the
            # downstream sidecars one consistent representation.
            seg_text = seg_text.strip() or words_to_text(timed_words)
            start = min(float(start), timed_words[0]["start"])
            end = max(float(end), timed_words[-1]["end"])

        output = {
            "start": float(start),
            "end": float(end),
            "text": seg_text.strip(),
        }
        if timed_words:
            output["words"] = timed_words
        segments.append(output)

    return {"text": text.strip(), "segments": segments}
