"""Tests for diarization segment handling functions in shared.diarize_lite."""
from __future__ import annotations

from shared.diarize_lite import (
    _filter_hallucinations,
    _merge_whisper_segments,
)


# ---------------------------------------------------------------------------
# _filter_hallucinations
# ---------------------------------------------------------------------------
class TestFilterHallucinations:
    def test_no_hallucination(self):
        segments = [
            {"start": 0, "end": 1, "text": "Hello world"},
            {"start": 1, "end": 2, "text": "How are you"},
            {"start": 2, "end": 3, "text": "I am fine"},
            {"start": 3, "end": 4, "text": "Great to hear"},
        ]
        result = _filter_hallucinations(segments)
        assert len(result) == 4

    def test_repeated_short_segments_at_end(self):
        segments = [
            {"start": 0, "end": 1, "text": "Real content here"},
            {"start": 1, "end": 2, "text": "Thank you."},
            {"start": 2, "end": 3, "text": "Thank you."},
            {"start": 3, "end": 4, "text": "Thank you."},
        ]
        result = _filter_hallucinations(segments)
        assert len(result) == 1
        assert result[0]["text"] == "Real content here"

    def test_repeated_case_insensitive(self):
        segments = [
            {"start": 0, "end": 1, "text": "Some real talk"},
            {"start": 1, "end": 2, "text": "Thanks."},
            {"start": 2, "end": 3, "text": "thanks."},
            {"start": 3, "end": 4, "text": "THANKS."},
        ]
        result = _filter_hallucinations(segments)
        assert len(result) == 1

    def test_too_few_segments_no_filter(self):
        """With fewer than max_repeats+1 segments, no filtering occurs."""
        segments = [
            {"start": 0, "end": 1, "text": "ok"},
            {"start": 1, "end": 2, "text": "ok"},
            {"start": 2, "end": 3, "text": "ok"},
        ]
        # default max_repeats=3, need at least 4 segments
        result = _filter_hallucinations(segments)
        assert len(result) == 3

    def test_long_repeated_text_not_filtered(self):
        """Repeated segments >= 20 chars are not treated as hallucinations."""
        long_text = "This is a fairly long segment text"
        segments = [
            {"start": 0, "end": 1, "text": "Real content"},
            {"start": 1, "end": 2, "text": long_text},
            {"start": 2, "end": 3, "text": long_text},
            {"start": 3, "end": 4, "text": long_text},
        ]
        result = _filter_hallucinations(segments)
        assert len(result) == 4

    def test_empty_segments(self):
        result = _filter_hallucinations([])
        assert result == []

    def test_single_segment(self):
        segments = [{"start": 0, "end": 1, "text": "Hello"}]
        result = _filter_hallucinations(segments)
        assert len(result) == 1

    def test_custom_max_repeats(self):
        segments = [
            {"start": 0, "end": 1, "text": "Real"},
            {"start": 1, "end": 2, "text": "bye"},
            {"start": 2, "end": 3, "text": "bye"},
        ]
        result = _filter_hallucinations(segments, max_repeats=2)
        assert len(result) == 1
        assert result[0]["text"] == "Real"


# ---------------------------------------------------------------------------
# _merge_whisper_segments
# ---------------------------------------------------------------------------
class TestMergeWhisperSegments:
    def test_no_merge_when_large_gap(self):
        segments = [
            {"start": 0, "end": 1, "text": "Hello"},
            {"start": 5, "end": 6, "text": "World"},
        ]
        result = _merge_whisper_segments(segments, max_gap=1.5)
        assert len(result) == 2
        assert result[0]["text"] == "Hello"
        assert result[1]["text"] == "World"

    def test_merge_small_gap(self):
        segments = [
            {"start": 0, "end": 1, "text": "Hello"},
            {"start": 1.5, "end": 2.5, "text": "World"},
        ]
        result = _merge_whisper_segments(segments, max_gap=1.5)
        assert len(result) == 1
        assert result[0]["text"] == "Hello World"
        assert result[0]["start"] == 0
        assert result[0]["end"] == 2.5

    def test_merge_multiple_into_one(self):
        segments = [
            {"start": 0, "end": 1, "text": "One"},
            {"start": 1.2, "end": 2, "text": "Two"},
            {"start": 2.3, "end": 3, "text": "Three"},
        ]
        result = _merge_whisper_segments(segments, max_gap=1.5)
        assert len(result) == 1
        assert result[0]["text"] == "One Two Three"

    def test_partial_merge(self):
        segments = [
            {"start": 0, "end": 1, "text": "A"},
            {"start": 1.2, "end": 2, "text": "B"},
            {"start": 10, "end": 11, "text": "C"},
            {"start": 11.2, "end": 12, "text": "D"},
        ]
        result = _merge_whisper_segments(segments, max_gap=1.5)
        assert len(result) == 2
        assert result[0]["text"] == "A B"
        assert result[1]["text"] == "C D"

    def test_empty_segments(self):
        result = _merge_whisper_segments([])
        assert result == []

    def test_single_segment(self):
        segments = [{"start": 0, "end": 1, "text": "Only"}]
        result = _merge_whisper_segments(segments)
        assert len(result) == 1
        assert result[0]["text"] == "Only"

    def test_strips_text(self):
        segments = [
            {"start": 0, "end": 1, "text": "  Hello  "},
            {"start": 1.1, "end": 2, "text": "  World  "},
        ]
        result = _merge_whisper_segments(segments, max_gap=1.5)
        assert result[0]["text"] == "Hello World"

    def test_exact_gap_boundary(self):
        """Gap exactly equal to max_gap should merge (<=)."""
        segments = [
            {"start": 0, "end": 1, "text": "A"},
            {"start": 2.5, "end": 3, "text": "B"},
        ]
        result = _merge_whisper_segments(segments, max_gap=1.5)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Speaker ID renumbering (tested via diarize integration helpers)
# ---------------------------------------------------------------------------
class TestSpeakerRenumbering:
    """Test the renumbering logic that ensures speaker IDs are contiguous 0,1,2..."""

    def test_renumber_contiguous(self):
        """Simulate the renumbering logic from diarize()."""
        ws_speakers = [3, 3, 1, 1, 3, 0]
        # Renumber by first appearance
        seen_ids: list[int] = []
        for spk_id in ws_speakers:
            if spk_id not in seen_ids:
                seen_ids.append(spk_id)
        id_map = {old: new for new, old in enumerate(seen_ids)}
        renumbered = [id_map[s] for s in ws_speakers]
        assert renumbered == [0, 0, 1, 1, 0, 2]

    def test_renumber_already_contiguous(self):
        ws_speakers = [0, 1, 2, 0]
        seen_ids: list[int] = []
        for spk_id in ws_speakers:
            if spk_id not in seen_ids:
                seen_ids.append(spk_id)
        id_map = {old: new for new, old in enumerate(seen_ids)}
        renumbered = [id_map[s] for s in ws_speakers]
        assert renumbered == [0, 1, 2, 0]

    def test_renumber_single_speaker(self):
        ws_speakers = [5, 5, 5]
        seen_ids: list[int] = []
        for spk_id in ws_speakers:
            if spk_id not in seen_ids:
                seen_ids.append(spk_id)
        id_map = {old: new for new, old in enumerate(seen_ids)}
        renumbered = [id_map[s] for s in ws_speakers]
        assert renumbered == [0, 0, 0]


# ---------------------------------------------------------------------------
# Merge consecutive same-speaker segments (logic from diarize())
# ---------------------------------------------------------------------------
class TestMergeConsecutiveSameSpeaker:
    def _merge(self, raw_segments: list[dict]) -> list[dict]:
        """Replicate the merge-consecutive-same-speaker logic from diarize()."""
        segments_out: list[dict] = []
        for seg in raw_segments:
            if not seg["text"]:
                continue
            if segments_out and segments_out[-1]["speaker_id"] == seg["speaker_id"]:
                segments_out[-1]["end"] = seg["end"]
                segments_out[-1]["text"] += " " + seg["text"]
            else:
                segments_out.append(dict(seg))
        return segments_out

    def test_merge_same_speaker(self):
        raw = [
            {"start": 0, "end": 1, "text": "Hello", "speaker": "Speaker 1", "speaker_id": 0},
            {"start": 1, "end": 2, "text": "there", "speaker": "Speaker 1", "speaker_id": 0},
            {"start": 2, "end": 3, "text": "Hi", "speaker": "Speaker 2", "speaker_id": 1},
        ]
        result = self._merge(raw)
        assert len(result) == 2
        assert result[0]["text"] == "Hello there"
        assert result[0]["end"] == 2
        assert result[1]["text"] == "Hi"

    def test_no_merge_different_speakers(self):
        raw = [
            {"start": 0, "end": 1, "text": "A", "speaker": "Speaker 1", "speaker_id": 0},
            {"start": 1, "end": 2, "text": "B", "speaker": "Speaker 2", "speaker_id": 1},
            {"start": 2, "end": 3, "text": "C", "speaker": "Speaker 1", "speaker_id": 0},
        ]
        result = self._merge(raw)
        assert len(result) == 3

    def test_skip_empty_text(self):
        raw = [
            {"start": 0, "end": 1, "text": "A", "speaker": "Speaker 1", "speaker_id": 0},
            {"start": 1, "end": 2, "text": "", "speaker": "Speaker 1", "speaker_id": 0},
            {"start": 2, "end": 3, "text": "B", "speaker": "Speaker 1", "speaker_id": 0},
        ]
        result = self._merge(raw)
        assert len(result) == 1
        assert result[0]["text"] == "A B"

    def test_empty_input(self):
        result = self._merge([])
        assert result == []
