"""Tests for shared.llm_cli module."""
from __future__ import annotations

from unittest.mock import patch


from shared.llm_cli import (
    LLMEngine,
    _clean_output,
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
            assert names == ["claude", "codex", "gemini", "ollama", "kimi", "grok"]


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


class TestNewCliRegistration:
    def test_kimi_and_grok_detected_when_installed(self):
        with patch("shutil.which", side_effect=lambda name: f"/usr/bin/{name}"):
            names = [e.name for e in detect_engines()]
        assert "kimi" in names and "grok" in names
        # Existing users keep their auto-resolution: new CLIs rank last.
        assert names.index("kimi") > names.index("ollama")
        assert names.index("grok") > names.index("ollama")

    def test_argv_prompt_engines_run_prompt_as_argument(self):
        with patch("shutil.which", return_value="/usr/bin/kimi"), \
             patch("subprocess.run") as run_mock:
            run_mock.return_value.returncode = 0
            run_mock.return_value.stdout = "• OK\n\nTo resume this session: kimi -r abc\n"
            run_mock.return_value.stderr = ""
            result = query("summarise this", engine=get_engine("kimi"))
        args, kwargs = run_mock.call_args
        assert args[0] == ["kimi", "-p", "summarise this"]
        assert kwargs["input"] is None
        assert result == "OK"

    def test_grok_runs_single_turn_argv(self):
        with patch("shutil.which", return_value="/usr/bin/grok"), \
             patch("subprocess.run") as run_mock:
            run_mock.return_value.returncode = 0
            run_mock.return_value.stdout = "OK"
            run_mock.return_value.stderr = ""
            result = query("hi", engine=get_engine("grok"))
        args, _kwargs = run_mock.call_args
        assert args[0] == ["grok", "--single", "hi"]
        assert result == "OK"

    def test_list_engines_labels_installed_clis(self):
        from shared.llm_cli import list_engines

        with patch("shutil.which", side_effect=lambda name: f"/usr/bin/{name}"):
            rows = list_engines()
        by_id = {row["id"]: row for row in rows}
        assert by_id["kimi"]["label"] == "Kimi"
        assert by_id["grok"]["label"] == "Grok"
        assert all(row["description"] for row in rows)


class TestKimiOutputCleaning:
    """Kimi writes its answer to stdout as one `•` block, indented continuations.

    The fixtures are the CLI's real *stdout*, captured 2026-07-27 by redirecting
    the two streams to separate files. Its reasoning and resume trailer go to
    stderr, which `query()` never reads for the answer — capturing with `2>&1`
    conflates them and gives a misleading picture. Keeping the real bytes here
    means a format change breaks these tests loudly instead of silently
    mangling summaries.
    """

    def test_flat_list_does_not_become_a_nested_list(self):
        # Verbatim stdout for "reply with exactly a markdown bullet list of
        # three fruits". The old cleaner stripped the bullet but left the
        # two-space indent, so Banana and Orange rendered as children of Apple.
        raw = "• - Apple\n  - Banana\n  - Orange"
        assert _clean_output("kimi", raw) == "- Apple\n- Banana\n- Orange"

    def test_single_sentence_answer_loses_only_the_marker(self):
        raw = "• Alice confirmed the budget is approved, and Bob asked about the timelines."
        assert _clean_output("kimi", raw) == (
            "Alice confirmed the budget is approved, and Bob asked about the timelines."
        )

    def test_markdown_glued_to_the_bullet_leaves_no_stray_asterisks(self):
        raw = "•**Answer:** 42"
        cleaned = _clean_output("kimi", raw)
        assert cleaned == "**Answer:** 42"
        assert "•" not in cleaned

    def test_output_without_bullets_is_returned_unchanged(self):
        raw = "A plain answer with no markers."
        assert _clean_output("kimi", raw) == "A plain answer with no markers."

    def test_trailer_on_stdout_is_filtered_defensively(self):
        # Today the trailer only appears on stderr. Filtered anyway so a future
        # Kimi that moves it to stdout cannot leak a session id into a summary.
        raw = "• The answer.\n\nTo resume this session: kimi -r session_abc123"
        assert _clean_output("kimi", raw) == "The answer."

    def test_last_block_wins_if_several_are_emitted(self):
        # Defensive: stdout carries one block today, but if reasoning ever moved
        # here the answer would be last and must not be prefixed by thinking.
        raw = "• thinking out loud\n\n• The answer."
        assert _clean_output("kimi", raw) == "The answer."

    def test_other_engines_are_untouched(self):
        raw = "• not kimi chrome\nTo resume this session: x"
        assert _clean_output("claude", raw) == raw


class TestArgvPromptLimit:
    def test_oversized_argv_prompt_is_refused_before_subprocess(self):
        from shared.llm_cli import _ARGV_PROMPT_MAX_BYTES

        oversized = "x" * (_ARGV_PROMPT_MAX_BYTES + 1)
        with patch("shutil.which", return_value="/usr/bin/kimi"), \
             patch("subprocess.run") as run_mock:
            result = query(oversized, engine=get_engine("kimi"))
        assert result is None
        # The point of the guard: never reach subprocess with a doomed argv.
        run_mock.assert_not_called()

    def test_prompt_at_the_limit_is_still_sent(self):
        from shared.llm_cli import _ARGV_PROMPT_MAX_BYTES

        at_limit = "x" * _ARGV_PROMPT_MAX_BYTES
        with patch("shutil.which", return_value="/usr/bin/kimi"), \
             patch("subprocess.run") as run_mock:
            run_mock.return_value.returncode = 0
            run_mock.return_value.stdout = "fine"
            run_mock.return_value.stderr = ""
            result = query(at_limit, engine=get_engine("kimi"))
        run_mock.assert_called_once()
        assert result == "fine"

    def test_stdin_engines_have_no_length_limit(self):
        from shared.llm_cli import _ARGV_PROMPT_MAX_BYTES

        huge = "x" * (_ARGV_PROMPT_MAX_BYTES * 2)
        with patch("shutil.which", return_value="/usr/bin/claude"), \
             patch("subprocess.run") as run_mock:
            run_mock.return_value.returncode = 0
            run_mock.return_value.stdout = "ok"
            run_mock.return_value.stderr = ""
            result = query(huge, engine=get_engine("claude"))
        run_mock.assert_called_once()
        _args, kwargs = run_mock.call_args
        assert kwargs["input"] == huge   # piped, not in argv
        assert result == "ok"
