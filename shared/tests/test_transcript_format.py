"""Tests for transcript markdown generation in shared.transcript_writer.

Covers YAML frontmatter, model descriptions, timestamped segments,
diarized segments, auto-title, and frontmatter round-trip parsing.
"""
from __future__ import annotations

from shared.transcript_writer import (
    _format_timestamp,
    _format_timestamped_segments,
    _model_description,
    _MODEL_DESCRIPTIONS,
    _yaml_escape,
    _yaml_list,
    auto_title,
    build_frontmatter,
    extract_speakers_from_diarized,
    format_diarized_transcript,
    parse_frontmatter,
)


# ---------------------------------------------------------------------------
# Model description mapping
# ---------------------------------------------------------------------------
class TestModelDescription:
    def test_known_models(self):
        assert "turbo" in _model_description("large-v3-turbo").lower()
        assert "1.55B" in _model_description("large-v3")
        assert "small" in _model_description("small").lower()

    def test_unknown_model_returns_key(self):
        assert _model_description("custom-model") == "custom-model"

    def test_all_registry_entries_have_descriptions(self):
        for key in _MODEL_DESCRIPTIONS:
            desc = _model_description(key)
            assert len(desc) > len(key)


# ---------------------------------------------------------------------------
# YAML helpers
# ---------------------------------------------------------------------------
class TestYamlEscape:
    def test_plain_string(self):
        assert _yaml_escape("hello") == "hello"

    def test_empty_string(self):
        assert _yaml_escape("") == '""'

    def test_string_with_colon(self):
        result = _yaml_escape("key: value")
        assert result.startswith('"')
        assert result.endswith('"')

    def test_string_with_quotes(self):
        """Double quotes with single quote trigger YAML quoting."""
        result = _yaml_escape("say 'hello'")
        # Single quote is a special YAML char, triggers quoting
        assert result.startswith('"')

    def test_string_starting_with_dash(self):
        result = _yaml_escape("-item")
        assert result.startswith('"')


class TestYamlList:
    def test_empty_list(self):
        assert _yaml_list([]) == "[]"

    def test_short_list_inline(self):
        result = _yaml_list(["Alice", "Bob"])
        assert result.startswith("[")
        assert result.endswith("]")
        assert "Alice" in result

    def test_long_list_block(self):
        items = [f"Very long speaker name number {i}" for i in range(10)]
        result = _yaml_list(items)
        assert result.startswith("\n")
        assert "- " in result


# ---------------------------------------------------------------------------
# Frontmatter generation
# ---------------------------------------------------------------------------
class TestBuildFrontmatter:
    def test_all_fields_present(self):
        fm = build_frontmatter(
            title="Quarterly Review",
            doc_type="meeting",
            date="2026-04-05T14:00:00+00:00",
            duration=234.5,
            speakers=["Alice", "Bob"],
            source_device="HiDock H1",
            source_file="recording.mp3",
            model="large-v3-turbo",
            action_items=[{"task": "Review PR", "assignee": "Alice", "status": "open"}],
            decisions=[{"text": "Approved budget", "topic": "finance"}],
            key_points=["Budget on track", "Hiring plan approved"],
            tags=["quarterly", "finance"],
        )
        assert fm.startswith("---")
        assert fm.endswith("---")
        assert "title:" in fm
        assert "type: meeting" in fm
        assert "date: 2026-04-05T14:00:00+00:00" in fm
        assert "duration: 234.5" in fm
        assert "Alice" in fm
        assert "Bob" in fm
        assert "HiDock H1" in fm
        assert "recording.mp3" in fm
        assert "large-v3-turbo" in fm or "809M" in fm
        assert "Review PR" in fm
        assert "Approved budget" in fm
        assert "Budget on track" in fm
        assert "quarterly" in fm

    def test_minimal_frontmatter(self):
        fm = build_frontmatter(title="Minimal", date="2026-01-01T00:00:00+00:00")
        assert "title: Minimal" in fm
        assert "action_items: []" in fm
        assert "decisions: []" in fm
        assert "key_points: []" in fm
        assert "tags: []" in fm

    def test_no_duration_omits_field(self):
        fm = build_frontmatter(title="Test", date="2026-01-01T00:00:00+00:00")
        assert "duration:" not in fm

    def test_no_source_device_omits_field(self):
        fm = build_frontmatter(title="Test", date="2026-01-01T00:00:00+00:00")
        assert "source_device:" not in fm

    def test_model_expanded_to_description(self):
        fm = build_frontmatter(
            title="Test",
            date="2026-01-01T00:00:00+00:00",
            model="large-v3-turbo",
        )
        assert "809M" in fm  # from the model description


# ---------------------------------------------------------------------------
# Timestamp formatting
# ---------------------------------------------------------------------------
class TestFormatTimestamp:
    def test_zero(self):
        assert _format_timestamp(0) == "00:00"

    def test_one_minute(self):
        assert _format_timestamp(60) == "01:00"

    def test_with_hours(self):
        assert _format_timestamp(3661) == "1:01:01"

    def test_fractional_seconds(self):
        assert _format_timestamp(90.7) == "01:30"


# ---------------------------------------------------------------------------
# Timestamped segment formatting (non-diarized)
# ---------------------------------------------------------------------------
class TestFormatTimestampedSegments:
    def test_basic_segments(self):
        segments = [
            {"start": 0, "text": "Hello world"},
            {"start": 65, "text": "Next part"},
        ]
        result = _format_timestamped_segments(segments)
        assert "[00:00] Hello world" in result
        assert "[01:05] Next part" in result

    def test_empty_text_skipped(self):
        segments = [
            {"start": 0, "text": "Real"},
            {"start": 5, "text": ""},
            {"start": 10, "text": "Also real"},
        ]
        result = _format_timestamped_segments(segments)
        lines = [l for l in result.split("\n") if l.strip()]
        assert len(lines) == 2

    def test_no_start_omits_timestamp(self):
        segments = [{"text": "No timestamp"}]
        result = _format_timestamped_segments(segments)
        assert result == "No timestamp"

    def test_empty_segments(self):
        assert _format_timestamped_segments([]) == ""

    def test_segments_separated_by_blank_line(self):
        segments = [
            {"start": 0, "text": "A"},
            {"start": 5, "text": "B"},
        ]
        result = _format_timestamped_segments(segments)
        assert "\n\n" in result


# ---------------------------------------------------------------------------
# Diarized segment formatting
# ---------------------------------------------------------------------------
class TestFormatDiarizedTranscript:
    def test_speaker_labels(self):
        result = {
            "segments": [
                {"speaker": "Speaker 1", "text": "Hello", "start": 0, "end": 1},
                {"speaker": "Speaker 2", "text": "Hi", "start": 1, "end": 2},
            ],
            "speaker_names": {"Speaker 1": "Alice", "Speaker 2": "Bob"},
        }
        text = format_diarized_transcript(result)
        assert "**Alice:**" in text
        assert "**Bob:**" in text

    def test_includes_timestamps(self):
        result = {
            "segments": [
                {"speaker": "Speaker 1", "text": "Hello", "start": 0, "end": 45},
            ],
            "speaker_names": {"Speaker 1": "Speaker 1"},
        }
        text = format_diarized_transcript(result)
        assert "[00:00-00:45]" in text

    def test_consecutive_same_speaker_no_repeat_label(self):
        result = {
            "segments": [
                {"speaker": "Speaker 1", "text": "Part one", "start": 0, "end": 1},
                {"speaker": "Speaker 1", "text": "Part two", "start": 1, "end": 2},
            ],
            "speaker_names": {"Speaker 1": "Alice"},
        }
        text = format_diarized_transcript(result)
        assert text.count("**Alice:**") == 1

    def test_empty_result(self):
        assert format_diarized_transcript(None) == ""
        assert format_diarized_transcript({}) == ""
        assert format_diarized_transcript({"segments": []}) == ""

    def test_skips_empty_text(self):
        result = {
            "segments": [
                {"speaker": "Speaker 1", "text": "", "start": 0, "end": 1},
                {"speaker": "Speaker 1", "text": "Real text", "start": 1, "end": 2},
            ],
            "speaker_names": {"Speaker 1": "Speaker 1"},
        }
        text = format_diarized_transcript(result)
        assert "Real text" in text


# ---------------------------------------------------------------------------
# Auto-title generation
# ---------------------------------------------------------------------------
class TestAutoTitle:
    def test_empty_input(self):
        assert auto_title("") == "Untitled recording"
        assert auto_title("   \n  ") == "Untitled recording"

    def test_short_text(self):
        assert auto_title("Hello world") == "Hello world"

    def test_truncation(self):
        long = " ".join(f"word{i}" for i in range(20))
        title = auto_title(long, max_words=5)
        assert title.endswith("...")
        assert len(title.split()) <= 6  # 5 words + "..."

    def test_strips_speaker_labels(self):
        text = "**Speaker 1:** Let's review the budget"
        title = auto_title(text)
        assert "**" not in title
        assert "budget" in title

    def test_strips_timestamps(self):
        text = "[00:00-01:30] We need to discuss hiring"
        title = auto_title(text)
        assert "[00:00" not in title
        assert "hiring" in title

    def test_multiline_uses_first_line(self):
        text = "First line here\nSecond line here\nThird"
        title = auto_title(text)
        assert "First line" in title
        assert "Second" not in title


# ---------------------------------------------------------------------------
# Frontmatter round-trip parsing
# ---------------------------------------------------------------------------
class TestFrontmatterRoundTrip:
    def test_basic_roundtrip(self):
        fm = build_frontmatter(
            title="Test Meeting",
            date="2026-04-05T14:00:00+00:00",
            duration=120.5,
            speakers=["Alice", "Bob"],
            tags=["engineering", "review"],
        )
        full = fm + "\n\n## Transcript\n\nHello world"
        meta, body = parse_frontmatter(full)
        assert meta["title"] == "Test Meeting"
        assert meta["type"] == "meeting"
        assert meta["duration"] == 120.5
        assert meta["speakers"] == ["Alice", "Bob"]
        assert meta["tags"] == ["engineering", "review"]
        assert "Hello world" in body

    def test_empty_lists_roundtrip(self):
        fm = build_frontmatter(title="Empty Lists", date="2026-01-01T00:00:00+00:00")
        meta, _ = parse_frontmatter(fm + "\n\nBody")
        assert meta["action_items"] == []
        assert meta["decisions"] == []
        assert meta["key_points"] == []
        assert meta["tags"] == []

    def test_no_frontmatter(self):
        meta, body = parse_frontmatter("Just plain text")
        assert meta == {}
        assert body == "Just plain text"

    def test_with_action_items_roundtrip(self):
        fm = build_frontmatter(
            title="With Actions",
            date="2026-01-01T00:00:00+00:00",
            action_items=[
                {"task": "Review PR", "assignee": "Alice", "status": "open"},
            ],
        )
        meta, _ = parse_frontmatter(fm + "\n\nBody")
        assert isinstance(meta["action_items"], list)
        assert len(meta["action_items"]) == 1
        assert meta["action_items"][0]["task"] == "Review PR"

    def test_with_decisions_roundtrip(self):
        fm = build_frontmatter(
            title="With Decisions",
            date="2026-01-01T00:00:00+00:00",
            decisions=[{"text": "Ship it", "topic": "release"}],
        )
        meta, _ = parse_frontmatter(fm + "\n\nBody")
        assert isinstance(meta["decisions"], list)
        assert len(meta["decisions"]) == 1
        assert meta["decisions"][0]["text"] == "Ship it"

    def test_speakers_from_diarized(self):
        diarized = {
            "segments": [
                {"speaker": "Speaker 1", "text": "Hello"},
                {"speaker": "Speaker 2", "text": "Hi"},
                {"speaker": "Speaker 1", "text": "How are you"},
            ],
            "speaker_names": {"Speaker 1": "Alice", "Speaker 2": "Bob"},
        }
        speakers = extract_speakers_from_diarized(diarized)
        assert speakers == ["Alice", "Bob"]
