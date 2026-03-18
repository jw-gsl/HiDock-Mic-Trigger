"""Test fixtures — redirect HIDOCK_ROOT to tmp_path so tests never touch real data.

Also mock heavy dependencies (whisper, torch, pyannote, speechbrain) so the test
suite runs with just pytest + numpy.
"""
import sys
import types

import pytest


# ── Mock heavy ML modules before any project imports ────────────────────────

def _ensure_mock(modname):
    if modname not in sys.modules:
        sys.modules[modname] = types.ModuleType(modname)
    return sys.modules[modname]


# torch
torch_mod = _ensure_mock("torch")
torch_backends = _ensure_mock("torch.backends")
torch_backends_mps = _ensure_mock("torch.backends.mps")
torch_backends_mps.is_available = lambda: False
torch_backends.mps = torch_backends_mps
torch_mod.backends = torch_backends
torch_mod.device = lambda x: x

# whisper
whisper_mod = _ensure_mock("whisper")
whisper_mod.load_model = lambda *a, **kw: None

# pyannote.audio
_ensure_mock("pyannote")
_ensure_mock("pyannote.audio")
pyannote_audio = sys.modules["pyannote.audio"]
pyannote_audio.Pipeline = type("Pipeline", (), {"from_pretrained": staticmethod(lambda *a, **kw: None)})

# speechbrain
_ensure_mock("speechbrain")
_ensure_mock("speechbrain.inference")
_ensure_mock("speechbrain.inference.speaker")

# torchaudio
_ensure_mock("torchaudio")


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolate_hidock_root(tmp_path, monkeypatch):
    """Redirect config.HIDOCK_ROOT (and all derived paths) to a temp directory."""
    import config

    root = tmp_path / "HiDock"
    root.mkdir()

    monkeypatch.setattr(config, "HIDOCK_ROOT", root)
    monkeypatch.setattr(config, "RECORDINGS_DIR", root / "Recordings")
    monkeypatch.setattr(config, "RAW_TRANSCRIPTS_DIR", root / "Raw Transcripts")
    monkeypatch.setattr(config, "TRANSCRIPTIONS_DIR", root / "Transcriptions")
    monkeypatch.setattr(config, "MODELS_DIR", root / "Speech-to-Text")
    monkeypatch.setattr(config, "VOICE_LIBRARY_DIR", root / "Voice Library")
    monkeypatch.setattr(config, "STATE_PATH", root / "transcription-pipeline" / "state.json")
    monkeypatch.setattr(config, "PROCESSED_LOG", root / "Raw Transcripts" / "processed.log")

    # Also patch state module's STATE_PATH
    import state
    monkeypatch.setattr(state, "STATE_PATH", root / "transcription-pipeline" / "state.json")
