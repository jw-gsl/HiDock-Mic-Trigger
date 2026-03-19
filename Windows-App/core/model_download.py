"""Download whisper.cpp model with progress reporting."""
from __future__ import annotations

import urllib.request
from pathlib import Path
from typing import Callable

from core.config import MODELS_DIR, WHISPER_MODEL_FILENAME, WHISPER_MODEL_URL, whisper_model_path


def download_model(
    on_progress: Callable[[int, int], None] | None = None,
    on_complete: Callable[[], None] | None = None,
    on_error: Callable[[str], None] | None = None,
) -> None:
    """Download the whisper.cpp model file.

    Args:
        on_progress: callback(bytes_downloaded, total_bytes)
        on_complete: called when download finishes successfully
        on_error: called with error message on failure
    """
    dest = whisper_model_path()
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".downloading")

    try:
        req = urllib.request.Request(WHISPER_MODEL_URL, headers={"User-Agent": "HiDock/1.0"})
        resp = urllib.request.urlopen(req, timeout=30)
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        chunk_size = 256 * 1024  # 256 KB chunks

        with open(tmp, "wb") as f:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if on_progress:
                    on_progress(downloaded, total)

        # Rename to final path
        if dest.exists():
            dest.unlink()
        tmp.rename(dest)

        if on_complete:
            on_complete()

    except Exception as e:
        # Clean up partial download
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        if on_error:
            on_error(str(e))
