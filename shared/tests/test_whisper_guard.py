"""Tests for shared.whisper_guard — anti-hallucination filtering."""

from shared.whisper_guard import (
    _check_repetition_density,
    _count_words,
    _filter_consecutive_dedup,
    _filter_foreign_script,
    _filter_interleaved_dedup,
    _filter_noise_markers,
    _filter_trailing_noise,
    clean_transcript,
)


class TestConsecutiveDedup:
    def test_no_duplicates(self):
        lines = ["Hello", "World", "Foo"]
        assert _filter_consecutive_dedup(lines) == lines

    def test_collapses_identical_lines(self):
        lines = ["Hello", "Hello", "Hello", "World"]
        assert _filter_consecutive_dedup(lines) == ["Hello", "World"]

    def test_preserves_non_consecutive_duplicates(self):
        lines = ["Hello", "World", "Hello"]
        assert _filter_consecutive_dedup(lines) == ["Hello", "World", "Hello"]

    def test_empty(self):
        assert _filter_consecutive_dedup([]) == []

    def test_case_insensitive(self):
        lines = ["Hello world.", "hello world", "Next"]
        assert _filter_consecutive_dedup(lines) == ["Hello world.", "Next"]


class TestInterleavedDedup:
    def test_no_loops(self):
        lines = ["A", "B", "C", "D"]
        assert _filter_interleaved_dedup(lines) == lines

    def test_detects_single_line_loop(self):
        lines = ["A", "A", "A", "A", "A", "B"]
        result = _filter_interleaved_dedup(lines)
        # Should keep one "A" and "B"
        assert result.count("A") == 1
        assert "B" in result

    def test_detects_two_line_loop(self):
        lines = ["A", "B", "A", "B", "A", "B", "C"]
        result = _filter_interleaved_dedup(lines)
        assert len(result) <= 4  # One copy of A,B plus C

    def test_short_input_unchanged(self):
        lines = ["A", "B"]
        assert _filter_interleaved_dedup(lines) == lines


class TestForeignScript:
    def test_english_keeps_latin(self):
        lines = ["Hello world", "This is a test"]
        assert _filter_foreign_script(lines, "en") == lines

    def test_english_removes_cjk(self):
        lines = ["Hello world", "これはテストです", "More English"]
        result = _filter_foreign_script(lines, "en")
        assert "Hello world" in result
        assert "More English" in result
        assert len(result) == 2

    def test_japanese_keeps_cjk(self):
        lines = ["これはテストです", "Hello"]
        result = _filter_foreign_script(lines, "ja")
        assert len(result) == 2  # Both kept (Japanese allows Latin + CJK)

    def test_unknown_language_skips_filter(self):
        lines = ["Hello", "これはテスト"]
        result = _filter_foreign_script(lines, "xx")
        assert len(result) == 2

    def test_preserves_empty_lines(self):
        lines = ["Hello", "", "World"]
        assert _filter_foreign_script(lines, "en") == lines

    def test_short_lines_kept(self):
        # Lines with <3 alpha chars are kept regardless
        lines = ["OK", "これ"]
        result = _filter_foreign_script(lines, "en")
        assert len(result) == 2


class TestNoiseMarkers:
    def test_collapses_consecutive_noise(self):
        lines = ["[Music]", "[music]", "[MUSIC]", "Hello"]
        result = _filter_noise_markers(lines)
        assert len(result) == 2  # One noise marker + "Hello"

    def test_preserves_separated_noise(self):
        lines = ["[Music]", "Hello", "[Music]"]
        result = _filter_noise_markers(lines)
        assert len(result) == 3

    def test_various_markers(self):
        lines = ["[applause]", "[laughter]", "Hello"]
        result = _filter_noise_markers(lines)
        assert len(result) == 2  # Two different markers collapse to first + Hello

    def test_no_noise(self):
        lines = ["Hello", "World"]
        assert _filter_noise_markers(lines) == lines


class TestTrailingNoise:
    def test_removes_hallucination_phrases(self):
        lines = ["Real content", "Thank you for watching"]
        result = _filter_trailing_noise(lines)
        assert result == ["Real content"]

    def test_removes_trailing_noise_markers(self):
        lines = ["Real content", "[Music]"]
        result = _filter_trailing_noise(lines)
        assert result == ["Real content"]

    def test_removes_trailing_empty(self):
        lines = ["Real content", "", ""]
        result = _filter_trailing_noise(lines)
        assert result == ["Real content"]

    def test_removes_multiple_trailing(self):
        lines = ["Content", "thanks for watching", "[music]", ""]
        result = _filter_trailing_noise(lines)
        assert result == ["Content"]

    def test_preserves_mid_content(self):
        lines = ["Thank you for watching", "Real content"]
        result = _filter_trailing_noise(lines)
        assert result == ["Thank you for watching", "Real content"]


class TestWordCount:
    def test_simple(self):
        assert _count_words("Hello world foo") == 3

    def test_ignores_noise_markers(self):
        assert _count_words("[music] Hello world") == 2

    def test_ignores_speaker_labels(self):
        assert _count_words("**Speaker 1:** Hello world") == 2

    def test_ignores_timestamps(self):
        assert _count_words("[00:00-00:45] Hello world") == 2

    def test_empty(self):
        assert _count_words("") == 0


class TestRepetitionDensity:
    def test_no_repetition(self):
        lines = ["A", "B", "C", "D"]
        assert _check_repetition_density(lines) == 0.0

    def test_high_repetition(self):
        lines = ["A", "A", "A", "A", "B"]
        density = _check_repetition_density(lines)
        assert density > 0.5

    def test_short_input(self):
        assert _check_repetition_density(["A"]) == 0.0


class TestCleanTranscript:
    def test_clean_text_unchanged(self):
        text = "Hello world.\nThis is a normal transcript.\nWith multiple lines."
        cleaned, stats = clean_transcript(text)
        assert cleaned == text
        assert not stats.is_likely_hallucination
        assert stats.filters_triggered == []

    def test_removes_consecutive_duplicates(self):
        text = "Hello world.\nHello world.\nHello world.\nReal content here."
        cleaned, stats = clean_transcript(text)
        assert cleaned.count("Hello world.") == 1
        assert "Real content here." in cleaned
        assert "consecutive_dedup" in stats.filters_triggered

    def test_flags_near_empty(self):
        text = "[Music]\n[Music]"
        cleaned, stats = clean_transcript(text, min_words=3)
        assert stats.is_likely_hallucination

    def test_flags_high_repetition(self):
        text = "\n".join(["Same line"] * 20 + ["Unique"])
        cleaned, stats = clean_transcript(text)
        assert stats.is_likely_hallucination
        assert "high_repetition" in stats.filters_triggered

    def test_removes_foreign_script(self):
        text = "Hello world.\nこれはテストです\nMore English content here."
        cleaned, stats = clean_transcript(text, language="en")
        assert "これはテストです" not in cleaned
        assert "foreign_script" in stats.filters_triggered

    def test_trims_trailing_hallucination(self):
        text = "Real meeting content.\nThank you for watching"
        cleaned, stats = clean_transcript(text)
        assert "Thank you for watching" not in cleaned
        assert "trailing_noise" in stats.filters_triggered

    def test_stats_line_counts(self):
        text = "A\nA\nA\nB\nC"
        _, stats = clean_transcript(text)
        assert stats.original_lines == 5
        assert stats.after_consecutive_dedup == 3

    def test_empty_text(self):
        cleaned, stats = clean_transcript("")
        assert stats.is_likely_hallucination
        assert stats.final_word_count == 0
