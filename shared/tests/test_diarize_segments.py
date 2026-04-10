"""Tests for diarization segment handling functions in shared.diarize_lite."""
from __future__ import annotations

from shared.diarize_lite import (
    _filter_hallucinations,
    _split_long_segments,
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
# _split_long_segments
# ---------------------------------------------------------------------------
class TestSplitLongSegments:
    def test_short_segment_unchanged(self):
        segments = [{"start": 0, "end": 30, "text": "Short segment.", "speaker_id": 0}]
        result = _split_long_segments(segments, max_duration=90)
        assert len(result) == 1

    def test_long_segment_split(self):
        # 200s segment with multiple sentences should be split
        text = "First sentence. Second sentence. Third sentence. Fourth sentence."
        segments = [{"start": 0, "end": 200, "text": text, "speaker_id": 0}]
        result = _split_long_segments(segments, max_duration=90)
        assert len(result) >= 2
        # All text should be preserved
        combined = " ".join(s["text"] for s in result)
        for word in ["First", "Second", "Third", "Fourth"]:
            assert word in combined

    def test_single_sentence_not_split(self):
        segments = [{"start": 0, "end": 200, "text": "One long sentence without periods", "speaker_id": 0}]
        result = _split_long_segments(segments, max_duration=90)
        assert len(result) == 1  # Can't split without sentence boundaries

    def test_empty_segments(self):
        result = _split_long_segments([])
        assert result == []

    def test_preserves_speaker_id(self):
        text = "First part. Second part. Third part. Fourth part."
        segments = [{"start": 0, "end": 200, "text": text, "speaker_id": 2, "speaker": "Speaker 3"}]
        result = _split_long_segments(segments, max_duration=90)
        for seg in result:
            assert seg["speaker_id"] == 2

    def test_time_ranges_continuous(self):
        text = "A sentence here. Another one here. And a third. Plus a fourth one."
        segments = [{"start": 10, "end": 210, "text": text, "speaker_id": 0}]
        result = _split_long_segments(segments, max_duration=90)
        if len(result) > 1:
            assert result[0]["start"] == 10
            assert result[-1]["end"] == 210
            # Each segment starts where the previous ended
            for i in range(1, len(result)):
                assert abs(result[i]["start"] - result[i-1]["end"]) < 0.1


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
