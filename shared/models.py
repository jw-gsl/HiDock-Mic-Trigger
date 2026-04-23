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

"""Model registry keyed by registry_key, with per-model metadata.

Each entry declares which `stage` of the pipeline it belongs to, so the
Model Manager UI can group them. Stages:
  - "transcription" — speech → text (Whisper, Parakeet)
  - "diarization" — who spoke when (built-in lite pipeline, Sortformer)
  - "vad" — where is speech vs silence (Silero)
  - "voice_library" — identify known speakers across recordings (TitaNet)

For stages with alternatives (transcription, diarization), the active
backend is persisted in pipeline_backends.json and read at runtime.
The `active` field is NOT hardcoded here any more; it's derived.

Model flavours:
  - Downloadable (`filename` + `url` + `size_mb`): a file we fetch.
  - Built-in (`built_in: True`): code-only, no download, always available.
  - NeMo-managed (`nemo_model: True`): weights fetched by NeMo on first
    use, but we still need to install nemo-toolkit before it works.
"""
MODEL_REGISTRY = {
    "whisper": {
        "name": "Whisper large-v3-turbo",
        "filename": "ggml-large-v3-turbo-q5_0.bin",
        "url": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin",
        "size_mb": 547,
        "required": True,
        "stage": "transcription",
        "stage_label": "Transcription (Speech → Text)",
        "category": "pipeline",
        "backend_key": "whisper",
        "description": "OpenAI Whisper large-v3-turbo, 99 languages. Reliable and multilingual; slower than Parakeet on English-only meetings.",
    },
    "parakeet": {
        "name": "Parakeet TDT 0.6B v2 (MLX)",
        # Managed by parakeet-mlx via HuggingFace hub cache, not MODELS_DIR.
        # filename is informational for the UI; actual weights live under
        # ~/.cache/huggingface/hub/ and are pulled on first transcription.
        "filename": "mlx-community--parakeet-tdt-0.6b-v2",
        "url": "https://huggingface.co/mlx-community/parakeet-tdt-0.6b-v2",
        "size_mb": 1200,
        "required": False,
        "platform": "darwin-arm64",
        "managed_externally": True,
        "stage": "transcription",
        "stage_label": "Transcription (Speech → Text)",
        "category": "pipeline",
        "backend_key": "parakeet",
        "experimental": True,
        "description": "NVIDIA Parakeet TDT 0.6B v2 via MLX. English only, ~60× real-time on Apple Silicon. Tops the English ASR leaderboard. Attribution: CC-BY-4.0.",
    },
    "silero_vad": {
        "name": "Silero VAD",
        "filename": "silero_vad.onnx",
        "url": "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx",
        "size_mb": 2,
        "required": False,
        "stage": "vad",
        "stage_label": "Voice Activity Detection",
        "category": "supporting",
        # Who consumes this stage — shown in the UI so the user knows
        # why it's needed and when it becomes dead weight. If the user
        # picks Sortformer for diarization, Silero becomes unused.
        "used_by": "Built-in Lite diarizer (not used by Sortformer)",
        "backend_key": "silero",
        "description": "Neural voice activity detection. Identifies speech segments with high accuracy.",
    },
    # Our current diarization pipeline (diarize_lite.py). Built-in means
    # it's always available with no download — the UI still lists it so
    # the user can compare it to Sortformer and pick which one runs.
    "diarize_lite": {
        "name": "Built-in Lite (Silero + TitaNet + clustering)",
        "stage": "diarization",
        "stage_label": "Speaker Diarization",
        "category": "pipeline",
        "backend_key": "lite",
        "built_in": True,
        "size_mb": 0,   # no download — VAD + embedding models cover this
        "depends_on": "Silero VAD + TitaNet",
        "description": "Three-stage pipeline that reuses the selected VAD (Silero) + Speaker Embeddings (TitaNet) backends. Hierarchical clustering groups speakers. No extra download. Weaker on short, rapid speaker turns than Sortformer.",
    },
    # NeMo Sortformer — end-to-end neural diarization. Requires the
    # `nemo-toolkit` Python package (~2 GB including torch deps) plus the
    # Sortformer model weights which NeMo fetches from HuggingFace hub
    # on first use. The UI's "Download" action handles both steps.
    "diarize_sortformer": {
        "name": "NeMo Sortformer 4-speaker",
        "stage": "diarization",
        "stage_label": "Speaker Diarization",
        "category": "pipeline",
        "backend_key": "sortformer",
        "nemo_model": True,
        "nemo_model_name": "nvidia/diar_sortformer_4spk-v1",
        "pip_package": "nemo-toolkit",
        # Sortformer itself is ~250 MB; nemo-toolkit brings torch + deps
        # — budget ~2 GB total install footprint.
        "size_mb": 2000,
        "experimental": True,
        "depends_on": "Self-contained (no supporting models needed)",
        "description": "State-of-the-art end-to-end neural diarization (CC-BY-4.0). Handles up to 4 speakers with much better per-turn accuracy than the lite pipeline. Includes its own VAD and speaker representation — does not use the Silero / TitaNet entries below. CPU-only on macOS. Installing also installs the NeMo toolkit (~2 GB).",
    },
    "speaker_embed": {
        "name": "TitaNet",
        "filename": "speaker_embedding.onnx",
        "url": "https://huggingface.co/csukuangfj/sherpa-onnx-nemo-speaker-verification-titanet_small/resolve/main/model.onnx",
        "size_mb": 10,
        "required": False,
        "stage": "embedding",
        "stage_label": "Speaker Embeddings",
        "category": "supporting",
        "used_by": "Built-in Lite diarizer + Voice Library (not used by Sortformer)",
        "backend_key": "titanet",
        "description": "Neural speaker embeddings trained on thousands of voices. Turns a speech clip into a 192-dim vector used for clustering speakers within a meeting and matching them to a personal voice library across meetings.",
    },
}

# Pipeline-stage entries are the user's primary choices; supporting-stage
# entries are infrastructure that a pipeline backend depends on.
# Category drives top-level grouping in the Model Manager UI.
_DEFAULT_CATEGORY_FOR_STAGE = {
    "transcription": "pipeline",
    "diarization": "pipeline",
    "vad": "supporting",
    "embedding": "supporting",
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


# ── Pipeline Backend Selection ──────────────────────────────────────────────
#
# Users can pick which model runs for stages with alternatives
# (Transcription: Whisper vs Parakeet; Diarization: lite vs Sortformer).
# Selection persists in pipeline_backends.json next to state/config, so
# it survives app restarts and survives editing the Python code.

PIPELINE_BACKENDS_PATH = Path.home() / "HiDock" / "pipeline_backends.json"

_DEFAULT_BACKENDS = {
    "transcription": "whisper",
    "diarization": "lite",
    "vad": "silero",
    "voice_library": "titanet",
}


def load_pipeline_backends() -> dict[str, str]:
    """Return the currently-selected backend for each stage. Falls back
    to defaults for missing keys so new stages introduced in code don't
    crash old config files."""
    merged = dict(_DEFAULT_BACKENDS)
    if PIPELINE_BACKENDS_PATH.exists():
        try:
            persisted = json.loads(PIPELINE_BACKENDS_PATH.read_text())
            if isinstance(persisted, dict):
                merged.update({k: v for k, v in persisted.items() if isinstance(v, str)})
        except (json.JSONDecodeError, OSError):
            pass
    return merged


def save_pipeline_backends(backends: dict[str, str]) -> None:
    """Persist the backend selections atomically."""
    PIPELINE_BACKENDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PIPELINE_BACKENDS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(backends, indent=2))
    tmp.replace(PIPELINE_BACKENDS_PATH)


def set_active_backend(stage: str, backend_key: str) -> dict[str, str]:
    """Set the active backend for a stage; returns the new full mapping."""
    backends = load_pipeline_backends()
    backends[stage] = backend_key
    save_pipeline_backends(backends)
    return backends


def _nemo_toolkit_available() -> bool:
    """Whether `nemo-toolkit` is importable right now.

    Used to tell the UI whether Sortformer is effectively installed.
    Downloading the Sortformer model entry implies installing nemo.
    """
    try:
        import importlib.util
        return importlib.util.find_spec("nemo") is not None
    except Exception:
        return False


# ── Model Status & Management ───────────────────────────────────────────────


def get_model_status() -> dict[str, dict]:
    """Return the status of each registered model.

    Installed-ness and active flags are both derived, not stored in the
    registry — so adding/removing selections doesn't require code edits.
    """
    backends = load_pipeline_backends()
    statuses = {}
    for key, info in MODEL_REGISTRY.items():
        stage = info.get("stage", "other")
        # Decide "installed" differently per flavour:
        #   - built-in: always True (no download)
        #   - nemo-managed: need nemo-toolkit importable + model-name set
        #     (we don't probe HuggingFace cache — NeMo handles that on use)
        #   - regular (file + url): check MODELS_DIR
        if info.get("built_in"):
            installed = True
            file_size = 0
            filename = None
            url = None
        elif info.get("nemo_model"):
            installed = _nemo_toolkit_available()
            file_size = 0
            filename = info.get("nemo_model_name")
            url = None
        else:
            filepath = MODELS_DIR / info["filename"]
            installed = filepath.exists() and filepath.stat().st_size > 1000
            file_size = filepath.stat().st_size if installed else 0
            filename = info["filename"]
            url = info.get("url")

        statuses[key] = {
            "name": info["name"],
            "description": info["description"],
            "size_mb": info.get("size_mb", 0),
            "filename": filename,
            "url": url,
            "required": info.get("required", False),
            "installed": installed,
            "file_size_bytes": file_size,
            "stage": stage,
            "stage_label": info.get("stage_label", stage.capitalize()),
            "category": info.get("category", _DEFAULT_CATEGORY_FOR_STAGE.get(stage, "pipeline")),
            "used_by": info.get("used_by", ""),
            "depends_on": info.get("depends_on", ""),
            "backend_key": info.get("backend_key", key),
            # Active = this entry's backend_key matches the persisted
            # selection for its stage. Makes the UI "ACTIVE" badge
            # reflect the live config, not a hardcoded registry flag.
            "active": backends.get(stage) == info.get("backend_key", key),
            "experimental": info.get("experimental", False),
            "built_in": info.get("built_in", False),
            "nemo_model": info.get("nemo_model", False),
            "pip_package": info.get("pip_package"),
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
    if info.get("built_in"):
        return False
    if "filename" not in info:
        return False
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
            # Built-in models never need downloading.
            if info.get("built_in"):
                print(json.dumps({"ok": True, "built_in": True}))
                return
            # NeMo-managed: pip install the toolkit (HuggingFace cache
            # then fetches the model weights lazily on first use).
            if info.get("nemo_model"):
                pkg = info.get("pip_package", "nemo-toolkit")
                print(f"Installing Python package: {pkg}", file=sys.stderr)
                import subprocess
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", pkg],
                    capture_output=True, text=True,
                )
                if result.returncode != 0:
                    print(json.dumps({
                        "ok": False,
                        "error": f"pip install {pkg} failed: {result.stderr[-500:]}",
                    }))
                    sys.exit(1)
                print(json.dumps({"ok": True, "pip_package_installed": pkg}))
                return
            # Regular file download.
            path = download_model_if_needed(info["url"], info["filename"])
            print(json.dumps({"ok": True, "path": str(path)}))
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)}))
            sys.exit(1)

    elif command == "set-active":
        if len(sys.argv) < 3:
            print("Usage: models.py set-active <model_key>", file=sys.stderr)
            sys.exit(1)
        key = sys.argv[2]
        if key not in MODEL_REGISTRY:
            print(json.dumps({"ok": False, "error": f"Unknown model key: {key}"}))
            sys.exit(1)
        info = MODEL_REGISTRY[key]
        stage = info.get("stage")
        backend_key = info.get("backend_key", key)
        if not stage:
            print(json.dumps({"ok": False, "error": f"Model {key} has no stage"}))
            sys.exit(1)
        backends = set_active_backend(stage, backend_key)
        print(json.dumps({"ok": True, "backends": backends}))

    elif command == "backends":
        # Dump the current backend selection for debugging/inspection.
        print(json.dumps(load_pipeline_backends(), indent=2))

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
