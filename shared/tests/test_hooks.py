"""Tests for shared.hooks module."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shared.hooks import run_hooks_pipeline, run_post_transcription_hook
from shared.transcript_writer import write_transcript


@pytest.fixture
def transcript_file(tmp_path):
    """Create a sample transcript file."""
    path = tmp_path / "test.md"
    write_transcript(
        path,
        "Test transcript content.",
        summary={
            "title": "Test Meeting",
            "action_items": [{"task": "Do thing", "assignee": "Alice", "status": "open"}],
            "decisions": [],
            "key_points": [],
            "tags": [],
            "summary_text": "A test meeting.",
        },
    )
    return path


class TestRunPostTranscriptionHook:
    def test_empty_command(self, transcript_file):
        assert run_post_transcription_hook("", transcript_file) is False
        assert run_post_transcription_hook("  ", transcript_file) is False

    def test_successful_command(self, transcript_file):
        result = run_post_transcription_hook(
            "echo $TRANSCRIPT_PATH",
            transcript_file,
        )
        assert result is True

    def test_failing_command(self, transcript_file):
        result = run_post_transcription_hook(
            "exit 1",
            transcript_file,
        )
        assert result is False

    def test_timeout(self, transcript_file):
        result = run_post_transcription_hook(
            "sleep 10",
            transcript_file,
            timeout=1,
        )
        assert result is False

    def test_env_variables_set(self, transcript_file, tmp_path):
        """Verify environment variables are passed to the hook."""
        output_file = tmp_path / "env_output.txt"
        cmd = f'echo "$TRANSCRIPT_TITLE|$HAS_SUMMARY|$ACTION_ITEMS_COUNT" > {output_file}'
        summary = {
            "title": "Budget Review",
            "summary_text": "We reviewed the budget.",
            "action_items": [{"task": "a"}, {"task": "b"}],
        }
        result = run_post_transcription_hook(
            cmd,
            transcript_file,
            summary=summary,
        )
        assert result is True
        output = output_file.read_text().strip()
        assert "Budget Review" in output
        assert "true" in output
        assert "2" in output

    def test_source_path_in_env(self, transcript_file, tmp_path):
        output_file = tmp_path / "source_output.txt"
        source = Path("/recordings/test.mp3")
        cmd = f'echo "$SOURCE_PATH" > {output_file}'
        run_post_transcription_hook(cmd, transcript_file, source_path=source)
        output = output_file.read_text().strip()
        assert "test.mp3" in output


class TestRunHooksPipeline:
    def test_no_config(self, transcript_file):
        """Pipeline should handle missing config gracefully."""
        with patch("shared.config_store.get_config", side_effect=Exception("no config")):
            results = run_hooks_pipeline(transcript_file)
            assert results["hook_command"] is None
            assert results["obsidian_sync"] is None

    def test_with_hook_command(self, transcript_file, tmp_path):
        config = MagicMock()
        config.get.side_effect = lambda section, key, default="": {
            ("hooks", "post_transcription", ""): "echo done",
            ("obsidian", "enabled", False): False,
            ("obsidian", "vault_path", ""): "",
        }.get((section, key, default), default)

        results = run_hooks_pipeline(transcript_file, config=config)
        assert results["hook_command"] is True

    def test_obsidian_disabled(self, transcript_file):
        config = MagicMock()
        config.get.side_effect = lambda section, key, default="": {
            ("hooks", "post_transcription", ""): "",
            ("obsidian", "enabled", False): False,
            ("obsidian", "vault_path", ""): "",
        }.get((section, key, default), default)

        results = run_hooks_pipeline(transcript_file, config=config)
        assert results["obsidian_sync"] is None  # not attempted
