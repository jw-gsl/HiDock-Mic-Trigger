"""Tests for shared.llm_cli module."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from shared.llm_cli import (
    LLMEngine,
    _extract_json,
    detect_engines,
    get_engine,
    query,
)


class TestDetectEngines:
    def test_no_engines(self):
        with patch("shutil.which", return_value=None):
            engines = detect_engines()
            assert engines == []

    def test_claude_available(self):
        def which_mock(name):
            return "/usr/bin/claude" if name == "claude" else None

        with patch("shutil.which", side_effect=which_mock):
            engines = detect_engines()
            assert len(engines) == 1
            assert engines[0].name == "claude"

    def test_multiple_engines(self):
        def which_mock(name):
            return f"/usr/bin/{name}" if name in ("claude", "ollama") else None

        with patch("shutil.which", side_effect=which_mock):
            engines = detect_engines()
            assert len(engines) == 2
            # claude should be first (higher priority)
            assert engines[0].name == "claude"
            assert engines[1].name == "ollama"

    def test_priority_order(self):
        with patch("shutil.which", return_value="/usr/bin/mock"):
            engines = detect_engines()
            names = [e.name for e in engines]
            assert names == ["claude", "codex", "gemini", "ollama"]


class TestGetEngine:
    def test_auto_returns_first(self):
        def which_mock(name):
            return f"/usr/bin/{name}" if name == "gemini" else None

        with patch("shutil.which", side_effect=which_mock):
            engine = get_engine("auto")
            assert engine is not None
            assert engine.name == "gemini"

    def test_auto_returns_none_when_empty(self):
        with patch("shutil.which", return_value=None):
            assert get_engine("auto") is None

    def test_none_returns_none(self):
        assert get_engine("none") is None

    def test_specific_engine(self):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            engine = get_engine("claude")
            assert engine is not None
            assert engine.name == "claude"

    def test_specific_engine_not_installed(self):
        with patch("shutil.which", return_value=None):
            assert get_engine("claude") is None


class TestQuery:
    def test_returns_none_when_no_engine(self):
        with patch("shutil.which", return_value=None):
            result = query("test prompt")
            assert result is None

    def test_returns_none_on_timeout(self):
        import subprocess

        engine = LLMEngine(name="test", command=["sleep", "10"], description="test")
        result = query("test", engine=engine, timeout=1)
        assert result is None

    def test_returns_none_on_missing_binary(self):
        engine = LLMEngine(
            name="test",
            command=["nonexistent_binary_xyz"],
            description="test",
        )
        result = query("test", engine=engine)
        assert result is None


class TestExtractJson:
    def test_direct_json(self):
        result = _extract_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_json_in_code_fence(self):
        text = 'Here is the result:\n```json\n{"key": "value"}\n```'
        result = _extract_json(text)
        assert result == {"key": "value"}

    def test_json_in_plain_fence(self):
        text = 'Result:\n```\n{"key": "value"}\n```'
        result = _extract_json(text)
        assert result == {"key": "value"}

    def test_embedded_json(self):
        text = 'Some text before {"key": "value"} and after'
        result = _extract_json(text)
        assert result == {"key": "value"}

    def test_invalid_json(self):
        assert _extract_json("not json at all") is None

    def test_complex_json(self):
        text = '```json\n{"title": "Test", "items": [1, 2, 3]}\n```'
        result = _extract_json(text)
        assert result["title"] == "Test"
        assert result["items"] == [1, 2, 3]
