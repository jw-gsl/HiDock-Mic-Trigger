"""Fixed-window language detection for code-switched recordings.

Whisper (and whisper.cpp/pywhispercpp) both detect language once, from the
start of the audio, and reuse that single guess for the whole file — a call
that switches language mid-way (English intro, Portuguese body) gets the
non-English stretch force-decoded in the first language detected instead of
transcribed or translated. This module re-runs detection on every ~30s
window instead, so a sustained switch gets picked up and decoded in its own
language.

Windowing is fixed-size rather than per-diarized-speaker-turn on purpose:
Whisper's encoder always processes a fixed ~30s frame internally (padding
shorter clips out to that), so per-turn detection on the short, frequent
turns typical of a real meeting would multiply encoder invocations well
beyond what whole-file windowing needs. Fixed windows catch sustained
switches — the case that actually matters — at negligible extra cost. See
docs/PLAN-multilingual-transcription.md for the reasoning.
"""
from __future__ import annotations

from typing import Callable, Sequence

DEFAULT_WINDOW_SECONDS = 30.0
# Below this confidence, keep the previous window's language instead of the
# new guess — a near-silent or very short window gives language detection
# little to work with, and a low-confidence flip is worse than continuity.
DEFAULT_CONFIDENCE_FLOOR = 0.6


def compute_windows(
    duration_s: float, window_s: float = DEFAULT_WINDOW_SECONDS
) -> list[tuple[float, float]]:
    """Split ``[0, duration_s)`` into consecutive ``(start, end)`` windows of
    at most ``window_s`` seconds each. The final window may be shorter."""
    if duration_s <= 0 or window_s <= 0:
        return []
    windows = []
    start = 0.0
    while start < duration_s:
        end = min(start + window_s, duration_s)
        windows.append((start, end))
        start = end
    return windows


def sample_probe_offsets(
    duration_s: float, window_s: float = DEFAULT_WINDOW_SECONDS
) -> list[float]:
    """Pick up to 3 window start offsets spread across the file — start,
    middle, end — for a cheap upfront language probe. Enough to catch a
    sustained mid-call language switch without detecting on every window."""
    if duration_s <= 0:
        return []
    offsets = {0.0}
    if duration_s > window_s:
        offsets.add(max(0.0, (duration_s - window_s) / 2))
        offsets.add(max(0.0, duration_s - window_s))
    return sorted(offsets)


def probe_has_non_english(
    duration_s: float,
    detect_fn: Callable[[float, float], tuple[str, float]],
    window_s: float = DEFAULT_WINDOW_SECONDS,
    confidence_floor: float = DEFAULT_CONFIDENCE_FLOOR,
    english_code: str = "en",
) -> bool:
    """True if any sampled window is confidently non-English.

    Used to decide whether an English-only backend (Parakeet) is safe to use
    for a file, or whether it needs routing to a multilingual backend
    (Whisper) instead — Parakeet is English-only by model architecture, so
    no per-window language forcing can fix it the way it fixes Whisper.
    """
    for start_s in sample_probe_offsets(duration_s, window_s):
        language, confidence = detect_fn(start_s, min(start_s + window_s, duration_s))
        if language != english_code and confidence >= confidence_floor:
            return True
    return False


def transcribe_with_language_windows(
    duration_s: float,
    detect_fn: Callable[[float, float], tuple[str, float]],
    transcribe_fn: Callable[[float, float, str], Sequence[dict]],
    window_s: float = DEFAULT_WINDOW_SECONDS,
    fallback_language: str = "en",
    confidence_floor: float = DEFAULT_CONFIDENCE_FLOOR,
) -> list[dict]:
    """Detect language per fixed window and transcribe each window forced to
    its own detected language, tagging every returned segment with it.

    ``detect_fn(start_s, end_s) -> (language_code, confidence)`` and
    ``transcribe_fn(start_s, end_s, language_code) -> [{"start", "end", "text", ...}, ...]``
    are supplied by the caller because the ASR backends in this pipeline
    (openai-whisper, pywhispercpp) have unrelated APIs for both steps — this
    function owns only the windowing/fallback/tagging logic, which is
    identical either way. Segment timestamps returned by ``transcribe_fn``
    must already be absolute (offset by ``start_s``), since only the caller
    knows how to offset its own backend's segment format.
    """
    segments: list[dict] = []
    last_language = fallback_language
    for start_s, end_s in compute_windows(duration_s, window_s):
        language, confidence = detect_fn(start_s, end_s)
        if confidence < confidence_floor:
            language = last_language
        else:
            last_language = language
        for seg in transcribe_fn(start_s, end_s, language):
            seg = dict(seg)
            seg["language"] = language
            seg["language_confidence"] = confidence
            segments.append(seg)
    return segments
