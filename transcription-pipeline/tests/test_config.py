"""Tests for config.py — verify paths and tunables."""
from pathlib import Path

import config


class TestConfigPaths:
    def test_hidock_root_is_path(self):
        assert isinstance(config.HIDOCK_ROOT, Path)

    def test_all_dirs_under_hidock_root(self):
        for attr in ("RECORDINGS_DIR", "RAW_TRANSCRIPTS_DIR", "MODELS_DIR",
                      "VOICE_LIBRARY_DIR", "TRANSCRIPTIONS_DIR"):
            p = getattr(config, attr)
            assert isinstance(p, Path)
            # All directory paths should be rooted under HIDOCK_ROOT
            assert str(p).startswith(str(config.HIDOCK_ROOT))

    def test_state_path_is_path(self):
        assert isinstance(config.STATE_PATH, Path)
        assert config.STATE_PATH.name == "state.json"


class TestConfigValues:
    def test_watch_extensions_populated(self):
        assert len(config.WATCH_EXTENSIONS) >= 3
        assert ".mp3" in config.WATCH_EXTENSIONS
        assert ".wav" in config.WATCH_EXTENSIONS
