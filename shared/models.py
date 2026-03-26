"""Model download management for shared speech processing modules.

Handles downloading and caching the Silero VAD ONNX model used for
voice activity detection. Speaker embeddings use MFCC (no model needed).
"""
from __future__ import annotations

import ssl
import sys
import urllib.request
from pathlib import Path

MODELS_DIR = Path.home() / "HiDock" / "Speech-to-Text"

# Silero VAD — lightweight voice activity detection (~2MB)
SILERO_VAD_FILENAME = "silero_vad.onnx"
SILERO_VAD_URL = (
    "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx"
)


def _ssl_context() -> ssl.SSLContext:
    """Create an SSL context with fallbacks for various environments."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    try:
        ctx = ssl.create_default_context()
        # Quick test — if the default context works, use it
        return ctx
    except ssl.SSLError:
        pass
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def download_model_if_needed(
    url: str,
    filename: str,
    on_progress: callable | None = None,
) -> Path:
    """Download a model file to MODELS_DIR if it does not already exist.

    Args:
        url: Remote URL to download from.
        filename: Local filename inside MODELS_DIR.
        on_progress: Optional callback receiving (downloaded_bytes, total_bytes).

    Returns:
        Path to the local model file.
    """
    dest = MODELS_DIR / filename
    if dest.exists() and dest.stat().st_size > 1000:
        return dest

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".downloading")

    print(f"Downloading {filename} to {dest}...", file=sys.stderr)

    ctx = _ssl_context()
    req = urllib.request.Request(url, headers={"User-Agent": "HiDock/1.0"})
    resp = urllib.request.urlopen(req, timeout=60, context=ctx)
    total = int(resp.headers.get("Content-Length", 0))
    downloaded = 0

    with open(tmp, "wb") as f:
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            if on_progress:
                on_progress(downloaded, total)
            elif total > 0:
                pct = int(downloaded * 100 / total)
                print(f"  {pct}%", file=sys.stderr, flush=True)

    if dest.exists():
        dest.unlink()
    tmp.rename(dest)
    print(f"Download complete: {filename}", file=sys.stderr)
    return dest


def ensure_silero_vad() -> Path:
    """Ensure the Silero VAD ONNX model is available locally."""
    return download_model_if_needed(SILERO_VAD_URL, SILERO_VAD_FILENAME)
