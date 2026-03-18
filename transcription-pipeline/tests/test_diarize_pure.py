"""Tests for pure functions in diarize.py — no audio or ML models needed."""
from diarize import _format_time


class TestFormatTime:
    def test_zero(self):
        assert _format_time(0) == "00:00"

    def test_sixty_seconds(self):
        assert _format_time(60) == "01:00"

    def test_ninety_point_seven(self):
        # int(90.7) = 90 → 1:30
        assert _format_time(90.7) == "01:30"


class TestAssignTextToSegments:
    def test_with_mock_diarization(self):
        """Test _assign_text_to_segments using a lightweight mock."""
        from diarize import _assign_text_to_segments

        # Minimal mock of pyannote Annotation tracks
        class MockSegment:
            def __init__(self, start, end):
                self.start = start
                self.end = end

        class MockDiarization:
            def itertracks(self, yield_label=False):
                return [
                    (MockSegment(0.0, 5.0), None, "SPEAKER_00"),
                    (MockSegment(5.0, 10.0), None, "SPEAKER_01"),
                ]

        whisper_segments = [
            {"start": 0.0, "end": 5.0, "text": "Hello world"},
            {"start": 5.0, "end": 10.0, "text": "Goodbye"},
        ]

        result = _assign_text_to_segments(MockDiarization(), whisper_segments)
        assert len(result) == 2
        assert result[0]["speaker"] == "SPEAKER_00"
        assert "Hello world" in result[0]["text"]
        assert result[1]["speaker"] == "SPEAKER_01"
        assert "Goodbye" in result[1]["text"]

    def test_no_overlap_produces_empty_text(self):
        from diarize import _assign_text_to_segments

        class MockSegment:
            def __init__(self, start, end):
                self.start = start
                self.end = end

        class MockDiarization:
            def itertracks(self, yield_label=False):
                return [(MockSegment(100.0, 200.0), None, "SPEAKER_00")]

        whisper_segments = [{"start": 0.0, "end": 5.0, "text": "Hello"}]
        result = _assign_text_to_segments(MockDiarization(), whisper_segments)
        assert len(result) == 1
        assert result[0]["text"] == ""
