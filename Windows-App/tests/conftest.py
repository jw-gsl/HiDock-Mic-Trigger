"""Test fixtures — mock Windows-only deps and redirect HIDOCK_ROOT to tmp_path."""
import sys
import types

import pytest

# ── Mock Windows-only and heavy dependencies ────────────────────────────────

def _ensure_mock(modname):
    if modname not in sys.modules:
        sys.modules[modname] = types.ModuleType(modname)
    return sys.modules[modname]

# PyQt6 (not needed for core tests)
for mod in ("PyQt6", "PyQt6.QtCore", "PyQt6.QtGui", "PyQt6.QtWidgets"):
    _ensure_mock(mod)

# pycaw (Windows WASAPI)
for mod in ("pycaw", "pycaw.pycaw", "comtypes"):
    _ensure_mock(mod)

# torch
torch_mod = _ensure_mock("torch")
torch_backends = _ensure_mock("torch.backends")
torch_backends_mps = _ensure_mock("torch.backends.mps")
torch_backends_mps.is_available = lambda: False
torch_backends.mps = torch_backends_mps
torch_mod.backends = torch_backends
torch_mod.device = lambda x: x

# cuda mock
class FakeCuda:
    @staticmethod
    def is_available():
        return False
torch_mod.cuda = FakeCuda

# whisper
whisper_mod = _ensure_mock("whisper")
whisper_mod.load_model = lambda *a, **kw: None

# torchaudio
_ensure_mock("torchaudio")


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_hidock_root(tmp_path, monkeypatch):
    """Redirect all paths in core.config to a temp directory."""
    # We need to patch the module-level attributes after import
    import core.config as cfg

    root = tmp_path / "HiDock"
    root.mkdir()

    monkeypatch.setattr(cfg, "HIDOCK_ROOT", root)
    monkeypatch.setattr(cfg, "RECORDINGS_DIR", root / "Recordings")
    monkeypatch.setattr(cfg, "RAW_TRANSCRIPTS_DIR", root / "Raw Transcripts")
    monkeypatch.setattr(cfg, "MODELS_DIR", root / "Speech-to-Text")
    monkeypatch.setattr(cfg, "STATE_PATH", root / "transcription-pipeline" / "state.json")

    # Also patch state module's reference
    import core.state as state_mod
    monkeypatch.setattr(state_mod, "STATE_PATH", root / "transcription-pipeline" / "state.json")
