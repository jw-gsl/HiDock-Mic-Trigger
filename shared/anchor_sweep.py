"""Reclaim a confirmed speaker's speech from the clusters that swallowed it.

`recluster_with_anchors` reassigns *unnamed* segments to the nearest confirmed
centroid. That is the wrong shape for the failure this addresses: when a reviewer
has confirmed every speaker, there are no unnamed segments left, yet a quiet
participant's speech is still sitting under someone else's name. Measured on
Rec79 Part 2 the tool reported `reassigned: 0, kept: 286` — correct, and useless.

The gap is mislabelled rather than unlabelled speech. A diarizer that never gave
a participant their own cluster cannot be fixed by clustering harder: on that
meeting Jeevan Dulai spoke 128 s of 56 minutes (3.8%) and was the *most*
acoustically distinct voice present — ≤0.335 cosine to anyone else, where the two
speakers that did separate sat at 0.449. Splitting looks for two balanced voices
inside one label and never finds a 128 s minority inside ~1,000 s of someone else.

A confirmed segment removes the discovery problem by saying where the voice is.
This sweeps every other segment and reclaims the ones that match a confirmed
centroid clearly — a similarity floor *and* a margin over the segment's current
speaker, so speech moves on positive evidence rather than on doubt about its
current owner. Confirmed segments are never reassigned; they are the ground truth
the sweep is built from.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

# Calibrated against the Rec79 Part 2 measurements (confirmed speaker self-match
# 0.888, cross-speaker maximum 0.335): a 0.55 floor clears the between-person
# range on this data without being loose enough to claim a neighbouring voice.
MIN_SIMILARITY = 0.55
MIN_MARGIN = 0.10
MIN_SEGMENT_SECONDS = 1.5


def confirmed_segments(data: dict) -> dict[str, list[dict]]:
    """Confirmed speaker name -> their segments, from a diarized sidecar."""
    names = data.get("speaker_names") or {}
    meta = data.get("speaker_meta") or {}
    grouped: dict[str, list[dict]] = {}
    for segment in data.get("segments") or []:
        sid = str(segment.get("speaker_id", ""))
        if (meta.get(sid) or {}).get("verified") is not True:
            continue
        name = names.get(sid)
        if name:
            grouped.setdefault(str(name), []).append(segment)
    return grouped


def _span_embedding(audio, spans, weights, session):
    from shared.audio_utils import extract_embedding

    vectors, used = [], []
    for (start, end), weight in zip(spans, weights):
        if end - start < MIN_SEGMENT_SECONDS:
            continue
        try:
            emb = extract_embedding(
                audio[int(start * 16000):int(end * 16000)], sr=16000, onnx_session=session
            )
        except Exception:
            continue
        norm = float(np.linalg.norm(emb))
        if norm <= 1e-10:
            continue
        vectors.append(emb / norm)
        used.append(max(float(weight), 1e-6))
    if not vectors:
        return None
    pooled = np.average(
        np.asarray(vectors, dtype=np.float32), axis=0,
        weights=np.asarray(used, dtype=np.float32),
    )
    norm = float(np.linalg.norm(pooled))
    return None if norm <= 1e-10 else pooled / norm


def plan_sweep(
    data: dict,
    audio,
    session,
    *,
    targets: list[str] | None = None,
    min_similarity: float = MIN_SIMILARITY,
    min_margin: float = MIN_MARGIN,
) -> dict:
    """Which segments would move, and why. Pure planning — nothing is written.

    `targets` limits the sweep to specific confirmed names; without it every
    confirmed speaker can reclaim speech, which is the general repair. Returning a
    plan rather than mutating means the caller (or a human) can inspect the
    proposed moves before any transcript changes.
    """
    grouped = confirmed_segments(data)
    if not grouped:
        return {"error": "no confirmed speakers to anchor on", "moves": []}
    wanted = set(targets) if targets else set(grouped)

    centroids: dict[str, np.ndarray] = {}
    anchor_seconds: dict[str, float] = {}
    for name, segments in grouped.items():
        spans = [(float(s["start"]), float(s["end"])) for s in segments]
        weights = [end - start for start, end in spans]
        pooled = _span_embedding(audio, spans, weights, session)
        if pooled is not None:
            centroids[name] = pooled
            anchor_seconds[name] = sum(weights)
    if not centroids:
        return {"error": "no usable anchor embeddings", "moves": []}

    names_by_id = data.get("speaker_names") or {}
    moves: list[dict] = []
    for index, segment in enumerate(data.get("segments") or []):
        start, end = float(segment.get("start", 0.0)), float(segment.get("end", 0.0))
        if end - start < MIN_SEGMENT_SECONDS:
            continue
        current = str(names_by_id.get(str(segment.get("speaker_id", "")), ""))
        embedding = _span_embedding(audio, [(start, end)], [end - start], session)
        if embedding is None:
            continue
        scored = sorted(
            ((float(np.dot(embedding, vector)), name) for name, vector in centroids.items()),
            reverse=True,
        )
        best_score, best_name = scored[0]
        if best_name == current or best_name not in wanted:
            continue
        if best_score < min_similarity:
            continue
        own = centroids.get(current)
        own_score = float(np.dot(embedding, own)) if own is not None else -1.0
        if best_score - own_score < min_margin:
            continue
        moves.append({
            "index": index,
            "start": round(start, 2),
            "end": round(end, 2),
            "from": current,
            "to": best_name,
            "score": round(best_score, 3),
            "current_score": round(own_score, 3),
            "text": (segment.get("text") or "")[:80],
        })
    return {
        "anchors": {name: round(seconds, 1) for name, seconds in anchor_seconds.items()},
        "targets": sorted(wanted),
        "moves": moves,
        "considered": len(data.get("segments") or []),
    }


def apply_sweep(data: dict, plan: dict) -> int:
    """Apply a plan in place. Returns the number of segments moved.

    Reassigns only the `speaker_id`/`speaker` of planned segments; it never
    invents a speaker, so a target must already exist in `speaker_names`.
    """
    names = data.get("speaker_names") or {}
    id_for = {str(name): sid for sid, name in names.items()}
    moved = 0
    for move in plan.get("moves") or []:
        sid = id_for.get(move["to"])
        if sid is None:
            continue
        segment = data["segments"][move["index"]]
        segment["source_speaker_id"] = str(segment.get("speaker_id"))
        segment["speaker_id"] = int(sid) if str(sid).isdigit() else sid
        segment["speaker"] = move["to"]
        moved += 1
    return moved


def load_audio_and_session(data: dict):
    """(audio, embedding session) for a sidecar, or (None, None)."""
    from shared.audio_utils import load_audio
    from shared.diarize_sortformer import _CrossWindowLinker

    audio_path = Path(data.get("audio_file") or "")
    if not audio_path.exists():
        return None, None
    audio = load_audio(audio_path, sr=16000)
    return audio, _CrossWindowLinker(audio)._session_or_none()
