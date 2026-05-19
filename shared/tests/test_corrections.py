"""Tests for shared.corrections — corrections dictionary system."""
from __future__ import annotations

import json
from unittest.mock import patch


from shared.corrections import (
    add_correction,
    apply_corrections,
    load_corrections,
    remove_correction,
    save_corrections,
)


# ---------------------------------------------------------------------------
# load_corrections
# ---------------------------------------------------------------------------
class TestLoadCorrections:
    def test_missing_file_returns_empty(self, tmp_path):
        fake_path = tmp_path / "corrections.json"
        with patch("shared.corrections.CORRECTIONS_PATH", fake_path):
            result = load_corrections()
        assert result == {}

    def test_empty_json_returns_empty(self, tmp_path):
        fake_path = tmp_path / "corrections.json"
        fake_path.write_text("{}", encoding="utf-8")
        with patch("shared.corrections.CORRECTIONS_PATH", fake_path):
            result = load_corrections()
        assert result == {}

    def test_valid_file(self, tmp_path):
        fake_path = tmp_path / "corrections.json"
        fake_path.write_text(
            json.dumps({"corrections": {"volaris": "VOLARIS"}}),
            encoding="utf-8",
        )
        with patch("shared.corrections.CORRECTIONS_PATH", fake_path):
            result = load_corrections()
        assert result == {"volaris": "VOLARIS"}

    def test_corrupt_json_returns_empty(self, tmp_path):
        fake_path = tmp_path / "corrections.json"
        fake_path.write_text("{bad json!!!", encoding="utf-8")
        with patch("shared.corrections.CORRECTIONS_PATH", fake_path):
            result = load_corrections()
        assert result == {}

    def test_missing_corrections_key(self, tmp_path):
        fake_path = tmp_path / "corrections.json"
        fake_path.write_text(json.dumps({"other_key": "value"}), encoding="utf-8")
        with patch("shared.corrections.CORRECTIONS_PATH", fake_path):
            result = load_corrections()
        assert result == {}


# ---------------------------------------------------------------------------
# save_corrections
# ---------------------------------------------------------------------------
class TestSaveCorrections:
    def test_save_creates_file(self, tmp_path):
        fake_path = tmp_path / "subdir" / "corrections.json"
        with patch("shared.corrections.CORRECTIONS_PATH", fake_path):
            save_corrections({"hyde oates": "HiDock"})
        assert fake_path.exists()
        data = json.loads(fake_path.read_text(encoding="utf-8"))
        assert data["corrections"]["hyde oates"] == "HiDock"

    def test_save_overwrites(self, tmp_path):
        fake_path = tmp_path / "corrections.json"
        fake_path.write_text(json.dumps({"corrections": {"old": "OLD"}}), encoding="utf-8")
        with patch("shared.corrections.CORRECTIONS_PATH", fake_path):
            save_corrections({"new": "NEW"})
        data = json.loads(fake_path.read_text(encoding="utf-8"))
        assert "old" not in data["corrections"]
        assert data["corrections"]["new"] == "NEW"


# ---------------------------------------------------------------------------
# add_correction / remove_correction
# ---------------------------------------------------------------------------
class TestAddRemoveCorrection:
    def test_add_stores_lowercase_key(self, tmp_path):
        fake_path = tmp_path / "corrections.json"
        with patch("shared.corrections.CORRECTIONS_PATH", fake_path):
            result = add_correction("Volaris", "VOLARIS")
        assert "volaris" in result
        assert result["volaris"] == "VOLARIS"

    def test_add_multiple(self, tmp_path):
        fake_path = tmp_path / "corrections.json"
        with patch("shared.corrections.CORRECTIONS_PATH", fake_path):
            add_correction("foo", "FOO")
            result = add_correction("bar", "BAR")
        assert result == {"foo": "FOO", "bar": "BAR"}

    def test_remove_existing(self, tmp_path):
        fake_path = tmp_path / "corrections.json"
        fake_path.write_text(
            json.dumps({"corrections": {"volaris": "VOLARIS", "hyde oates": "HiDock"}}),
            encoding="utf-8",
        )
        with patch("shared.corrections.CORRECTIONS_PATH", fake_path):
            result = remove_correction("volaris")
        assert "volaris" not in result
        assert "hyde oates" in result

    def test_remove_nonexistent_is_noop(self, tmp_path):
        fake_path = tmp_path / "corrections.json"
        fake_path.write_text(
            json.dumps({"corrections": {"volaris": "VOLARIS"}}),
            encoding="utf-8",
        )
        with patch("shared.corrections.CORRECTIONS_PATH", fake_path):
            result = remove_correction("nonexistent")
        assert result == {"volaris": "VOLARIS"}


# ---------------------------------------------------------------------------
# apply_corrections
# ---------------------------------------------------------------------------
class TestApplyCorrections:
    def test_basic_replacement(self):
        text = "We use volaris for AI."
        result = apply_corrections(text, {"volaris": "VOLARIS"})
        assert result == "We use VOLARIS for AI."

    def test_case_insensitive(self):
        text = "Volaris and VOLARIS and volaris"
        result = apply_corrections(text, {"volaris": "VOLARIS"})
        assert result == "VOLARIS and VOLARIS and VOLARIS"

    def test_empty_corrections(self):
        text = "Hello world"
        result = apply_corrections(text, {})
        assert result == "Hello world"

    def test_no_corrections_provided_loads_default(self, tmp_path):
        fake_path = tmp_path / "corrections.json"
        fake_path.write_text(
            json.dumps({"corrections": {"hello": "HELLO"}}),
            encoding="utf-8",
        )
        with patch("shared.corrections.CORRECTIONS_PATH", fake_path):
            result = apply_corrections("hello world")
        assert result == "HELLO world"

    def test_special_characters_in_correction(self):
        """Regex special chars in the 'wrong' string should be escaped properly."""
        text = "Use c++ for performance."
        result = apply_corrections(text, {"c++": "C++"})
        assert result == "Use C++ for performance."

    def test_correction_with_period(self):
        text = "Send it to mr. smith please."
        result = apply_corrections(text, {"mr. smith": "Mr. Smith"})
        assert result == "Send it to Mr. Smith please."

    def test_multiple_corrections_applied(self):
        text = "hyde oates uses volaris"
        corrections = {"hyde oates": "HiDock", "volaris": "VOLARIS"}
        result = apply_corrections(text, corrections)
        assert result == "HiDock uses VOLARIS"

    def test_mid_word_replacement(self):
        """The current implementation replaces substrings (not word-boundary only)."""
        text = "volaris-based solution"
        result = apply_corrections(text, {"volaris": "VOLARIS"})
        assert result == "VOLARIS-based solution"

    def test_empty_text(self):
        result = apply_corrections("", {"foo": "bar"})
        assert result == ""
