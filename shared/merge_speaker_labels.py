"""Carry confirmed child speaker labels into a re-diarized merged meeting.

The merged-audio diarizer starts with fresh, local speaker IDs. That is useful
for cross-piece continuity, but it otherwise discards the human verification
already attached to the child transcripts. This module maps the fresh clusters
back to confirmed child names by timestamp overlap, then collapses clusters
that are confirmed as the same person.

Only explicit ``verified`` labels are propagated. Unverified auto-matches and
generic ``Speaker N`` labels remain available for review in the merged meeting.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Iterable


_GENERIC_RE = re.compile(r"^Speaker \d+$")
_MIN_OVERLAP_SECONDS = 0.75


def _is_generic(name: str | None) -> bool:
    return not name or bool(_GENERIC_RE.fullmatch(str(name).strip()))


def _overlap(left_start: float, left_end: float, right_start: float, right_end: float) -> float:
    return max(0.0, min(left_end, right_end) - max(left_start, right_start))


def _normalised_average(vectors: list[list[float]]) -> list[float] | None:
    if not vectors:
        return None
    dimension = len(vectors[0])
    if not dimension or any(len(vector) != dimension for vector in vectors):
        return None
    average = [sum(vector[i] for vector in vectors) / len(vectors) for i in range(dimension)]
    norm = math.sqrt(sum(value * value for value in average))
    if norm <= 1e-10:
        return None
    return [value / norm for value in average]


def _confirmed_anchors(
    piece_paths: Iterable[str | Path],
    durations: Iterable[float],
    transcripts_dir: str | Path,
) -> list[tuple[float, float, str, dict]]:
    """Load confirmed child speaker intervals in merged-audio time."""
    anchors: list[tuple[float, float, str, dict]] = []
    offset = 0.0
    directory = Path(transcripts_dir)
    for piece_path, duration in zip(piece_paths, durations):
        piece = Path(piece_path)
        sidecar = directory / f"{piece.stem}_diarized.json"
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            offset += float(duration)
            continue

        names = data.get("speaker_names") or {}
        meta = data.get("speaker_meta") or {}
        for segment in data.get("segments", []):
            try:
                start = float(segment.get("start", 0.0)) + offset
                end = float(segment.get("end", 0.0)) + offset
            except (TypeError, ValueError):
                continue
            if end <= start:
                continue
            speaker_id = str(segment.get("speaker_id", ""))
            name = names.get(speaker_id) or segment.get("speaker")
            speaker_meta = meta.get(speaker_id) or {}
            # A child label is safe to carry only after explicit confirmation.
            if _is_generic(name) or speaker_meta.get("verified") is not True:
                continue
            anchors.append((start, end, str(name), dict(speaker_meta)))
        offset += float(duration)
    return anchors


def preserve_existing_speaker_labels(
    diarized_result: dict,
    previous_result: dict,
) -> dict:
    """Carry trustworthy labels from an older sidecar onto new clusters.

    ``rediarize`` creates fresh cluster IDs. Without this mapping, a perfectly
    valid user label is replaced by ``Speaker N`` every time diarization is
    rerun. Explicitly verified labels are preserved, as are non-generic names
    from legacy sidecars that predate ``speaker_meta``. Unverified auto-match
    labels are deliberately not anchors: they should be re-evaluated.
    """
    previous_names = previous_result.get("speaker_names") or {}
    previous_meta = previous_result.get("speaker_meta") or {}
    anchors: list[tuple[float, float, str, dict]] = []
    for segment in previous_result.get("segments", []) or []:
        try:
            start = float(segment.get("start", 0.0))
            end = float(segment.get("end", 0.0))
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        speaker_id = str(segment.get("speaker_id", ""))
        name = previous_names.get(speaker_id) or segment.get("speaker")
        if _is_generic(name):
            continue
        meta = dict(previous_meta.get(speaker_id) or {})
        if meta.get("source") == "auto" and meta.get("verified") is not True:
            continue
        if not meta:
            meta = {"source": "legacy", "confidence": None, "verified": False}
        anchors.append((start, end, str(name), meta))

    segments = diarized_result.get("segments") or []
    if not anchors or not segments:
        return diarized_result

    original_names = diarized_result.get("speaker_names") or {}
    original_meta = diarized_result.get("speaker_meta") or {}
    original_embeddings = diarized_result.get("speaker_embeddings") or {}
    cluster_order: list[str] = []
    cluster_scores: dict[str, dict[str, float]] = {}
    cluster_meta: dict[str, dict[str, dict]] = {}
    for segment in segments:
        cluster = str(segment.get("speaker_id", "0"))
        if cluster not in cluster_order:
            cluster_order.append(cluster)
        try:
            start = float(segment.get("start", 0.0))
            end = float(segment.get("end", 0.0))
        except (TypeError, ValueError):
            continue
        for anchor_start, anchor_end, name, meta in anchors:
            overlap = _overlap(start, end, anchor_start, anchor_end)
            if overlap <= 0:
                continue
            scores = cluster_scores.setdefault(cluster, {})
            scores[name] = scores.get(name, 0.0) + overlap
            cluster_meta.setdefault(cluster, {})[name] = meta

    cluster_to_name: dict[str, str] = {}
    cluster_to_meta: dict[str, dict] = {}
    for cluster in cluster_order:
        scores = cluster_scores.get(cluster, {})
        if not scores:
            continue
        name, overlap = max(scores.items(), key=lambda item: item[1])
        if overlap < _MIN_OVERLAP_SECONDS:
            continue
        cluster_to_name[cluster] = name
        cluster_to_meta[cluster] = dict(cluster_meta[cluster][name])

    if not cluster_to_name:
        return diarized_result

    next_id = 0
    name_to_id: dict[str, int] = {}
    cluster_to_id: dict[str, int] = {}
    new_names: dict[str, str] = {}
    new_meta: dict[str, dict] = {}
    embedding_buckets: dict[int, list[list[float]]] = {}
    lineage: dict[str, dict] = {}
    for cluster in cluster_order:
        name = cluster_to_name.get(cluster)
        if name is not None:
            new_id = name_to_id.setdefault(name, next_id)
            if new_id == next_id:
                next_id += 1
            new_names[str(new_id)] = name
            new_meta[str(new_id)] = cluster_to_meta[cluster]
        else:
            new_id = next_id
            next_id += 1
            new_names[str(new_id)] = str(original_names.get(cluster, f"Speaker {new_id + 1}"))
            new_meta[str(new_id)] = dict(original_meta.get(cluster) or {
                "source": "generic" if _is_generic(new_names[str(new_id)]) else "auto",
                "confidence": None,
                "verified": False,
            })
        cluster_to_id[cluster] = new_id
        entry = lineage.setdefault(str(new_id), {
            "source_cluster_ids": [],
            "surviving_name": new_names[str(new_id)],
        })
        if cluster not in entry["source_cluster_ids"]:
            entry["source_cluster_ids"].append(cluster)
        embedding = original_embeddings.get(cluster)
        if isinstance(embedding, list):
            embedding_buckets.setdefault(new_id, []).append(embedding)

    remapped_segments = []
    for segment in segments:
        updated = dict(segment)
        old_cluster = str(segment.get("speaker_id", "0"))
        new_id = cluster_to_id[old_cluster]
        updated["source_speaker_id"] = old_cluster
        updated["speaker_id"] = new_id
        updated["speaker"] = new_names[str(new_id)]
        remapped_segments.append(updated)

    remapped_embeddings = {}
    for new_id, vectors in embedding_buckets.items():
        average = _normalised_average(vectors)
        if average is not None:
            remapped_embeddings[str(new_id)] = average

    diarized_result["segments"] = remapped_segments
    diarized_result["speaker_names"] = new_names
    diarized_result["speaker_meta"] = new_meta
    diarized_result["speaker_embeddings"] = remapped_embeddings
    diarized_result["speaker_lineage"] = lineage
    diarized_result["preserved_speaker_labels"] = sorted(set(cluster_to_name.values()))
    return diarized_result


def apply_confirmed_speaker_labels(
    diarized_result: dict,
    piece_paths: Iterable[str | Path],
    durations: Iterable[float],
    transcripts_dir: str | Path,
) -> dict:
    """Apply verified child names to a freshly re-diarized merged result.

    The returned dictionary keeps the diarizer's structure and unknown
    clusters. Confirmed names are selected by the greatest timestamp overlap;
    two fresh clusters mapped to the same confirmed person are collapsed into
    one speaker ID so the merged transcript does not create duplicate people.
    """
    anchors = _confirmed_anchors(piece_paths, durations, transcripts_dir)
    segments = diarized_result.get("segments") or []
    if not anchors or not segments:
        return diarized_result

    original_names = diarized_result.get("speaker_names") or {}
    original_meta = diarized_result.get("speaker_meta") or {}
    original_embeddings = diarized_result.get("speaker_embeddings") or {}

    cluster_order: list[str] = []
    cluster_scores: dict[str, dict[str, float]] = {}
    cluster_meta: dict[str, dict[str, dict]] = {}
    cluster_durations: dict[str, float] = {}
    for segment in segments:
        cluster = str(segment.get("speaker_id", "0"))
        if cluster not in cluster_order:
            cluster_order.append(cluster)
        try:
            start = float(segment.get("start", 0.0))
            end = float(segment.get("end", 0.0))
        except (TypeError, ValueError):
            continue
        cluster_durations[cluster] = cluster_durations.get(cluster, 0.0) + max(0.0, end - start)
        for anchor_start, anchor_end, name, meta in anchors:
            overlap = _overlap(start, end, anchor_start, anchor_end)
            if overlap <= 0:
                continue
            cluster_scores.setdefault(cluster, {})[name] = (
                cluster_scores.setdefault(cluster, {}).get(name, 0.0) + overlap
            )
            cluster_meta.setdefault(cluster, {})[name] = meta

    cluster_to_name: dict[str, str] = {}
    cluster_to_meta: dict[str, dict] = {}
    for cluster in cluster_order:
        scores = cluster_scores.get(cluster, {})
        if not scores:
            continue
        name, overlap = max(scores.items(), key=lambda item: item[1])
        # Do not let a tiny accidental boundary overlap rename an entire fresh
        # cluster. The absolute threshold also makes short meetings safe.
        if overlap < _MIN_OVERLAP_SECONDS:
            continue
        cluster_to_name[cluster] = name
        cluster_to_meta[cluster] = dict(cluster_meta[cluster][name])

    if not cluster_to_name:
        return diarized_result

    # Assign stable IDs in first-appearance order, sharing one ID for a name
    # that was confirmed in several child pieces.
    next_id = 0
    name_to_id: dict[str, int] = {}
    cluster_to_id: dict[str, int] = {}
    new_names: dict[str, str] = {}
    new_meta: dict[str, dict] = {}
    embedding_buckets: dict[int, list[list[float]]] = {}
    speaker_lineage: dict[str, dict] = {}
    for cluster in cluster_order:
        name = cluster_to_name.get(cluster)
        if name is not None:
            new_id = name_to_id.setdefault(name, next_id)
            if new_id == next_id:
                next_id += 1
            new_names[str(new_id)] = name
            new_meta[str(new_id)] = cluster_to_meta[cluster]
        else:
            new_id = next_id
            next_id += 1
            new_names[str(new_id)] = str(original_names.get(cluster, f"Speaker {new_id + 1}"))
            new_meta[str(new_id)] = dict(original_meta.get(cluster) or {
                "source": "generic" if _is_generic(new_names[str(new_id)]) else "auto",
                "confidence": None,
                "verified": False,
            })
        cluster_to_id[cluster] = new_id
        lineage = speaker_lineage.setdefault(str(new_id), {
            "source_cluster_ids": [],
            "surviving_name": new_names[str(new_id)],
        })
        if cluster not in lineage["source_cluster_ids"]:
            lineage["source_cluster_ids"].append(cluster)
        embedding = original_embeddings.get(cluster)
        if isinstance(embedding, list):
            embedding_buckets.setdefault(new_id, []).append(embedding)

    remapped_segments = []
    for segment in segments:
        updated = dict(segment)
        old_cluster = str(segment.get("speaker_id", "0"))
        new_id = cluster_to_id[old_cluster]
        # Keep the fresh diarizer cluster id for later split/merge evaluation;
        # speaker_id is intentionally the survivor id used by the transcript.
        updated["source_speaker_id"] = old_cluster
        updated["speaker_id"] = new_id
        updated["speaker"] = new_names[str(new_id)]
        remapped_segments.append(updated)

    remapped_embeddings = {}
    for new_id, vectors in embedding_buckets.items():
        average = _normalised_average(vectors)
        if average is not None:
            remapped_embeddings[str(new_id)] = average

    diarized_result["segments"] = remapped_segments
    diarized_result["speaker_names"] = new_names
    diarized_result["speaker_meta"] = new_meta
    diarized_result["speaker_embeddings"] = remapped_embeddings
    diarized_result["confirmed_from_children"] = sorted(set(cluster_to_name.values()))
    diarized_result["speaker_lineage"] = speaker_lineage
    return diarized_result
