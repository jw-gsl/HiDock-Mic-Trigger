"""Tests for silence-strip time mapping and timestamp remapping.

Covers the fix for silence-strip timestamp drift: when long silence is
replaced by short padding before ASR, all ASR timestamps land on the
compressed timeline. The strip map returned by strip_silence_with_map lets
remap_time / remap_segments translate them back to the original audio so
diarization and the .srt/_whisper.json/_diarized.json sidecars line up.
"""
from __future__ import annotations

import numpy as np
import pytest

from shared.diarize_lite import (
    _replace_silence_with_padding,
    remap_segments,
    remap_time,
    strip_silence_with_map,
)


# ---------------------------------------------------------------------------
# remap_time — pure math on a hand-built map
# ---------------------------------------------------------------------------
# Scenario: 60s original audio; 30s of silence from t=10 to t=40 was replaced
# by 0.3s padding. Compressed timeline: 0–10 speech, 10–10.3 padding,
# 10.3–30.3 speech (orig 40–60).
SYNTH_MAP = [(0.0, 0.0), (10.0, 10.0), (10.3, 40.0), (30.3, 60.0)]


class TestRemapTime:
    def test_identity_before_strip(self):
        assert remap_time(5.0, SYNTH_MAP) == pytest.approx(5.0)

    def test_boundary_at_strip_start(self):
        assert remap_time(10.0, SYNTH_MAP) == pytest.approx(10.0)

    def test_padding_midpoint_stretches_across_silence(self):
        # Midpoint of the 0.3s padding maps to midpoint of the 30s silence
        assert remap_time(10.15, SYNTH_MAP) == pytest.approx(25.0)

    def test_boundary_at_strip_end(self):
        assert remap_time(10.3, SYNTH_MAP) == pytest.approx(40.0)

    def test_after_strip_offsets_by_stripped_amount(self):
        assert remap_time(20.0, SYNTH_MAP) == pytest.approx(49.7)
        assert remap_time(30.3, SYNTH_MAP) == pytest.approx(60.0)

    def test_extrapolates_past_end_with_slope_one(self):
        # ASR timestamps can slightly exceed the processed extent
        assert remap_time(31.0, SYNTH_MAP) == pytest.approx(60.7)

    def test_extrapolates_before_start(self):
        assert remap_time(-1.0, SYNTH_MAP) == pytest.approx(-1.0)

    def test_empty_map_is_identity(self):
        assert remap_time(12.34, []) == pytest.approx(12.34)

    def test_degenerate_zero_width_knot(self):
        # Zero-length padding (padding_s=0) produces duplicate comp values
        degenerate = [(0.0, 0.0), (5.0, 5.0), (5.0, 20.0), (10.0, 25.0)]
        assert remap_time(7.0, degenerate) == pytest.approx(22.0)

    def test_monotone(self):
        probes = np.linspace(-1.0, 32.0, 200)
        mapped = [remap_time(t, SYNTH_MAP) for t in probes]
        assert all(b >= a for a, b in zip(mapped, mapped[1:]))


# ---------------------------------------------------------------------------
# remap_segments — segments and words, including ones straddling the strip
# ---------------------------------------------------------------------------
class TestRemapSegments:
    def test_segment_straddling_stripped_region(self):
        segments = [
            {"start": 9.0, "end": 11.0, "text": "straddles the gap"},
        ]
        remap_segments(segments, SYNTH_MAP)
        assert segments[0]["start"] == pytest.approx(9.0)
        # 11.0 is 0.7s past the padding end (10.3 → 40.0), so 40.7
        assert segments[0]["end"] == pytest.approx(40.7)

    def test_word_timestamps_remapped(self):
        segments = [
            {
                "start": 9.0, "end": 11.0, "text": "two words",
                "words": [
                    {"word": "two", "start": 9.0, "end": 10.0},
                    {"word": "words", "start": 10.2, "end": 11.0},
                ],
            },
        ]
        remap_segments(segments, SYNTH_MAP)
        words = segments[0]["words"]
        assert words[0]["start"] == pytest.approx(9.0)
        assert words[0]["end"] == pytest.approx(10.0)
        # 10.2 is 2/3 through the padding → 2/3 through the 30s silence
        assert words[1]["start"] == pytest.approx(30.0)
        assert words[1]["end"] == pytest.approx(40.7)

    def test_segments_fully_after_strip(self):
        segments = [{"start": 12.0, "end": 15.0, "text": "later"}]
        remap_segments(segments, SYNTH_MAP)
        assert segments[0]["start"] == pytest.approx(41.7)
        assert segments[0]["end"] == pytest.approx(44.7)

    def test_returns_same_list_and_mutates_in_place(self):
        segments = [{"start": 1.0, "end": 2.0, "text": "x"}]
        out = remap_segments(segments, SYNTH_MAP)
        assert out is segments

    def test_empty_map_no_change(self):
        segments = [{"start": 1.0, "end": 2.0, "text": "x"}]
        remap_segments(segments, [])
        assert segments[0]["start"] == 1.0
        assert segments[0]["end"] == 2.0

    def test_segments_without_words_key(self):
        segments = [{"start": 0.5, "end": 1.5, "text": "no words key"}]
        remap_segments(segments, SYNTH_MAP)  # must not raise
        assert segments[0]["end"] == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# strip_silence_with_map — synthetic audio end-to-end
# ---------------------------------------------------------------------------
SR = 16000
CHUNK_S = 0.05


def _synthetic_audio() -> np.ndarray:
    """2s loud sine + 3s near-silence + 2s loud sine at 16 kHz.

    The quiet middle uses tiny uniform noise (not exact zeros) so the
    adaptive noise floor (quietest-20% RMS x4) classifies it as silence.
    """
    rng = np.random.default_rng(0)
    t1 = np.arange(2 * SR) / SR
    loud1 = 0.5 * np.sin(2 * np.pi * 440 * t1)
    quiet = rng.uniform(-1e-3, 1e-3, 3 * SR)
    t2 = np.arange(2 * SR) / SR
    loud2 = 0.5 * np.sin(2 * np.pi * 330 * t2)
    return np.concatenate([loud1, quiet, loud2]).astype(np.float32)


class TestStripSilenceWithMap:
    def test_audio_matches_legacy_function(self):
        audio = _synthetic_audio()
        processed, _ = strip_silence_with_map(audio, sr=SR)
        legacy = _replace_silence_with_padding(audio, sr=SR)
        assert np.array_equal(processed, legacy)

    def test_expected_compression(self):
        # Default threshold 0.5s → 10 chunks needed → 9 silence chunks kept
        # (0.45s) + 0.3s padding; the rest of the 3s silence is dropped.
        # Kept: 2.0 + 0.45 + 0.3 + 2.0 = 4.75s
        audio = _synthetic_audio()
        processed, _ = strip_silence_with_map(audio, sr=SR)
        assert len(processed) == int(4.75 * SR)

    def test_map_knots(self):
        audio = _synthetic_audio()
        _, time_map = strip_silence_with_map(audio, sr=SR)
        assert time_map[0] == (0.0, 0.0)
        # Padding emitted after 2.0s speech + 0.45s kept silence
        assert any(
            c == pytest.approx(2.45) and o == pytest.approx(2.45)
            for c, o in time_map
        )
        # Padding closes where speech resumes: comp 2.75 → orig 5.0
        assert any(
            c == pytest.approx(2.75) and o == pytest.approx(5.0)
            for c, o in time_map
        )
        # Final knot covers the full extents
        assert time_map[-1][0] == pytest.approx(4.75)
        assert time_map[-1][1] == pytest.approx(7.0)

    def test_remap_restores_original_positions(self):
        audio = _synthetic_audio()
        _, time_map = strip_silence_with_map(audio, sr=SR)
        # Before the strip: identity
        assert remap_time(1.0, time_map) == pytest.approx(1.0)
        assert remap_time(2.45, time_map) == pytest.approx(2.45)
        # Speech resumes at comp 2.75 == orig 5.0, slope 1 afterwards
        assert remap_time(2.75, time_map) == pytest.approx(5.0)
        assert remap_time(3.75, time_map) == pytest.approx(6.0)
        assert remap_time(4.75, time_map) == pytest.approx(7.0)
        # Inside the padding: linear stretch across the dropped silence
        assert remap_time(2.6, time_map) == pytest.approx(2.45 + 0.5 * (5.0 - 2.45))

    def test_map_monotone_both_axes(self):
        audio = _synthetic_audio()
        _, time_map = strip_silence_with_map(audio, sr=SR)
        comps = [c for c, _ in time_map]
        origs = [o for _, o in time_map]
        assert comps == sorted(comps)
        assert origs == sorted(origs)

    def test_no_strip_returns_identity_map(self):
        # Quiet runs shorter than the 0.5s threshold are never replaced, so
        # alternating 0.4s speech / 0.3s quiet leaves the audio untouched
        # and the map must be the identity.
        rng = np.random.default_rng(2)
        t = np.arange(int(0.4 * SR)) / SR
        loud = 0.5 * np.sin(2 * np.pi * 440 * t)
        quiet = rng.uniform(-1e-3, 1e-3, int(0.3 * SR))
        audio = np.concatenate([loud, quiet] * 4).astype(np.float32)
        processed, time_map = strip_silence_with_map(audio, sr=SR)
        assert len(processed) == len(audio)
        # Identity map: every knot has comp == orig
        for c, o in time_map:
            assert c == pytest.approx(o)
        assert remap_time(1.234, time_map) == pytest.approx(1.234)

    def test_short_audio_returns_identity(self):
        audio = np.zeros(100, dtype=np.float32)  # shorter than one 50ms chunk
        processed, time_map = strip_silence_with_map(audio, sr=SR)
        assert np.array_equal(processed, audio)
        assert remap_time(0.001, time_map) == pytest.approx(0.001)

    def test_trailing_silence_maps_padding_to_end(self):
        # 2s loud + 3s trailing quiet: the padding at the end must map up to
        # the end of the processed extent (7s minus any sub-chunk tail).
        rng = np.random.default_rng(1)
        t = np.arange(2 * SR) / SR
        loud = 0.5 * np.sin(2 * np.pi * 440 * t)
        quiet = rng.uniform(-1e-3, 1e-3, 3 * SR)
        audio = np.concatenate([loud, quiet]).astype(np.float32)
        processed, time_map = strip_silence_with_map(audio, sr=SR)
        comp_end = len(processed) / SR
        assert time_map[-1][0] == pytest.approx(comp_end)
        assert time_map[-1][1] == pytest.approx(5.0)
        # End of the compressed audio maps to the end of the original
        assert remap_time(comp_end, time_map) == pytest.approx(5.0)
