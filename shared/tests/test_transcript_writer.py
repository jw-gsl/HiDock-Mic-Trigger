"""Tests for shared.transcript_writer module."""
from __future__ import annotations

from pathlib import Path


from shared.transcript_writer import (
    auto_title,
    build_frontmatter,
    extract_speakers_from_diarized,
    format_diarized_transcript,
    parse_frontmatter,
    write_transcript,
)


class TestAutoTitle:
    def test_empty_text(self):
        assert auto_title("") == "Untitled recording"
        assert auto_title("   ") == "Untitled recording"

    def test_short_text(self):
        assert auto_title("Hello world") == "Hello world"

    def test_truncates_long_text(self):
        text = " ".join(f"word{i}" for i in range(20))
        title = auto_title(text, max_words=5)
        assert title.endswith("...")
        assert title.count(" ") == 4  # 5 words, 4 spaces

    def test_strips_speaker_labels(self):
        text = "**Speaker 1:** Let's discuss the roadmap"
        title = auto_title(text)
        assert "**" not in title
        assert "roadmap" in title

    def test_strips_timestamps(self):
        text = "[00:00-00:45] We need to review the budget"
        title = auto_title(text)
        assert "[00:00" not in title
        assert "budget" in title


class TestBuildFrontmatter:
    def test_basic_frontmatter(self):
        fm = build_frontmatter(
            title="Test meeting",
            date="2026-04-05T14:00:00+00:00",
            duration=120.5,
            speakers=["Alice", "Bob"],
            model="large-v3-turbo",
        )
        assert fm.startswith("---")
        assert fm.endswith("---")
        assert "title: Test meeting" in fm
        assert "duration: 120.5" in fm
        assert "Alice" in fm
        assert "Bob" in fm

    def test_empty_lists(self):
        fm = build_frontmatter(title="Test")
        assert "action_items: []" in fm
        assert "decisions: []" in fm
        assert "key_points: []" in fm
        assert "tags: []" in fm

    def test_action_items(self):
        fm = build_frontmatter(
            title="Test",
            action_items=[{"task": "Review PR", "assignee": "Alice", "status": "open"}],
        )
        assert "Review PR" in fm
        assert "Alice" in fm

    def test_dict_key_points(self):
        """key_points can be list[dict] with text+confidence — should serialize to YAML strings."""
        fm = build_frontmatter(
            title="Test",
            key_points=[
                {"text": "Budget approved", "confidence": "high"},
                {"text": "New hire starting", "confidence": "medium"},
            ],
        )
        assert "Budget approved" in fm
        assert "New hire starting" in fm
        # Should NOT contain Python dict repr
        assert "'text'" not in fm
        assert "'confidence'" not in fm

    def test_mixed_key_points(self):
        """key_points can be a mix of strings and dicts."""
        fm = build_frontmatter(
            title="Test",
            key_points=[
                "Plain string point",
                {"text": "Dict point", "confidence": "low"},
            ],
        )
        assert "Plain string point" in fm
        assert "Dict point" in fm

    def test_special_chars_escaped(self):
        fm = build_frontmatter(title='Meeting: "Q2 Planning" & Review')
        assert "---" in fm
        # Title with special chars should be quoted
        assert '"' in fm


class TestExtractSpeakers:
    def test_empty(self):
        assert extract_speakers_from_diarized(None) == []
        assert extract_speakers_from_diarized({}) == []

    def test_extracts_display_names(self):
        result = {
            "segments": [
                {"speaker": "Speaker 1", "text": "hello"},
                {"speaker": "Speaker 2", "text": "hi"},
                {"speaker": "Speaker 1", "text": "how are you"},
            ],
            "speaker_names": {"Speaker 1": "Alice", "Speaker 2": "Bob"},
        }
        speakers = extract_speakers_from_diarized(result)
        assert speakers == ["Alice", "Bob"]

    def test_preserves_order(self):
        result = {
            "segments": [
                {"speaker": "Speaker 2", "text": "first"},
                {"speaker": "Speaker 1", "text": "second"},
            ],
            "speaker_names": {"Speaker 1": "Alice", "Speaker 2": "Bob"},
        }
        speakers = extract_speakers_from_diarized(result)
        assert speakers == ["Bob", "Alice"]


class TestFormatDiarizedTranscript:
    def test_empty(self):
        assert format_diarized_transcript(None) == ""
        assert format_diarized_transcript({"segments": []}) == ""

    def test_basic_format(self):
        result = {
            "segments": [
                {"speaker": "Speaker 1", "text": "Hello", "start": 0, "end": 1.5},
                {"speaker": "Speaker 2", "text": "Hi there", "start": 1.5, "end": 3.0},
            ],
            "speaker_names": {"Speaker 1": "Alice", "Speaker 2": "Bob"},
        }
        text = format_diarized_transcript(result)
        assert "**Alice:**" in text
        assert "**Bob:**" in text
        assert "Hello" in text

    def test_consecutive_same_speaker(self):
        result = {
            "segments": [
                {"speaker": "Speaker 1", "text": "Part one", "start": 0, "end": 1},
                {"speaker": "Speaker 1", "text": "Part two", "start": 1, "end": 2},
            ],
            "speaker_names": {"Speaker 1": "Alice"},
        }
        text = format_diarized_transcript(result)
        # Should only have one speaker label for consecutive segments
        assert text.count("**Alice:**") == 1


class TestParseFrontmatter:
    def test_no_frontmatter(self):
        meta, body = parse_frontmatter("Just plain text")
        assert meta == {}
        assert body == "Just plain text"

    def test_basic_parse(self):
        text = """---
title: Test meeting
type: meeting
date: 2026-04-05T14:00:00+00:00
duration: 120.5
speakers: [Alice, Bob]
action_items: []
decisions: []
key_points: []
tags: []
---

## Transcript

Hello world"""
        meta, body = parse_frontmatter(text)
        assert meta["title"] == "Test meeting"
        assert meta["type"] == "meeting"
        assert meta["duration"] == 120.5
        assert meta["speakers"] == ["Alice", "Bob"]
        assert "Hello world" in body

    def test_roundtrip(self):
        """Frontmatter we generate should be parseable."""
        fm = build_frontmatter(
            title="Roundtrip test",
            duration=60.0,
            speakers=["Alice"],
            tags=["engineering"],
        )
        full = fm + "\n\nSome transcript text"
        meta, body = parse_frontmatter(full)
        assert meta["title"] == "Roundtrip test"
        assert meta["duration"] == 60.0
        assert "Some transcript text" in body


class TestWriteTranscript:
    def test_writes_file_with_frontmatter(self, tmp_path):
        output = tmp_path / "test.md"
        write_transcript(
            output,
            "Hello world, this is a test transcript.",
            source_path=Path("/recordings/test.mp3"),
            model="large-v3-turbo",
        )
        content = output.read_text()
        assert content.startswith("---")
        assert "## Transcript" in content
        assert "Hello world" in content
        assert "large-v3-turbo" in content

    def test_writes_with_summary(self, tmp_path):
        output = tmp_path / "test.md"
        summary = {
            "title": "Budget Review",
            "action_items": [{"task": "Send report", "assignee": "Alice", "status": "open"}],
            "decisions": [{"text": "Approved Q2 budget", "topic": "finance"}],
            "key_points": ["Budget is on track"],
            "tags": ["finance", "planning"],
            "summary_text": "The team reviewed and approved the Q2 budget.",
        }
        write_transcript(output, "Transcript text here", summary=summary)
        content = output.read_text()
        assert "Budget Review" in content
        assert "Send report" in content
        assert "Approved Q2 budget" in content
        assert "## Summary" in content

    def test_writes_with_diarization(self, tmp_path):
        output = tmp_path / "test.md"
        diarized = {
            "segments": [
                {"speaker": "Speaker 1", "text": "Hello", "start": 0, "end": 1},
                {"speaker": "Speaker 2", "text": "Hi", "start": 1, "end": 2},
            ],
            "speaker_names": {"Speaker 1": "Alice", "Speaker 2": "Bob"},
        }
        write_transcript(output, "", diarized_result=diarized)
        content = output.read_text()
        assert "speakers:" in content
        assert "Alice" in content
        assert "**Alice:**" in content

    def test_creates_parent_dirs(self, tmp_path):
        output = tmp_path / "deep" / "nested" / "test.md"
        write_transcript(output, "Test text")
        assert output.exists()


class TestYamlRoundTripEdgeCases:
    """Round-trip tests for values that used to corrupt the frontmatter:
    embedded double quotes, commas inside quoted list items, newlines."""

    def _roundtrip(self, **kwargs):
        fm = build_frontmatter(**kwargs)
        meta, _ = parse_frontmatter(fm + "\n\nbody")
        return meta

    def test_title_with_double_quotes(self):
        title = 'He said "ship it" on the call'
        meta = self._roundtrip(title=title)
        assert meta["title"] == title

    def test_title_with_newline_collapsed_to_one_line(self):
        fm = build_frontmatter(title="Line one\nLine two")
        # The frontmatter block must stay one line per key — a raw newline
        # would terminate the title entry and corrupt the block.
        meta, _ = parse_frontmatter(fm + "\n\nbody")
        assert meta["title"] == "Line one Line two"
        assert meta.get("type") == "meeting"  # rest of block still parses

    def test_speaker_with_embedded_comma(self):
        meta = self._roundtrip(title="T", speakers=["Whiting, James", "Bob"])
        assert meta["speakers"] == ["Whiting, James", "Bob"]

    def test_inline_list_item_with_colon(self):
        meta = self._roundtrip(title="T", tags=["budget: q3"])
        assert meta["tags"] == ["budget: q3"]

    def test_block_list_item_with_colon_not_parsed_as_dict(self):
        # Items > 40 chars force the block list format ("- item" lines).
        items = [
            f"Point {i}: a fairly long key point to force the block format"
            for i in range(3)
        ]
        meta = self._roundtrip(title="T", key_points=items)
        assert meta["key_points"] == items

    def test_action_item_with_quotes_and_comma_assignee(self):
        meta = self._roundtrip(
            title="T",
            action_items=[{
                "task": 'Review the "final" doc',
                "assignee": "Whiting, James",
                "status": "open",
            }],
        )
        item = meta["action_items"][0]
        assert item["task"] == 'Review the "final" doc'
        assert item["assignee"] == "Whiting, James"

    def test_backslash_roundtrip(self):
        title = 'Path C:\\temp with "quotes"'
        meta = self._roundtrip(title=title)
        assert meta["title"] == title
