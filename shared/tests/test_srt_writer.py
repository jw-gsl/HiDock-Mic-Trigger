"""Tests for shared.srt_writer."""
from __future__ import annotations

from pathlib import Path

from shared.srt_writer import (
    _format_srt_timestamp,
    format_srt,
    srt_path_for,
    write_srt,
)


class TestFormatTimestamp:
    def test_zero(self):
        assert _format_srt_timestamp(0) == "00:00:00,000"

    def test_sub_second(self):
        assert _format_srt_timestamp(0.123) == "00:00:00,123"

    def test_minute_boundary(self):
        assert _format_srt_timestamp(60) == "00:01:00,000"

    def test_hour_boundary(self):
        assert _format_srt_timestamp(3600) == "01:00:00,000"

    def test_mixed(self):
        assert _format_srt_timestamp(3661.5) == "01:01:01,500"

    def test_millisecond_rounding(self):
        # 0.9995s rounds to 1000ms, bumping seconds to 1
        assert _format_srt_timestamp(0.9995) == "00:00:01,000"

    def test_negative_clamped(self):
        assert _format_srt_timestamp(-1) == "00:00:00,000"


def _diarized(segments, speaker_names=None):
    return {"segments": segments, "speaker_names": speaker_names or {}}


class TestFormatSrtDiarized:
    def test_basic_diarized(self):
        diarized = _diarized(
            [
                {"start": 0.0, "end": 2.5, "speaker": "SPEAKER_00", "text": "Hello there."},
                {"start": 2.5, "end": 5.0, "speaker": "SPEAKER_01", "text": "General Kenobi."},
            ],
            {"SPEAKER_00": "Obi-Wan", "SPEAKER_01": "Grievous"},
        )
        out = format_srt(diarized_result=diarized)
        assert out == (
            "1\n"
            "00:00:00,000 --> 00:00:02,500\n"
            "Obi-Wan: Hello there.\n"
            "\n"
            "2\n"
            "00:00:02,500 --> 00:00:05,000\n"
            "Grievous: General Kenobi.\n"
        )

    def test_include_speakers_false(self):
        diarized = _diarized(
            [{"start": 1.0, "end": 2.0, "speaker": "SPEAKER_00", "text": "Hi."}],
            {"SPEAKER_00": "Alice"},
        )
        out = format_srt(diarized_result=diarized, include_speakers=False)
        # Plain caption: no speaker prefix
        assert "Alice" not in out
        assert "Hi." in out

    def test_raw_speaker_when_no_name_mapping(self):
        diarized = _diarized(
            [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_03", "text": "Anon."}],
        )
        out = format_srt(diarized_result=diarized)
        assert "SPEAKER_03: Anon." in out

    def test_skips_empty_text(self):
        diarized = _diarized(
            [
                {"start": 0.0, "end": 1.0, "speaker": "X", "text": "  "},
                {"start": 1.0, "end": 2.0, "speaker": "X", "text": "Real."},
            ],
        )
        out = format_srt(diarized_result=diarized)
        # Empty segment skipped; renumbered from 1
        assert out.startswith("1\n")
        assert out.count("-->") == 1
        assert "Real." in out

    def test_skips_segments_without_timings(self):
        diarized = _diarized(
            [
                {"speaker": "X", "text": "no times"},
                {"start": 0.0, "end": 1.0, "speaker": "X", "text": "has times"},
            ],
        )
        out = format_srt(diarized_result=diarized)
        assert out.count("-->") == 1
        assert "has times" in out
        assert "no times" not in out

    def test_zero_duration_segment_nudged(self):
        # Some diarization backends produce zero-duration segments — ensure the
        # end is pushed past start so VLC et al. accept the cue.
        diarized = _diarized(
            [{"start": 5.0, "end": 5.0, "speaker": "X", "text": "Blip."}],
        )
        out = format_srt(diarized_result=diarized)
        assert "00:00:05,000 --> 00:00:05,500" in out


class TestFormatSrtWhisperFallback:
    def test_whisper_segments_no_speakers(self):
        segs = [
            {"start": 0.0, "end": 1.2, "text": "First."},
            {"start": 1.2, "end": 2.4, "text": "Second."},
        ]
        out = format_srt(whisper_segments=segs)
        assert "1\n00:00:00,000 --> 00:00:01,200\nFirst." in out
        assert "2\n00:00:01,200 --> 00:00:02,400\nSecond." in out

    def test_empty_input_returns_empty(self):
        assert format_srt() == ""
        assert format_srt(diarized_result={"segments": []}) == ""
        assert format_srt(whisper_segments=[]) == ""


class TestWriteSrt:
    def test_writes_file(self, tmp_path: Path):
        diarized = _diarized(
            [{"start": 0.0, "end": 1.0, "speaker": "X", "text": "Hi."}],
            {"X": "Alice"},
        )
        out_path = tmp_path / "clip.srt"
        result = write_srt(out_path, diarized_result=diarized)
        assert result == out_path
        assert out_path.exists()
        content = out_path.read_text(encoding="utf-8")
        assert "Alice: Hi." in content

    def test_returns_none_when_nothing_to_write(self, tmp_path: Path):
        out_path = tmp_path / "empty.srt"
        assert write_srt(out_path, diarized_result={"segments": []}) is None
        assert not out_path.exists()

    def test_creates_parent_directory(self, tmp_path: Path):
        diarized = _diarized(
            [{"start": 0.0, "end": 1.0, "speaker": "X", "text": "Hi."}],
        )
        out_path = tmp_path / "nested" / "dir" / "clip.srt"
        write_srt(out_path, diarized_result=diarized)
        assert out_path.exists()

    def test_srt_path_for(self):
        assert srt_path_for(Path("/tmp/foo.md")) == Path("/tmp/foo.srt")
        assert srt_path_for(Path("/tmp/bar.txt")) == Path("/tmp/bar.srt")
