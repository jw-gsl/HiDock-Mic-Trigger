"""Tests for fixed-window language detection (shared/lang_windows.py)."""
from __future__ import annotations

from shared.lang_windows import (
    compute_windows,
    probe_has_non_english,
    sample_probe_offsets,
    transcribe_with_language_windows,
)


def test_compute_windows_splits_into_fixed_size_chunks():
    assert compute_windows(75.0, window_s=30.0) == [(0.0, 30.0), (30.0, 60.0), (60.0, 75.0)]


def test_compute_windows_exact_multiple_has_no_trailing_short_window():
    assert compute_windows(60.0, window_s=30.0) == [(0.0, 30.0), (30.0, 60.0)]


def test_compute_windows_shorter_than_one_window():
    assert compute_windows(10.0, window_s=30.0) == [(0.0, 10.0)]


def test_compute_windows_zero_or_negative_duration_is_empty():
    assert compute_windows(0.0) == []
    assert compute_windows(-5.0) == []


def test_language_switch_mid_file_is_detected_per_window():
    """The case this module exists for: a call that opens in English and
    switches to Portuguese partway through must get each window decoded in
    its own language, not the language detected at the start."""
    languages_by_window_start = {0.0: ("en", 0.9), 30.0: ("pt", 0.9)}

    def detect_fn(start_s, end_s):
        return languages_by_window_start[start_s]

    def transcribe_fn(start_s, end_s, language):
        return [{"start": start_s, "end": end_s, "text": f"[{language}] hello"}]

    segments = transcribe_with_language_windows(
        60.0, detect_fn, transcribe_fn, window_s=30.0,
    )

    assert [s["language"] for s in segments] == ["en", "pt"]
    assert segments[1]["text"] == "[pt] hello"


def test_low_confidence_window_falls_back_to_previous_language():
    """A near-silent window shouldn't be able to flip the detected language
    on a low-confidence guess — it should keep decoding in whatever language
    the previous confident window settled on."""
    detections = {0.0: ("en", 0.9), 30.0: ("fr", 0.2)}  # low confidence

    def detect_fn(start_s, end_s):
        return detections[start_s]

    calls = []

    def transcribe_fn(start_s, end_s, language):
        calls.append(language)
        return [{"start": start_s, "end": end_s, "text": "x"}]

    transcribe_with_language_windows(
        60.0, detect_fn, transcribe_fn, window_s=30.0, confidence_floor=0.6,
    )

    assert calls == ["en", "en"]  # second window's low-confidence "fr" is ignored


def test_first_window_low_confidence_uses_fallback_language():
    def detect_fn(start_s, end_s):
        return ("de", 0.1)

    def transcribe_fn(start_s, end_s, language):
        return [{"start": start_s, "end": end_s, "text": "x", "language_used": language}]

    segments = transcribe_with_language_windows(
        20.0, detect_fn, transcribe_fn, window_s=30.0, fallback_language="en",
    )

    assert segments[0]["language_used"] == "en"
    assert segments[0]["language"] == "en"


def test_sample_probe_offsets_short_file_is_just_the_start():
    assert sample_probe_offsets(10.0, window_s=30.0) == [0.0]


def test_sample_probe_offsets_long_file_covers_start_middle_end():
    offsets = sample_probe_offsets(1800.0, window_s=30.0)  # 30-minute file, like Rec32
    assert offsets[0] == 0.0
    assert offsets[-1] == 1770.0
    assert len(offsets) == 3


def test_probe_detects_non_english_like_rec32():
    """Rec32 shape: English at the start, Portuguese in the middle and near
    the end — a probe sampling start/middle/end must catch it."""
    by_offset = {0.0: ("en", 0.9), 885.0: ("pt", 0.9), 1770.0: ("en", 0.9)}

    def detect_fn(start_s, end_s):
        return by_offset[start_s]

    assert probe_has_non_english(1800.0, detect_fn) is True


def test_probe_all_english_is_safe_for_parakeet():
    def detect_fn(start_s, end_s):
        return ("en", 0.9)

    assert probe_has_non_english(600.0, detect_fn) is False


def test_probe_low_confidence_non_english_does_not_trigger_reroute():
    """A low-confidence non-English guess (e.g. a near-silent window)
    shouldn't be enough to route an otherwise-English file to Whisper."""
    def detect_fn(start_s, end_s):
        return ("fr", 0.2)

    assert probe_has_non_english(600.0, detect_fn) is False


def test_segment_dicts_are_not_mutated_in_place():
    """transcribe_with_language_windows must not mutate the caller's segment
    dicts — it returns copies tagged with language metadata."""
    original = {"start": 0.0, "end": 1.0, "text": "hi"}

    def detect_fn(start_s, end_s):
        return ("en", 0.9)

    def transcribe_fn(start_s, end_s, language):
        return [original]

    segments = transcribe_with_language_windows(1.0, detect_fn, transcribe_fn)

    assert "language" not in original
    assert segments[0]["language"] == "en"
