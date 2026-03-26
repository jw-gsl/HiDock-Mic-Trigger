"""Voice library management using speaker embeddings.

Stores enrolled speaker embeddings as JSON for quick identification.
Uses cosine similarity for matching and running-average merging for
incremental enrollment. Supports both neural (TitaNet) and MFCC embeddings.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from shared.audio_utils import extract_embedding, load_audio

VOICE_LIBRARY_DIR = Path.home() / "HiDock" / "Voice Library"
EMBEDDINGS_FILE = VOICE_LIBRARY_DIR / "embeddings.json"

_EMBEDDING_DIM = 40
_MODEL_VERSION = "mfcc-v1"

# Neural embedding constants
_NEURAL_EMBEDDING_DIM = 192
_NEURAL_MODEL_VERSION = "titanet-small"


def _get_speaker_embed_session():
    """Try to load the TitaNet speaker embedding ONNX model.

    Returns:
        onnxruntime.InferenceSession or None if not available.
    """
    try:
        from shared.models import MODELS_DIR, SPEAKER_EMBED_FILENAME

        model_path = MODELS_DIR / SPEAKER_EMBED_FILENAME
        if not model_path.exists():
            return None

        import onnxruntime as ort

        return ort.InferenceSession(
            str(model_path),
            providers=["CPUExecutionProvider"],
        )
    except ImportError:
        return None
    except Exception:
        return None


def cosine_similarity(a: np.ndarray | list, b: np.ndarray | list) -> float:
    """Compute cosine similarity between two vectors.

    Args:
        a: First vector.
        b: Second vector.

    Returns:
        Cosine similarity in range [-1, 1].
    """
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a < 1e-10 or norm_b < 1e-10:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def load_library() -> dict:
    """Load the voice library from disk.

    Returns:
        Library dict with "speakers" key mapping names to speaker data.
    """
    if not EMBEDDINGS_FILE.exists():
        return {"speakers": {}}
    try:
        data = json.loads(EMBEDDINGS_FILE.read_text(encoding="utf-8"))
        if "speakers" not in data:
            data["speakers"] = {}
        return data
    except (json.JSONDecodeError, OSError):
        return {"speakers": {}}


def save_library(lib: dict) -> None:
    """Save the voice library to disk.

    Args:
        lib: Library dict to persist.
    """
    VOICE_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    EMBEDDINGS_FILE.write_text(
        json.dumps(lib, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def enroll_speaker(
    name: str,
    audio_path: str | Path,
    segment_start: float | None = None,
    segment_end: float | None = None,
) -> dict:
    """Enroll or update a speaker in the voice library.

    Uses neural TitaNet embeddings when the model is available,
    otherwise falls back to MFCC. If the speaker already exists, the
    new embedding is merged using a running average weighted by sample count.

    Note: When upgrading from MFCC to neural embeddings, existing MFCC
    entries are preserved but new enrollments will use the neural model.
    The speaker will need to be re-enrolled to fully upgrade.

    Args:
        name: Speaker name.
        audio_path: Path to the audio file containing this speaker's voice.
        segment_start: Optional start time in seconds (use a subsection).
        segment_end: Optional end time in seconds.

    Returns:
        Updated speaker entry dict.
    """
    audio = load_audio(audio_path, sr=16000)

    if segment_start is not None or segment_end is not None:
        sr = 16000
        s = int((segment_start or 0) * sr)
        e = int((segment_end or len(audio) / sr) * sr)
        audio = audio[max(0, s) : min(len(audio), e)]

    # Try neural embeddings first
    session = _get_speaker_embed_session()
    if session is not None:
        embedding = extract_embedding(audio, sr=16000, onnx_session=session)
        embed_dim = len(embedding)
        model_version = _NEURAL_MODEL_VERSION
    else:
        embedding = extract_embedding(audio, sr=16000, n_mfcc=_EMBEDDING_DIM)
        embed_dim = _EMBEDDING_DIM
        model_version = _MODEL_VERSION

    lib = load_library()
    now = datetime.now(timezone.utc).isoformat()

    if name in lib["speakers"]:
        existing = lib["speakers"][name]
        old_model = existing.get("model", _MODEL_VERSION)

        # If upgrading from MFCC to neural, replace the embedding entirely
        if old_model != model_version:
            existing["embedding"] = embedding.tolist()
            existing["embedding_dim"] = embed_dim
            existing["model"] = model_version
            existing["sample_count"] = 1
            existing["last_updated"] = now
        else:
            # Running average merge (same model type)
            old_emb = np.array(existing["embedding"], dtype=np.float64)
            old_count = existing.get("sample_count", 1)
            new_emb = (old_emb * old_count + embedding.astype(np.float64)) / (old_count + 1)
            # Re-normalize for neural embeddings
            if model_version == _NEURAL_MODEL_VERSION:
                norm = np.linalg.norm(new_emb)
                if norm > 1e-10:
                    new_emb = new_emb / norm
            existing["embedding"] = new_emb.tolist()
            existing["sample_count"] = old_count + 1
            existing["last_updated"] = now
    else:
        lib["speakers"][name] = {
            "embedding": embedding.tolist(),
            "embedding_dim": embed_dim,
            "model": model_version,
            "sample_count": 1,
            "enrolled_at": now,
            "last_updated": now,
        }

    save_library(lib)
    return lib["speakers"][name]


def identify_speaker(
    embedding: np.ndarray | list,
    threshold: float = 0.7,
) -> tuple[str | None, float]:
    """Identify a speaker from the voice library by embedding similarity.

    Only compares against speakers whose embeddings have the same dimension
    (to avoid comparing neural vs MFCC embeddings).

    Args:
        embedding: Speaker embedding vector.
        threshold: Minimum cosine similarity to accept a match.

    Returns:
        (name, confidence) if a match is found, or (None, 0.0).
    """
    lib = load_library()
    if not lib["speakers"]:
        return (None, 0.0)

    embedding = np.asarray(embedding)
    embed_dim = len(embedding)

    best_name = None
    best_score = -1.0

    for name, data in lib["speakers"].items():
        stored_dim = data.get("embedding_dim", len(data["embedding"]))
        # Only compare embeddings of the same dimension
        if stored_dim != embed_dim:
            continue
        score = cosine_similarity(embedding, data["embedding"])
        if score > best_score:
            best_score = score
            best_name = name

    if best_score >= threshold:
        return (best_name, best_score)
    return (None, 0.0)


def identify_speakers(
    embeddings: np.ndarray | list,
    threshold: float = 0.7,
) -> dict[int, tuple[str | None, float]]:
    """Identify speakers for multiple embeddings.

    Args:
        embeddings: Array of shape (N, embedding_dim) or list of embedding vectors.
        threshold: Minimum cosine similarity to accept a match.

    Returns:
        Dict mapping index -> (name, confidence).
    """
    result = {}
    for i, emb in enumerate(embeddings):
        name, conf = identify_speaker(emb, threshold=threshold)
        result[i] = (name, conf)
    return result


def list_speakers() -> list[dict]:
    """List all enrolled speakers.

    Returns:
        List of dicts with name, sample_count, and last_updated.
    """
    lib = load_library()
    speakers = []
    for name, data in lib["speakers"].items():
        speakers.append({
            "name": name,
            "sample_count": data.get("sample_count", 1),
            "last_updated": data.get("last_updated", ""),
        })
    return speakers


def delete_speaker(name: str) -> bool:
    """Delete a speaker from the voice library.

    Args:
        name: Speaker name to remove.

    Returns:
        True if the speaker was found and deleted, False otherwise.
    """
    lib = load_library()
    if name not in lib["speakers"]:
        return False
    del lib["speakers"][name]
    save_library(lib)
    return True


def rename_speaker(old_name: str, new_name: str) -> bool:
    """Rename a speaker in the voice library.

    Args:
        old_name: Current name.
        new_name: New name.

    Returns:
        True if the rename succeeded, False if old_name not found.
    """
    lib = load_library()
    if old_name not in lib["speakers"]:
        return False
    if new_name in lib["speakers"]:
        raise ValueError(f"Speaker '{new_name}' already exists in the library")
    lib["speakers"][new_name] = lib["speakers"].pop(old_name)
    lib["speakers"][new_name]["last_updated"] = datetime.now(timezone.utc).isoformat()
    save_library(lib)
    return True


# ── CLI ─────────────────────────────────────────────────────────────────────

def _cli():
    """Command-line interface for voice library management."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Voice library management CLI")
    sub = parser.add_subparsers(dest="command")

    # list
    sub.add_parser("list", help="List all enrolled speakers (JSON)")

    # enroll
    enroll_p = sub.add_parser("enroll", help="Enroll a speaker")
    enroll_p.add_argument("--name", required=True, help="Speaker name")
    enroll_p.add_argument("--audio", required=True, help="Path to audio file")
    enroll_p.add_argument("--start", type=float, default=None, help="Segment start (seconds)")
    enroll_p.add_argument("--end", type=float, default=None, help="Segment end (seconds)")

    # delete
    delete_p = sub.add_parser("delete", help="Delete a speaker")
    delete_p.add_argument("--name", required=True, help="Speaker name to delete")

    # rename
    rename_p = sub.add_parser("rename", help="Rename a speaker")
    rename_p.add_argument("--old", required=True, help="Current name")
    rename_p.add_argument("--new", required=True, help="New name")

    args = parser.parse_args()

    if args.command == "list":
        speakers = list_speakers()
        print(json.dumps(speakers, indent=2, ensure_ascii=False))

    elif args.command == "enroll":
        try:
            result = enroll_speaker(
                name=args.name,
                audio_path=args.audio,
                segment_start=args.start,
                segment_end=args.end,
            )
            print(json.dumps({"ok": True, "sample_count": result.get("sample_count", 1)}))
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
            sys.exit(1)

    elif args.command == "delete":
        ok = delete_speaker(args.name)
        if ok:
            print(json.dumps({"ok": True}))
        else:
            print(json.dumps({"ok": False, "error": f"Speaker '{args.name}' not found"}), file=sys.stderr)
            sys.exit(1)

    elif args.command == "rename":
        try:
            ok = rename_speaker(args.old, args.new)
            if ok:
                print(json.dumps({"ok": True}))
            else:
                print(json.dumps({"ok": False, "error": f"Speaker '{args.old}' not found"}), file=sys.stderr)
                sys.exit(1)
        except ValueError as e:
            print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
            sys.exit(1)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    _cli()
