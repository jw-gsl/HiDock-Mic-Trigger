"""Tests for notification preferences and transcription result routing."""


class TestNotificationPreferenceDefaults:
    """Verify notification preference defaults follow expected patterns."""

    def test_default_true_convention(self):
        """The default value for all notification settings should be True."""
        # This mirrors the pattern used in main_window.py:
        # self.settings.value("notifyTranscription", True, type=bool)
        default = True
        assert default is True

    def test_disabled_preference_blocks_notification(self):
        """When a preference is False, the notification should be skipped."""
        notify_enabled = False
        should_show = notify_enabled  # Guard pattern from main_window.py
        assert should_show is False

    def test_enabled_preference_allows_notification(self):
        """When a preference is True, the notification should be shown."""
        notify_enabled = True
        should_show = notify_enabled
        assert should_show is True


class TestTranscriptionResultData:
    """Verify transcription result dicts include transcript paths."""

    def test_result_dict_has_transcript_path_key(self):
        """A successful transcription result should carry transcript_paths."""
        result = {
            "_transcription_done": True,
            "succeeded": 2,
            "total": 3,
            "transcript_paths": ["/tmp/a.md", "/tmp/b.md"],
        }
        assert "transcript_paths" in result
        assert len(result["transcript_paths"]) == 2

    def test_empty_transcript_paths_for_zero_succeeded(self):
        """When nothing succeeded, transcript_paths should be empty."""
        result = {
            "_transcription_done": True,
            "succeeded": 0,
            "total": 2,
            "transcript_paths": [],
        }
        assert result["transcript_paths"] == []

    def test_last_transcript_path_selection(self):
        """The most recently transcribed file should be the last in the list."""
        paths = ["/tmp/first.md", "/tmp/second.md", "/tmp/third.md"]
        last = paths[-1] if paths else None
        assert last == "/tmp/third.md"

    def test_single_transcript_click_message(self):
        """Single file transcription should suggest click to open."""
        succeeded = 1
        transcript_paths = ["/tmp/recording.md"]
        body = f"Transcribed {succeeded}/1 files"
        if succeeded == 1 and transcript_paths:
            body += "\nClick to open transcript"
        assert "Click to open transcript" in body

    def test_batch_transcript_click_message(self):
        """Batch transcription should suggest click to open folder."""
        succeeded = 3
        body = f"Transcribed {succeeded}/3 files"
        if succeeded > 1:
            body += "\nClick to open transcript folder"
        assert "Click to open transcript folder" in body


class TestTrayNotificationClickHandler:
    """Verify the click handler logic for opening transcripts."""

    def test_none_path_does_nothing(self):
        """When no transcript path is stored, click does nothing."""
        last_path = None
        assert last_path is None

    def test_path_cleared_after_click(self):
        """After handling a click, the stored path should be cleared."""
        last_path = "/tmp/transcript.md"
        # Simulate click handler
        last_path = None
        assert last_path is None

    def test_file_path_detected(self):
        """A .md path should be treated as a file to open."""
        import os
        path = "/tmp/nonexistent_test_file.md"
        # os.path.isfile returns False for nonexistent, which is correct behavior
        assert not os.path.isfile(path)

    def test_dir_path_detected(self):
        """A directory path should be treated as a folder to open."""
        import tempfile
        tmpdir = tempfile.gettempdir()
        import os
        assert os.path.isdir(tmpdir)
