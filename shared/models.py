"""Model download management for shared speech processing modules.

Handles downloading and caching models used for speech processing:
- Silero VAD for voice activity detection
- TitaNet speaker embedding for neural speaker identification
- Whisper for speech recognition (managed by the app, cataloged here)
"""
from __future__ import annotations

import json
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

# Speaker embedding models — configurable (from minutes v0.10.0)
SPEAKER_EMBED_MODELS = {
    "titanet": {
        "filename": "speaker_embedding.onnx",
        "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/nemo_en_titanet_small.onnx",
        "dim": 192,
        "description": "NeMo TitaNet Small (192-dim, mel-spectrogram input)",
    },
    "campp": {
        "filename": "campp_speaker.onnx",
        "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx",
        "dim": 512,
        "description": "3D-Speaker CAM++ (512-dim, ~12% lower error than TitaNet)",
    },
}

# Default model — can be changed via config
SPEAKER_EMBED_MODEL = "titanet"
SPEAKER_EMBED_FILENAME = SPEAKER_EMBED_MODELS[SPEAKER_EMBED_MODEL]["filename"]
SPEAKER_EMBED_URL = SPEAKER_EMBED_MODELS[SPEAKER_EMBED_MODEL]["url"]

# ── Model Registry ──────────────────────────────────────────────────────────

MODEL_REGISTRY = {
    "whisper": {
        "name": "Speech Recognition (Whisper)",
        "filename": "ggml-large-v3-turbo-q5_0.bin",
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin",
        "size_mb": 547,
        "required": True,
        "description": "Transcribes speech to text. Required for transcription.",
    },
    "silero_vad": {
        "name": "Voice Detection (Silero VAD)",
        "filename": "silero_vad.onnx",
        "url": "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx",
        "size_mb": 2,
        "required": False,
        "description": "Industry-leading voice activity detection. Identifies speech segments with high accuracy.",
    },
    "speaker_embed": {
        "name": "Speaker Recognition (TitaNet)",
        "filename": "speaker_embedding.onnx",
        "url": "https://huggingface.co/csukuangfj/sherpa-onnx-nemo-speaker-verification-titanet_small/resolve/main/model.onnx",
        "size_mb": 10,
        "required": False,
        "description": "Neural speaker recognition trained on thousands of voices. Identifies who is speaking across recordings.",
    },
}


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


def ensure_speaker_embed(model_key: str | None = None) -> Path:
    """Ensure the speaker embedding ONNX model is available locally."""
    key = model_key or SPEAKER_EMBED_MODEL
    if key in SPEAKER_EMBED_MODELS:
        model = SPEAKER_EMBED_MODELS[key]
        return download_model_if_needed(model["url"], model["filename"])
    return download_model_if_needed(SPEAKER_EMBED_URL, SPEAKER_EMBED_FILENAME)


def get_speaker_embed_model_name() -> str:
    """Return the current speaker embedding model name."""
    return SPEAKER_EMBED_MODEL


def set_speaker_embed_model(key: str) -> None:
    """Switch the speaker embedding model. Clears cached session."""
    global SPEAKER_EMBED_MODEL, SPEAKER_EMBED_FILENAME, SPEAKER_EMBED_URL
    if key not in SPEAKER_EMBED_MODELS:
        raise ValueError(f"Unknown model: {key}. Choose from: {list(SPEAKER_EMBED_MODELS.keys())}")
    SPEAKER_EMBED_MODEL = key
    SPEAKER_EMBED_FILENAME = SPEAKER_EMBED_MODELS[key]["filename"]
    SPEAKER_EMBED_URL = SPEAKER_EMBED_MODELS[key]["url"]


# Backward-compatible alias
ensure_speaker_embedding_model = ensure_speaker_embed


# ── Model Status & Management ───────────────────────────────────────────────


def get_model_status() -> dict[str, dict]:
    """Return the status of each registered model.

    Returns:
        Dict keyed by model registry key, each value containing:
        name, description, size_mb, filename, installed, file_size_bytes.
    """
    statuses = {}
    for key, info in MODEL_REGISTRY.items():
        filepath = MODELS_DIR / info["filename"]
        installed = filepath.exists() and filepath.stat().st_size > 1000
        file_size = filepath.stat().st_size if installed else 0
        statuses[key] = {
            "name": info["name"],
            "description": info["description"],
            "size_mb": info["size_mb"],
            "filename": info["filename"],
            "url": info["url"],
            "required": info["required"],
            "installed": installed,
            "file_size_bytes": file_size,
        }
    return statuses


def delete_model(model_key: str) -> bool:
    """Delete a downloaded model file.

    Args:
        model_key: Key from MODEL_REGISTRY (e.g. "silero_vad", "speaker_embed").

    Returns:
        True if the file was deleted, False if not found or is required.
    """
    if model_key not in MODEL_REGISTRY:
        return False
    info = MODEL_REGISTRY[model_key]
    filepath = MODELS_DIR / info["filename"]
    if not filepath.exists():
        return False
    filepath.unlink()
    return True


# ── CLI ──────────────────────────────────────────────────────────────────────


def _cli():
    """Command-line interface for model management."""
    if len(sys.argv) < 2:
        print("Usage: models.py {status|download <key>|delete <key>}", file=sys.stderr)
        sys.exit(1)

    command = sys.argv[1]

    if command == "status":
        statuses = get_model_status()
        print(json.dumps(statuses, indent=2))

    elif command == "download":
        if len(sys.argv) < 3:
            print("Usage: models.py download <model_key>", file=sys.stderr)
            sys.exit(1)
        key = sys.argv[2]
        if key not in MODEL_REGISTRY:
            print(json.dumps({"ok": False, "error": f"Unknown model key: {key}"}))
            sys.exit(1)
        info = MODEL_REGISTRY[key]
        try:
            path = download_model_if_needed(info["url"], info["filename"])
            print(json.dumps({"ok": True, "path": str(path)}))
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)}))
            sys.exit(1)

    elif command == "delete":
        if len(sys.argv) < 3:
            print("Usage: models.py delete <model_key>", file=sys.stderr)
            sys.exit(1)
        key = sys.argv[2]
        if key not in MODEL_REGISTRY:
            print(json.dumps({"ok": False, "error": f"Unknown model key: {key}"}))
            sys.exit(1)
        ok = delete_model(key)
        if ok:
            print(json.dumps({"ok": True}))
        else:
            print(json.dumps({"ok": False, "error": "Model file not found"}))
            sys.exit(1)

    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        print("Usage: models.py {status|download <key>|delete <key>}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
