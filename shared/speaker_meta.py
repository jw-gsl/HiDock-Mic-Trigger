"""Speaker provenance + review-state helpers for the tagging verify loop.

The diarized sidecar (`<base>_diarized.json`) carries, alongside `speaker_names`:

    "speaker_meta":       { "<id>": {"source": ..., "confidence": ..., "verified": bool} }
    "speaker_embeddings": { "<id>": [float, ...] }        # L2-normalised TitaNet

`source` is one of:
    "auto"    — matched from the voice library (unverified until the user confirms)
    "user"    — a name the user typed / confirmed
    "unknown" — an acknowledged guest the user chose not to name
    "generic" — untouched "Speaker N"

This module owns the `rematch` operation: after the voice library grows, sweep a
meeting's still-generic speakers and try to auto-match them against the current
library — using the stored per-speaker embeddings when present, or re-deriving
them from the audio for legacy sidecars. It never overwrites a name the user has
confirmed.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np

_GENERIC_RE = re.compile(r"^Speaker \d+$")
_MATCH_THRESHOLD = 0.65


def is_generic_name(name: str | None) -> bool:
    """True for an untouched "Speaker N" label (or empty)."""
    if not name:
        return True
    return bool(_GENERIC_RE.match(name.strip()))


def infer_source(name: str | None) -> str:
    """Best-effort provenance for a sidecar with no speaker_meta (legacy).
    A generic label is "generic"; anything else is treated as an unverified
    "auto" match so the app surfaces it for verification."""
    return "generic" if is_generic_name(name) else "auto"


def ensure_speaker_meta(data: dict) -> dict:
    """Back-fill a `speaker_meta` block for a legacy sidecar that lacks one, so
    downstream code can rely on it. Does not mark anything verified."""
    names = data.get("speaker_names", {}) or {}
    meta = data.get("speaker_meta")
    if not isinstance(meta, dict):
        meta = {}
    for sid, name in names.items():
        if sid not in meta:
            meta[sid] = {
                "source": infer_source(name),
                "confidence": None,
                "verified": False,
            }
    data["speaker_meta"] = meta
    return meta


def resolve_name_collisions(speaker_names: dict, speaker_meta: dict) -> tuple[dict, dict]:
    """Ensure no enrolled name is auto-assigned to more than one speaker.

    Over-clustering can split one person into two speakers that BOTH match the
    same enrolled voice — leaving e.g. two "Natasha Fura" rows. When that
    happens, keep the single best speaker for that name (verified wins, then
    highest confidence) and revert the others to a generic "Speaker N" label so
    the user can review/merge them. Never demotes a user-verified name. Mutates
    both dicts in place and returns them."""
    from collections import defaultdict

    by_name: dict[str, list[str]] = defaultdict(list)
    for sid, name in speaker_names.items():
        by_name[name].append(sid)

    for name, sids in by_name.items():
        if len(sids) < 2 or is_generic_name(name):
            continue

        def rank(sid: str):
            m = speaker_meta.get(sid, {}) or {}
            return (1 if m.get("verified") else 0, m.get("confidence") or 0.0)

        # Highest-ranked keeps the name; demote the rest (unless user-verified).
        for sid in sorted(sids, key=rank, reverse=True)[1:]:
            m = speaker_meta.get(sid, {}) or {}
            if m.get("verified"):
                continue
            try:
                generic = f"Speaker {int(sid) + 1}"
            except (TypeError, ValueError):
                generic = "Speaker ?"
            speaker_names[sid] = generic
            speaker_meta[sid] = {"source": "generic", "confidence": None, "verified": False}

    return speaker_names, speaker_meta


def score_speakers(data: dict) -> dict:
    """Margin-based confidence that each speaker's assigned name is correct.

    A raw cosine similarity to the assigned voice is misleading: the auto-matcher
    picked that name *because* it was the closest, and cosine runs high even
    between different people, so a wrong match still shows a high number. Instead,
    for each speaker with a stored embedding we score it against EVERY enrolled
    voice and report how clearly the assigned name stands out from the rest:

        {speaker_id: {
            "assigned":      current name,
            "score":         cosine to the assigned voice (null if not enrolled),
            "best":          closest enrolled voice overall,
            "bestScore":     its cosine,
            "runnerUp":      best enrolled voice OTHER than the assigned name,
            "runnerUpScore": its cosine,
            "margin":        score - runnerUpScore  (how much the assigned name
                             beats the next-best voice; negative ⇒ another voice
                             matches better, i.e. the assignment is suspect),
        }}

    The margin — not the absolute score — is what tells a confident match from a
    coin-flip. Omitted for speakers with no embedding or an empty library.
    """
    from shared.voice_library_lite import cosine_similarity, load_library

    lib = load_library().get("speakers", {})
    names = data.get("speaker_names", {}) or {}
    embeddings = data.get("speaker_embeddings") or {}

    out: dict[str, dict] = {}
    for sid, name in names.items():
        emb = embeddings.get(sid)
        if emb is None:
            continue
        sims: list[tuple[str, float]] = []
        for lname, ldata in lib.items():
            stored = ldata.get("embedding")
            if not stored or len(stored) != len(emb):
                continue   # different embedding model/dim — not comparable
            sims.append((lname, round(float(cosine_similarity(emb, stored)), 4)))
        if not sims:
            continue
        sims.sort(key=lambda x: x[1], reverse=True)

        assigned_score = next((s for n, s in sims if n == name), None)
        best_other = next(((n, s) for n, s in sims if n != name), None)

        entry: dict = {
            "assigned": name,
            "score": assigned_score,
            "best": sims[0][0],
            "bestScore": sims[0][1],
        }
        if best_other is not None:
            entry["runnerUp"] = best_other[0]
            entry["runnerUpScore"] = best_other[1]
            if assigned_score is not None:
                entry["margin"] = round(assigned_score - best_other[1], 4)
        out[sid] = entry
    return out


def _collect_speaker_audio(audio: np.ndarray, segments: list, speaker_id: int,
                           sr: int = 16000, max_seconds: float = 10.0,
                           min_seconds: float = 1.0) -> np.ndarray:
    """Concatenate up to `max_seconds` of one speaker's audio, longest segments
    first — the same strategy the diarizer uses to build a stable embedding."""
    spans = [
        (float(s.get("start", 0.0)), float(s.get("end", 0.0)))
        for s in segments
        if s.get("speaker_id") == speaker_id
        and (float(s.get("end", 0.0)) - float(s.get("start", 0.0))) >= min_seconds
    ]
    if not spans:
        return np.zeros(0, dtype=np.float32)
    spans.sort(key=lambda p: p[1] - p[0], reverse=True)
    pieces: list[np.ndarray] = []
    collected = 0.0
    for ts, te in spans:
        if collected >= max_seconds:
            break
        a = max(0, int(ts * sr))
        b = min(len(audio), int(te * sr))
        if b <= a:
            continue
        pieces.append(audio[a:b])
        collected += (b - a) / sr
    if not pieces:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(pieces).astype(np.float32)


def _load_embed_session():
    """Load the TitaNet ONNX session, or None if unavailable."""
    try:
        from shared.models import ensure_speaker_embed
        import onnxruntime as ort

        model_path = ensure_speaker_embed()
        return ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    except Exception as e:  # noqa: BLE001 - best effort
        print(f"rematch: TitaNet unavailable ({e})", file=sys.stderr)
        return None


def rematch_diarized(data: dict, threshold: float = _MATCH_THRESHOLD,
                     audio_fallback: bool = True) -> dict:
    """Re-identify still-generic speakers against the current voice library.

    Mutates `data` in place — updates `speaker_names`, `speaker_meta`,
    `speaker_embeddings`, and each segment's `speaker` text. Only speakers whose
    name is still "Speaker N" and are not verified are eligible; a confirmed /
    user-named speaker is never touched.

    Args:
        data: A loaded `_diarized.json` dict.
        threshold: Minimum cosine similarity to accept a match.
        audio_fallback: When a speaker has no stored embedding (legacy sidecar),
            re-derive it from the audio. CPU-heavy; set False to skip.

    Returns:
        {"rematched": int, "matches": [{"id","name","confidence"}], "eligible": int}
    """
    from shared.voice_library_lite import identify_speaker

    names: dict = data.get("speaker_names", {}) or {}
    meta = ensure_speaker_meta(data)
    embeddings: dict = data.get("speaker_embeddings")
    if not isinstance(embeddings, dict):
        embeddings = {}

    # Which speakers can we try? Still-generic and not verified.
    eligible = [
        sid for sid, name in names.items()
        if is_generic_name(name) and not meta.get(sid, {}).get("verified", False)
    ]

    result = {"rematched": 0, "matches": [], "eligible": len(eligible)}
    if not eligible:
        return result

    # Lazily load audio + embed session only if we actually need to re-derive.
    audio = None
    session = None
    need_reembed = any(sid not in embeddings for sid in eligible)
    if need_reembed and audio_fallback:
        audio_path = data.get("audio_file", "")
        if audio_path and Path(audio_path).exists():
            try:
                from shared.audio_utils import load_audio
                audio = load_audio(audio_path, sr=16000)
                session = _load_embed_session()
            except Exception as e:  # noqa: BLE001
                print(f"rematch: audio load failed ({e})", file=sys.stderr)

    for sid in eligible:
        emb = embeddings.get(sid)
        if emb is None:
            # Legacy sidecar — re-derive from audio if we can.
            if audio is None or session is None:
                continue
            try:
                from shared.audio_utils import extract_embedding
                chunk = _collect_speaker_audio(audio, data.get("segments", []), int(sid))
                if chunk.size == 0:
                    continue
                raw = extract_embedding(chunk, sr=16000, onnx_session=session)
                norm = float(np.linalg.norm(raw))
                if norm > 1e-10:
                    raw = raw / norm
                emb = [float(x) for x in raw]
                embeddings[sid] = emb  # cache for next time
            except Exception as e:  # noqa: BLE001
                print(f"rematch: re-embed failed for speaker {sid}: {e}", file=sys.stderr)
                continue

        matched, confidence = identify_speaker(np.asarray(emb, dtype=np.float32), threshold=threshold)
        if matched:
            names[sid] = matched
            meta[sid] = {"source": "auto", "confidence": float(confidence), "verified": False}
            result["rematched"] += 1
            result["matches"].append({"id": sid, "name": matched, "confidence": float(confidence)})
            print(f"  Re-matched speaker {sid} → {matched} ({confidence:.0%})", file=sys.stderr)

    # A rematch can also produce a collision (two generics both matching the
    # same enrolled voice) — dedupe so we never write duplicate names.
    resolve_name_collisions(names, meta)

    # Reflect any new names into the segment text so a regenerated .md is correct.
    for seg in data.get("segments", []):
        sid = str(seg.get("speaker_id", ""))
        if sid in names:
            seg["speaker"] = names[sid]

    data["speaker_names"] = names
    data["speaker_meta"] = meta
    data["speaker_embeddings"] = embeddings
    return result
