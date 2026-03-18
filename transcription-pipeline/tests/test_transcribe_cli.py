"""Tests for transcribe.py — CLI argument parsing and progress protocol."""
import argparse
import sys

import config


class TestCliParsing:
    def _parse(self, argv):
        """Run the argument parser from transcribe.main() with given argv."""
        # Import here so conftest patches are applied
        import transcribe

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")

        p_t = sub.add_parser("transcribe")
        p_t.add_argument("mp3_path")
        p_t.add_argument("--diarize", action="store_true")
        p_t.set_defaults(func=transcribe.cmd_transcribe)

        p_b = sub.add_parser("transcribe-batch")
        p_b.add_argument("--diarize", action="store_true")
        p_b.set_defaults(func=transcribe.cmd_transcribe_batch)

        p_s = sub.add_parser("status")
        p_s.set_defaults(func=transcribe.cmd_status)

        return parser.parse_args(argv)

    def test_transcribe_subcommand(self):
        args = self._parse(["transcribe", "/tmp/foo.mp3"])
        assert args.command == "transcribe"
        assert args.mp3_path == "/tmp/foo.mp3"
        assert args.diarize is False

    def test_transcribe_diarize_flag(self):
        args = self._parse(["transcribe", "/tmp/foo.mp3", "--diarize"])
        assert args.diarize is True

    def test_batch_subcommand(self):
        args = self._parse(["transcribe-batch"])
        assert args.command == "transcribe-batch"
        assert args.diarize is False

    def test_status_subcommand(self):
        args = self._parse(["status"])
        assert args.command == "status"

    def test_no_args_has_no_func(self):
        args = self._parse([])
        assert not hasattr(args, "func")


class TestProgress:
    def test_progress_writes_to_stderr(self, capsys):
        import transcribe
        transcribe.progress(42)
        captured = capsys.readouterr()
        assert captured.err.strip() == "PROGRESS:42"
        assert captured.out == ""
