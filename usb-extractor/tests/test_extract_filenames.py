"""Unit tests for extract_filenames.py."""
from __future__ import annotations

from extract_filenames import extract_names


class TestExtractNames:
    def test_single_filename(self):
        text = 'some text 2026Feb25-111702-Rec25.hda more text'
        assert extract_names(text) == ["2026Feb25-111702-Rec25.hda"]

    def test_multiple_filenames(self):
        text = "2026Feb25-111702-Rec25.hda and 2026Mar10-130028-Rec43.hda"
        result = extract_names(text)
        assert len(result) == 2
        assert result[0] == "2026Feb25-111702-Rec25.hda"
        assert result[1] == "2026Mar10-130028-Rec43.hda"

    def test_deduplication(self):
        text = "2026Feb25-111702-Rec25.hda 2026Feb25-111702-Rec25.hda"
        assert len(extract_names(text)) == 1

    def test_no_matches(self):
        assert extract_names("no recordings here") == []

    def test_embedded_in_json(self):
        text = '{"file": "2026Feb25-111702-Rec25.hda", "other": "data"}'
        assert extract_names(text) == ["2026Feb25-111702-Rec25.hda"]

    def test_sorted_output(self):
        text = "2026Mar10-130028-Rec43.hda 2026Feb25-111702-Rec25.hda"
        result = extract_names(text)
        assert result == sorted(result)
