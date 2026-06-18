"""Imported-recordings store for the Windows app.

Mirrors the macOS "Import Audio File…" feature: a local audio/video file is
copied into ~/HiDock/Recordings/ under the HiDock filename convention and
tracked here so it appears in the recordings table under a virtual "Imported"
device, transcribe-able without any device download.

The persisted list lives at ~/HiDock/imported_recordings.json.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from core.config import HIDOCK_ROOT, RECORDINGS_DIR

IMPORTED_DEVICE_ID = "imported"
IMPORTED_DEVICE_NAME = "Imported"
_STORE = HIDOCK_ROOT / "imported_recordings.json"

# Audio extensions copied as-is; video extensions get their audio extracted via
# ffmpeg when it's available (else copied and left for the pipeline to handle).
_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"}
_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}
ALLOWED_EXTS = _AUDIO_EXTS | _VIDEO_EXTS


def _load() -> list[dict]:
    if not _STORE.exists():
        return []
    try:
        data = json.loads(_STORE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save(items: list[dict]) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=2) + "\n", encoding="utf-8")
    tmp.replace(_STORE)


def _hidock_name(stem: str, when: datetime) -> str:
    """HiDock filename convention: YYYYMonDD-HHMMSS-<stem>."""
    safe = "".join(c for c in stem if c.isalnum() or c in " -_").strip() or "Imported"
    return f"{when:%Y%b%d-%H%M%S}-{safe}.mp3"


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def import_file(src: Path) -> dict:
    """Copy/convert ``src`` into the recordings folder and record it.

    Returns the stored entry dict. Raises on unsupported type or copy failure.
    """
    src = Path(src).expanduser().resolve()
    ext = src.suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise ValueError(f"Unsupported file type: {ext}")
    RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
    when = datetime.now()
    out_name = _hidock_name(src.stem, when)
    out_path = RECORDINGS_DIR / out_name

    if ext in _VIDEO_EXTS and _ffmpeg():
        # Extract the audio track to mp3.
        subprocess.run(
            [_ffmpeg(), "-y", "-i", str(src), "-vn", "-acodec", "libmp3lame", str(out_path)],
            check=True, capture_output=True,
        )
    elif ext == ".mp3":
        shutil.copy2(src, out_path)
    elif _ffmpeg():
        # Transcode other audio formats to mp3 for a consistent pipeline input.
        subprocess.run(
            [_ffmpeg(), "-y", "-i", str(src), str(out_path)],
            check=True, capture_output=True,
        )
    else:
        # No ffmpeg: keep the original container/extension.
        out_path = RECORDINGS_DIR / f"{out_path.stem}{ext}"
        shutil.copy2(src, out_path)

    size = out_path.stat().st_size if out_path.exists() else 0
    entry = {
        "name": out_path.name,
        "output_name": out_path.name,
        "output_path": str(out_path),
        "length": size,
        "create_date": when.strftime("%Y-%m-%d"),
        "create_time": when.strftime("%H:%M:%S"),
        "source": str(src),
        "imported_at": when.isoformat(),
    }
    items = _load()
    items.append(entry)
    _save(items)
    return entry


def list_imported() -> list[dict]:
    """Imported entries whose files still exist on disk (prunes missing ones)."""
    items = _load()
    present = [e for e in items if Path(e.get("output_path", "")).exists()]
    if len(present) != len(items):
        _save(present)
    return present


def remove_import(output_path: str, delete_file: bool = True) -> None:
    items = [e for e in _load() if e.get("output_path") != output_path]
    _save(items)
    if delete_file:
        try:
            Path(output_path).unlink()
        except OSError:
            pass
