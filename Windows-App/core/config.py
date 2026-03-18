"""Windows-adapted configuration — all paths and tunables."""
from __future__ import annotations

import os
from pathlib import Path

# ── Directories ──────────────────────────────────────────────────────────────
HIDOCK_ROOT = Path(os.environ.get("USERPROFILE", Path.home())) / "HiDock"
RECORDINGS_DIR = HIDOCK_ROOT / "Recordings"
RAW_TRANSCRIPTS_DIR = HIDOCK_ROOT / "Raw Transcripts"

# Where Whisper .pt models live
MODELS_DIR = HIDOCK_ROOT / "Speech-to-Text"

# ── Whisper settings ─────────────────────────────────────────────────────────
WHISPER_MODEL = "large-v3-turbo"
WHISPER_LANGUAGE = "en"

def whisper_device() -> str:
    """Return best available device: cuda > cpu."""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except ImportError:
        pass
    return "cpu"

# ── Watcher ──────────────────────────────────────────────────────────────────
WATCH_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}

# ── State ────────────────────────────────────────────────────────────────────
STATE_PATH = HIDOCK_ROOT / "transcription-pipeline" / "state.json"

# ── Extractor ────────────────────────────────────────────────────────────────
# Path to the Windows-Script extractor (sibling directory)
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
EXTRACTOR_DIR = REPO_ROOT / "Windows-Script"
EXTRACTOR_SCRIPT = EXTRACTOR_DIR / "extractor.py"
EXTRACTOR_PYTHON = EXTRACTOR_DIR / ".venv" / "Scripts" / "python.exe"

# ── Logs ─────────────────────────────────────────────────────────────────────
APPDATA = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
LOG_DIR = APPDATA / "HiDock" / "logs"
