"""Tests for core/config.py — paths and whisper_device."""
from pathlib import Path


class TestConfigPaths:
    def test_hidock_root_is_path(self):
        import core.config as cfg
        assert isinstance(cfg.HIDOCK_ROOT, Path)

    def test_state_path_is_json(self):
        import core.config as cfg
        assert cfg.STATE_PATH.suffix == ".json"

    def test_watch_extensions(self):
        import core.config as cfg
        assert ".mp3" in cfg.WATCH_EXTENSIONS
        assert len(cfg.WATCH_EXTENSIONS) >= 3


class TestWhisperModel:
    def test_model_path_exists(self):
        import core.config as cfg
        path = cfg.whisper_model_path()
        assert path.suffix == ".bin"
        assert "ggml" in path.name

    def test_model_not_ready_when_missing(self, tmp_path, monkeypatch):
        import core.config as cfg
        monkeypatch.setattr(cfg, "MODELS_DIR", tmp_path)
        assert not cfg.whisper_model_ready()

    def test_model_url_is_huggingface(self):
        import core.config as cfg
        assert "huggingface.co" in cfg.WHISPER_MODEL_URL
