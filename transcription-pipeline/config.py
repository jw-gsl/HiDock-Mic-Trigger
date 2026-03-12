"""Pipeline configuration — all paths and tunables in one place."""
from __future__ import annotations

from pathlib import Path

# ── Directories ──────────────────────────────────────────────────────────────
HIDOCK_ROOT = Path.home() / "HiDock"
RECORDINGS_DIR = HIDOCK_ROOT / "Recordings"
RAW_TRANSCRIPTS_DIR = HIDOCK_ROOT / "Raw Transcripts"
TRANSCRIPTIONS_DIR = HIDOCK_ROOT / "Transcriptions"

# Where Whisper .pt models live
MODELS_DIR = HIDOCK_ROOT / "Speech-to-Text"

# ── Whisper settings ─────────────────────────────────────────────────────────
WHISPER_MODEL = "large-v3-turbo"  # matches your downloaded .pt file
WHISPER_LANGUAGE = "en"
WHISPER_DEVICE = "mps"  # Apple Silicon GPU; falls back to "cpu" if unavailable

# ── Watcher ──────────────────────────────────────────────────────────────────
WATCH_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}
# Seconds to wait after last file modification before transcribing
# (avoids processing a file that's still being written)
SETTLE_SECONDS = 5.0

# ── Processing log ───────────────────────────────────────────────────────────
PROCESSED_LOG = RAW_TRANSCRIPTS_DIR / "processed.log"

# ── Transcription state ─────────────────────────────────────────────────────
STATE_PATH = HIDOCK_ROOT / "transcription-pipeline" / "state.json"

# ── Voice library (Phase 3) ─────────────────────────────────────────────────
VOICE_LIBRARY_DIR = HIDOCK_ROOT / "Voice Library"
