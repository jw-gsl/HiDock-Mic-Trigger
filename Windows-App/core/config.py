"""Windows-adapted configuration — all paths and tunables."""
from __future__ import annotations

import os
from pathlib import Path

# ── Directories ──────────────────────────────────────────────────────────────
HIDOCK_ROOT = Path(os.environ.get("USERPROFILE", Path.home())) / "HiDock"
RECORDINGS_DIR = HIDOCK_ROOT / "Recordings"
RAW_TRANSCRIPTS_DIR = HIDOCK_ROOT / "Raw Transcripts"

# Where whisper.cpp GGML models live
MODELS_DIR = HIDOCK_ROOT / "Speech-to-Text"

# ── Whisper settings ─────────────────────────────────────────────────────────
WHISPER_MODEL = "large-v3-turbo"
WHISPER_MODEL_FILENAME = "ggml-large-v3-turbo-q5_0.bin"
WHISPER_MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin"
WHISPER_LANGUAGE = "en"

def whisper_model_path() -> Path:
    """Return full path to the whisper.cpp model file."""
    return MODELS_DIR / WHISPER_MODEL_FILENAME

def whisper_model_ready() -> bool:
    """Check if the model file exists and is non-empty."""
    p = whisper_model_path()
    return p.exists() and p.stat().st_size > 1_000_000

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
