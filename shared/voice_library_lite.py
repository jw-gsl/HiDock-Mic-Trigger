"""Voice library management using speaker embeddings.

Stores enrolled speakers as JSON. Each speaker keeps MULTIPLE embedding samples
(exemplars) rather than a single running-average centroid — matching is done
best-of-samples (max cosine over a speaker's exemplars), which is far more robust
to the same person sounding different across meetings/mics than one average.

Legacy single-embedding entries are migrated to a one-element sample list on
load, so old libraries keep working.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np

from shared.audio_utils import extract_embedding, load_audio

VOICE_LIBRARY_DIR = Path.home() / "HiDock" / "Voice Library"
EMBEDDINGS_FILE = VOICE_LIBRARY_DIR / "embeddings.json"

_EMBEDDING_DIM = 40
_MODEL_VERSION = "mfcc-v1"

# Neural embedding constants
_NEURAL_EMBEDDING_DIM = 192
_NEURAL_MODEL_VERSION = "titanet-small"

# Multi-exemplar tuning
_MAX_SAMPLES = 30       # per speaker; keep the most recent this many
_DEDUP_THRESHOLD = 0.98  # skip a new sample this similar to an existing one


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _samples_of(entry: dict) -> list[dict]:
    """Return a speaker entry's exemplar list, migrating a legacy single
    `embedding` into a one-element `samples` list."""
    samples = entry.get("samples")
    if isinstance(samples, list) and samples:
        return samples
    if "embedding" in entry:
        return [{
            "embedding": entry["embedding"],
            "embedding_dim": entry.get("embedding_dim", len(entry["embedding"])),
            "model": entry.get("model", _MODEL_VERSION),
            "source": "legacy",
            "added_at": entry.get("last_updated") or entry.get("enrolled_at", ""),
        }]
    return []


def load_library() -> dict:
    """Load the voice library from disk, migrating legacy entries to the
    multi-sample schema so callers can rely on `samples`.

    Returns:
        Library dict with "speakers" key mapping names to speaker data.
    """
    if not EMBEDDINGS_FILE.exists():
        return {"speakers": {}}
    try:
        data = json.loads(EMBEDDINGS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"speakers": {}}
    if "speakers" not in data:
        data["speakers"] = {}
    for entry in data["speakers"].values():
        samples = _samples_of(entry)
        entry["samples"] = samples
        entry.pop("embedding", None)   # single source of truth = samples
        if samples:
            entry.setdefault("embedding_dim", samples[0].get("embedding_dim"))
            entry.setdefault("model", samples[0].get("model"))
    return data


def save_library(lib: dict) -> None:
    """Save the voice library to disk atomically (temp file + rename)."""
    VOICE_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    content = json.dumps(lib, indent=2, ensure_ascii=False) + "\n"
    fd, tmp_path = tempfile.mkstemp(
        dir=VOICE_LIBRARY_DIR, prefix=EMBEDDINGS_FILE.name, suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, EMBEDDINGS_FILE)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _canonical_speaker_name(lib: dict, name: str) -> str:
    """Strip/collapse whitespace and, if a case-insensitive match already exists
    in the library, reuse that key so we don't create 'Leslie Mcneely' next to
    'Leslie McNeely'. Typos (Wildmsith vs Wildsmith) still need an explicit merge."""
    cleaned = " ".join((name or "").strip().split())
    if not cleaned:
        return cleaned
    folded = cleaned.casefold()
    for existing in lib.get("speakers", {}):
        if existing.casefold() == folded:
            return existing
    return cleaned


def _enroll_into(lib: dict, name: str, embedding: np.ndarray | list,
                 embed_dim: int, model: str, source: str) -> dict:
    """Append `embedding` as an exemplar of `name` in `lib` (no disk write).

    Normalises the embedding, skips near-duplicates, and caps the sample count.
    Returns the speaker entry."""
    emb = np.asarray(embedding, dtype=np.float32)
    norm = float(np.linalg.norm(emb))
    if norm > 1e-10:
        emb = emb / norm
    now = _now()

    name = _canonical_speaker_name(lib, name)
    if not name:
        raise ValueError("speaker name is empty")

    entry = lib["speakers"].get(name)
    if entry is None:
        entry = {"enrolled_at": now, "last_updated": now,
                 "embedding_dim": embed_dim, "model": model, "samples": []}
        lib["speakers"][name] = entry
    samples = entry.setdefault("samples", [])

    # Skip a near-identical exemplar (e.g. confirming the same meeting twice).
    for s in samples:
        if s.get("embedding_dim", len(s["embedding"])) == embed_dim \
                and cosine_similarity(emb, s["embedding"]) > _DEDUP_THRESHOLD:
            s["added_at"] = now
            entry["last_updated"] = now
            return entry

    samples.append({
        "embedding": emb.tolist(),
        "embedding_dim": embed_dim,
        "model": model,
        "source": source,
        "added_at": now,
    })
    if len(samples) > _MAX_SAMPLES:      # keep the most recent N
        del samples[0:len(samples) - _MAX_SAMPLES]
    entry["last_updated"] = now
    entry["embedding_dim"] = embed_dim
    entry["model"] = model
    return entry


def enroll_embedding(name: str, embedding: np.ndarray | list, *,
                     embed_dim: int | None = None, model: str | None = None,
                     source: str = "confirm") -> dict:
    """Enroll a precomputed embedding as an exemplar of `name`."""
    embed_dim = embed_dim if embed_dim is not None else len(embedding)
    model = model or _NEURAL_MODEL_VERSION
    lib = load_library()
    entry = _enroll_into(lib, name, embedding, embed_dim, model, source)
    save_library(lib)
    return entry


def enroll_speaker(
    name: str,
    audio_path: str | Path,
    segment_start: float | None = None,
    segment_end: float | None = None,
) -> dict:
    """Enroll a speaker from an audio segment (computes the embedding, then adds
    it as an exemplar). Neural TitaNet when available, else MFCC."""
    audio = load_audio(audio_path, sr=16000)
    if segment_start is not None or segment_end is not None:
        sr = 16000
        s = int((segment_start or 0) * sr)
        e = int((segment_end or len(audio) / sr) * sr)
        audio = audio[max(0, s):min(len(audio), e)]

    session = _get_speaker_embed_session()
    if session is not None:
        embedding = extract_embedding(audio, sr=16000, onnx_session=session)
        embed_dim = len(embedding)
        model_version = _NEURAL_MODEL_VERSION
    else:
        embedding = extract_embedding(audio, sr=16000, n_mfcc=_EMBEDDING_DIM)
        embed_dim = _EMBEDDING_DIM
        model_version = _MODEL_VERSION

    return enroll_embedding(name, embedding, embed_dim=embed_dim,
                            model=model_version, source="audio")


def enroll_from_diarized(name: str, diarized_path: str | Path, speaker_id) -> dict:
    """Enroll the stored per-speaker centroid (`speaker_embeddings[id]`) from a
    diarized sidecar. This is the robust, multi-segment voiceprint the diarizer
    already computed — no audio re-decode (works even for Opus/Plaud)."""
    data = json.loads(Path(diarized_path).read_text(encoding="utf-8"))
    embs = data.get("speaker_embeddings") or {}
    emb = embs.get(str(speaker_id))
    if emb is None:
        raise ValueError(f"no stored embedding for speaker {speaker_id} in {diarized_path}")
    return enroll_embedding(name, emb, embed_dim=len(emb),
                            model=_NEURAL_MODEL_VERSION, source="confirm")


def enroll_from_transcripts(directory: str | Path) -> dict:
    """Build the library from the tagged backlog: for every *_diarized.json in
    `directory`, enrol each TRUSTWORTHY named speaker's stored embedding.

    Trustworthy = a real (non-generic) name that is NOT an unverified auto-match
    — i.e. user-typed / confirmed / legacy hand-tagged. Unverified auto-matches
    are skipped so a wrong guess doesn't poison the library. One disk write."""
    from shared.speaker_meta import is_generic_name

    import sys as _sys
    d = Path(directory)
    lib = load_library()
    enrolled = 0
    per_name: dict[str, int] = {}
    files = 0
    all_files = sorted(d.glob("*_diarized.json"))
    total = len(all_files)
    print(f"PROGRESS:0/{total}", file=_sys.stderr, flush=True)
    for idx, f in enumerate(all_files, start=1):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            print(f"PROGRESS:{idx}/{total}", file=_sys.stderr, flush=True)
            continue
        files += 1
        names = data.get("speaker_names", {}) or {}
        meta = data.get("speaker_meta", {}) or {}
        embs = data.get("speaker_embeddings") or {}
        for sid, nm in names.items():
            if is_generic_name(nm):
                continue
            m = meta.get(sid, {}) or {}
            if m.get("source") == "auto" and not m.get("verified"):
                continue   # uncertain auto-match — don't train on it
            emb = embs.get(sid)
            if emb is None:
                continue
            _enroll_into(lib, nm, emb, len(emb), _NEURAL_MODEL_VERSION, "backfill")
            enrolled += 1
            per_name[nm] = per_name.get(nm, 0) + 1
        print(f"PROGRESS:{idx}/{total}", file=_sys.stderr, flush=True)
    save_library(lib)
    return {"files": files, "enrolled": enrolled, "speakers": per_name}


def _best_similarity(embedding: np.ndarray, entry: dict, dim: int) -> float:
    """Max cosine of `embedding` over a speaker's same-dimension exemplars."""
    best = -1.0
    for s in _samples_of(entry):
        if s.get("embedding_dim", len(s["embedding"])) != dim:
            continue
        sc = cosine_similarity(embedding, s["embedding"])
        if sc > best:
            best = sc
    return best


def library_scores(
    embedding: np.ndarray | list,
    allowed_names: Iterable[str] | None = None,
) -> list[tuple[str, float]]:
    """Best-of-samples similarity of `embedding` against every enrolled speaker
    (same embedding dim only).

    ``allowed_names`` is an optional candidate set supplied by contextual
    matching (for example a calendar attendee list). An empty set is
    intentional: it means context was available but did not identify any
    enrolled candidate, so no voice-library label should be emitted.
    Returns [(name, score)] — used by both identify_speaker and
    speaker_meta.score_speakers."""
    lib = load_library()
    embedding = np.asarray(embedding)
    dim = len(embedding)
    allowed = None if allowed_names is None else set(allowed_names)
    out: list[tuple[str, float]] = []
    for name, entry in lib["speakers"].items():
        if allowed is not None and name not in allowed:
            continue
        s = _best_similarity(embedding, entry, dim)
        if s > -1.0:
            out.append((name, round(float(s), 4)))
    return out


def identify_speaker(
    embedding: np.ndarray | list,
    threshold: float = 0.7,
    allowed_names: Iterable[str] | None = None,
) -> tuple[str | None, float]:
    """Identify a speaker by best-of-exemplars similarity.

    Returns:
        (name, confidence) if the best match clears `threshold`, else (None, 0.0).
    """
    scores = library_scores(embedding, allowed_names=allowed_names)
    if not scores:
        return (None, 0.0)
    best_name, best_score = max(scores, key=lambda x: x[1])
    if best_score >= threshold:
        return (best_name, best_score)
    return (None, 0.0)


def identify_speakers(
    embeddings: np.ndarray | list,
    threshold: float = 0.7,
    allowed_names: Iterable[str] | None = None,
) -> dict[int, tuple[str | None, float]]:
    """Identify speakers for multiple embeddings."""
    return {
        i: identify_speaker(emb, threshold=threshold, allowed_names=allowed_names)
        for i, emb in enumerate(embeddings)
    }


def set_calendar_emails(name: str, emails: Iterable[str]) -> bool:
    """Associate Microsoft 365 attendee email addresses with a voice entry."""
    lib = load_library()
    if name not in lib["speakers"]:
        return False
    cleaned = sorted({str(email).strip().casefold() for email in emails if str(email).strip()})
    if cleaned:
        lib["speakers"][name]["calendar_emails"] = cleaned
    else:
        lib["speakers"][name].pop("calendar_emails", None)
    lib["speakers"][name]["last_updated"] = _now()
    save_library(lib)
    return True


def list_speakers() -> list[dict]:
    """List enrolled speakers with their exemplar count (enrollment depth)."""
    lib = load_library()
    speakers = []
    for name, data in lib["speakers"].items():
        speakers.append({
            "name": name,
            "sample_count": len(_samples_of(data)),
            "last_updated": data.get("last_updated", ""),
        })
    return speakers


def delete_speaker(name: str) -> bool:
    """Delete a speaker from the voice library."""
    lib = load_library()
    if name not in lib["speakers"]:
        return False
    del lib["speakers"][name]
    save_library(lib)
    return True


def rename_speaker(old_name: str, new_name: str) -> bool:
    """Rename a speaker (merges exemplars if the new name already exists).

    This is also the merge primitive: rename A → B when B exists folds A's
    exemplars into B and deletes A.
    """
    lib = load_library()
    if old_name not in lib["speakers"]:
        return False
    new_name = " ".join((new_name or "").strip().split())
    if not new_name:
        return False
    # Prefer an existing case-insensitive target so rename "leslie" → "Leslie"
    # lands on the canonical key when present.
    new_name = _canonical_speaker_name(lib, new_name)
    if new_name == old_name:
        return True
    if new_name in lib["speakers"] and new_name != old_name:
        # Merge exemplars into the existing target rather than erroring.
        target = lib["speakers"][new_name]
        target_samples = target.setdefault("samples", _samples_of(target))
        target_samples.extend(_samples_of(lib["speakers"][old_name]))
        if len(target_samples) > _MAX_SAMPLES:
            del target_samples[0:len(target_samples) - _MAX_SAMPLES]
        target["last_updated"] = _now()
        del lib["speakers"][old_name]
    else:
        lib["speakers"][new_name] = lib["speakers"].pop(old_name)
        lib["speakers"][new_name]["last_updated"] = _now()
    save_library(lib)
    return True


def merge_speakers(source_name: str, target_name: str) -> bool:
    """Merge `source_name` into `target_name` (exemplars + delete source).

    Requires both names to exist. Prefer this over rename when the UI intent is
    explicitly "merge these two people".
    """
    lib = load_library()
    if source_name not in lib["speakers"] or target_name not in lib["speakers"]:
        return False
    if source_name == target_name:
        return True
    return rename_speaker(source_name, target_name)


# ── CLI ─────────────────────────────────────────────────────────────────────

def _cli():
    """Command-line interface for voice library management."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Voice library management CLI")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="List all enrolled speakers (JSON)")

    enroll_p = sub.add_parser("enroll", help="Enroll a speaker from audio")
    enroll_p.add_argument("--name", required=True)
    enroll_p.add_argument("--audio", required=True)
    enroll_p.add_argument("--start", type=float, default=None)
    enroll_p.add_argument("--end", type=float, default=None)

    diar_p = sub.add_parser("enroll-diarized", help="Enroll a speaker's stored embedding from a diarized sidecar")
    diar_p.add_argument("--name", required=True)
    diar_p.add_argument("--json", required=True, help="Path to _diarized.json")
    diar_p.add_argument("--id", required=True, help="Speaker id in the sidecar")

    bulk_p = sub.add_parser("enroll-from-transcripts", help="Build the library from all tagged transcripts in a directory")
    bulk_p.add_argument("--dir", required=True, help="Directory of *_diarized.json files")

    delete_p = sub.add_parser("delete", help="Delete a speaker")
    delete_p.add_argument("--name", required=True)

    rename_p = sub.add_parser("rename", help="Rename a speaker (merges if --new already exists)")
    rename_p.add_argument("--old", required=True)
    rename_p.add_argument("--new", required=True)

    merge_p = sub.add_parser("merge", help="Merge one speaker into another (keep target name)")
    merge_p.add_argument("--from", dest="source", required=True, help="Speaker to absorb and delete")
    merge_p.add_argument("--into", dest="target", required=True, help="Speaker that keeps the name")

    calendar_p = sub.add_parser(
        "set-calendar-emails",
        help="Associate Microsoft 365 attendee email addresses with a speaker",
    )
    calendar_p.add_argument("--name", required=True)
    calendar_p.add_argument("--email", action="append", required=True)

    args = parser.parse_args()

    if args.command == "list":
        print(json.dumps(list_speakers(), indent=2, ensure_ascii=False))

    elif args.command == "enroll":
        try:
            result = enroll_speaker(name=args.name, audio_path=args.audio,
                                    segment_start=args.start, segment_end=args.end)
            print(json.dumps({"ok": True, "sample_count": len(result.get("samples", []))}))
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
            sys.exit(1)

    elif args.command == "enroll-diarized":
        try:
            result = enroll_from_diarized(args.name, args.json, args.id)
            print(json.dumps({"ok": True, "sample_count": len(result.get("samples", []))}))
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
            sys.exit(1)

    elif args.command == "enroll-from-transcripts":
        try:
            print(json.dumps({"ok": True, **enroll_from_transcripts(args.dir)}))
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
            sys.exit(1)

    elif args.command == "delete":
        ok = delete_speaker(args.name)
        print(json.dumps({"ok": ok}) if ok else json.dumps({"ok": False, "error": f"Speaker '{args.name}' not found"}))
        if not ok:
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

    elif args.command == "merge":
        try:
            ok = merge_speakers(args.source, args.target)
            if ok:
                print(json.dumps({"ok": True}))
            else:
                print(
                    json.dumps({
                        "ok": False,
                        "error": f"Cannot merge '{args.source}' into '{args.target}' (missing name?)",
                    }),
                    file=sys.stderr,
                )
                sys.exit(1)
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
            sys.exit(1)

    elif args.command == "set-calendar-emails":
        ok = set_calendar_emails(args.name, args.email)
        print(json.dumps({"ok": ok, "name": args.name, "calendar_emails": args.email}))
        if not ok:
            sys.exit(1)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    _cli()
