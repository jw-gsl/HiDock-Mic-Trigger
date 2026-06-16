"""Tests for shared.summarize module."""
from __future__ import annotations

from unittest.mock import patch


from shared.summarize import _empty_summary, _normalize_summary, summarize


class TestEmptySummary:
    def test_structure(self):
        s = _empty_summary()
        assert s["title"] == ""
        assert s["action_items"] == []
        assert s["decisions"] == []
        assert s["key_points"] == []
        assert s["tags"] == []
        assert s["summary_text"] == ""


class TestNormalizeSummary:
    def test_valid_full_response(self):
        raw = {
            "title": "Weekly Sync",
            "action_items": [
                {"task": "Review PR", "assignee": "Alice", "due": "2026-04-10", "status": "open"}
            ],
            "decisions": [{"text": "Ship v2.0", "topic": "release"}],
            "key_points": ["Budget approved", "New hire starting Monday"],
            "tags": ["Engineering", "Planning"],
            "summary_text": "The team discussed the roadmap.",
        }
        result = _normalize_summary(raw)
        assert result["title"] == "Weekly Sync"
        assert len(result["action_items"]) == 1
        assert result["action_items"][0]["task"] == "Review PR"
        assert len(result["decisions"]) == 1
        assert len(result["key_points"]) == 2
        assert result["key_points"][0]["text"] == "Budget approved"
        assert result["key_points"][0]["confidence"] == "medium"
        assert result["key_points"][1]["text"] == "New hire starting Monday"
        assert result["tags"] == ["engineering", "planning"]  # lowercased
        # Check confidence on action items and decisions
        assert result["action_items"][0]["confidence"] == "medium"
        assert result["decisions"][0]["confidence"] == "medium"

    def test_empty_response(self):
        result = _normalize_summary({})
        assert result == _empty_summary()

    def test_filters_invalid_action_items(self):
        raw = {
            "action_items": [
                {"task": "Valid task", "assignee": "Bob"},
                {"not_a_task": "invalid"},  # missing "task" key
                "just a string",  # not a dict
            ]
        }
        result = _normalize_summary(raw)
        assert len(result["action_items"]) == 1
        assert result["action_items"][0]["task"] == "Valid task"

    def test_filters_empty_key_points(self):
        raw = {"key_points": ["Valid point", "", "  ", "Another point"]}
        result = _normalize_summary(raw)
        assert len(result["key_points"]) == 2
        assert result["key_points"][0]["text"] == "Valid point"
        assert result["key_points"][1]["text"] == "Another point"

    def test_title_truncation(self):
        raw = {"title": "A" * 200}
        result = _normalize_summary(raw)
        assert len(result["title"]) == 100


class TestSummarize:
    def test_no_engine_available(self):
        with patch("shared.summarize.get_engine", return_value=None):
            result = summarize("Some transcript text")
            assert result == _empty_summary()

    def test_engine_name_defaults_from_config(self):
        # With no explicit engine_name, the provider is read from
        # [summarization].engine instead of being hard-coded to "auto".
        with patch("shared.summarize.get_config") as cfg, \
             patch("shared.summarize.get_engine", return_value=None) as ge:
            cfg.return_value.get.return_value = "claude"
            summarize("text")
            cfg.return_value.get.assert_called_once_with("summarization", "engine", "auto")
            ge.assert_called_once_with("claude")

    def test_explicit_engine_name_bypasses_config(self):
        with patch("shared.summarize.get_config") as cfg, \
             patch("shared.summarize.get_engine", return_value=None) as ge:
            summarize("text", engine_name="ollama")
            cfg.return_value.get.assert_not_called()
            ge.assert_called_once_with("ollama")

    def test_returns_empty_on_failure(self):
        from shared.llm_cli import LLMEngine
        fake_engine = LLMEngine(name="test", command=["test"], description="test")
        with patch("shared.summarize.get_engine", return_value=fake_engine), \
             patch("shared.summarize.query_json", return_value=None):
            result = summarize("Some transcript text")
            assert result == _empty_summary()
