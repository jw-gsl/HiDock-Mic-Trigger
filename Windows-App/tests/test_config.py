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


class TestWhisperDevice:
    def test_no_torch_returns_cpu(self, monkeypatch):
        """When torch import fails, whisper_device() should return 'cpu'."""
        import core.config as cfg
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "torch":
                raise ImportError("no torch")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        assert cfg.whisper_device() == "cpu"

    def test_cuda_available_returns_cuda(self, monkeypatch):
        """When torch.cuda.is_available() is True, should return 'cuda'."""
        import core.config as cfg
        import sys

        # Ensure torch mock has cuda.is_available = True
        torch_mod = sys.modules["torch"]
        orig = torch_mod.cuda.is_available

        class MockCuda:
            @staticmethod
            def is_available():
                return True

        monkeypatch.setattr(torch_mod, "cuda", MockCuda)
        assert cfg.whisper_device() == "cuda"
