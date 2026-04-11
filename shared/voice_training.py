"""Voice training — collect speaker samples across meetings for review and enrollment.

Scans all diarized transcripts, extracts per-speaker embeddings, and clusters
them across meetings to find recurring voices. Produces a review list where
users can confirm identities, building up the voice library for auto-matching.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.spatial.distance import cosine as cosine_distance

from shared.audio_utils import extract_embedding, load_audio
from shared.voice_library_lite import (
    identify_speaker,
    list_speakers as list_enrolled,
)


@dataclass
class VoiceSample:
    """A single speaker sample from a meeting."""
    meeting_file: str       # audio file path
    meeting_name: str       # human-readable meeting name
    speaker_id: int         # speaker ID within this meeting
    speaker_label: str      # "Speaker 1" or tagged name
    start: float            # segment start time
    end: float              # segment end time
    duration: float         # total talk time for this speaker in this meeting
    text_preview: str       # first ~100 chars of what they said
    embedding: list[float]  # speaker embedding vector


@dataclass
class VoiceCluster:
    """A group of samples believed to be the same person across meetings."""
    cluster_id: int
    suggested_name: str | None = None  # from voice library match
    confidence: float = 0.0
    samples: list[VoiceSample] = field(default_factory=list)
    centroid: list[float] = field(default_factory=list)


def scan_meetings(transcripts_dir: Path | None = None) -> list[VoiceSample]:
    """Scan all diarized transcripts and extract per-speaker samples.

    Returns a list of VoiceSample objects, one per speaker per meeting.
    """
    if transcripts_dir is None:
        transcripts_dir = Path.home() / "HiDock" / "Raw Transcripts"

    samples = []

    for json_path in sorted(transcripts_dir.glob("*_diarized.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        audio_file = data.get("audio_file", "")
        if not audio_file or not Path(audio_file).exists():
            continue

        segments = data.get("segments", [])
        speaker_names = data.get("speaker_names", {})
        if not segments:
            continue

        meeting_name = json_path.stem.replace("_diarized", "")

        # Collect stats per speaker
        speaker_stats: dict[int, dict] = {}
        for seg in segments:
            sid = seg.get("speaker_id", 0)
            dur = seg.get("end", 0) - seg.get("start", 0)
            if sid not in speaker_stats:
                speaker_stats[sid] = {
                    "total_duration": 0.0,
                    "first_start": seg.get("start", 0),
                    "first_end": min(seg.get("end", 0), seg.get("start", 0) + 15),  # cap at 15s for sample
                    "text_preview": seg.get("text", "")[:100],
                }
            speaker_stats[sid]["total_duration"] += dur

        # Extract embedding for each speaker using their first segment
        try:
            audio = load_audio(audio_file, sr=16000)
        except Exception:
            continue

        from shared.voice_library_lite import _get_speaker_embed_session

        session = _get_speaker_embed_session()

        for sid, stats in speaker_stats.items():
            if stats["total_duration"] < 5:
                continue  # Skip speakers with <5s of audio

            # Extract embedding from their first segment
            sr = 16000
            s = int(stats["first_start"] * sr)
            e = int(stats["first_end"] * sr)
            chunk = audio[max(0, s):min(len(audio), e)]

            if len(chunk) < sr:  # < 1 second
                continue

            try:
                emb = extract_embedding(chunk, sr=sr, onnx_session=session)
            except Exception:
                continue

            label = speaker_names.get(str(sid), f"Speaker {sid + 1}")

            samples.append(VoiceSample(
                meeting_file=audio_file,
                meeting_name=meeting_name,
                speaker_id=sid,
                speaker_label=label,
                start=stats["first_start"],
                end=stats["first_end"],
                duration=stats["total_duration"],
                text_preview=stats["text_preview"],
                embedding=emb.tolist(),
            ))

    return samples


def cluster_across_meetings(
    samples: list[VoiceSample],
    distance_threshold: float = 0.4,
) -> list[VoiceCluster]:
    """Cluster samples across meetings to find recurring voices.

    Uses agglomerative clustering on embeddings, then matches
    clusters against the enrolled voice library.
    """
    if not samples:
        return []

    embeddings = np.array([s.embedding for s in samples], dtype=np.float32)
    n = len(embeddings)

    if n == 1:
        cluster = VoiceCluster(cluster_id=0, samples=[samples[0]])
        cluster.centroid = samples[0].embedding
        _match_to_library(cluster)
        return [cluster]

    # Cluster
    from scipy.cluster.hierarchy import fcluster, linkage

    Z = linkage(embeddings, method="average", metric="cosine")
    labels = fcluster(Z, t=distance_threshold, criterion="distance")
    labels = [int(l) - 1 for l in labels]

    # Build clusters
    clusters_dict: dict[int, VoiceCluster] = {}
    for sample, label in zip(samples, labels):
        if label not in clusters_dict:
            clusters_dict[label] = VoiceCluster(cluster_id=label, samples=[])
        clusters_dict[label].samples.append(sample)

    # Compute centroids and match to library
    clusters = []
    for cluster in sorted(clusters_dict.values(), key=lambda c: -sum(s.duration for s in c.samples)):
        mask = [i for i, l in enumerate(labels) if l == cluster.cluster_id]
        centroid = embeddings[mask].mean(axis=0)
        norm = np.linalg.norm(centroid)
        if norm > 1e-10:
            centroid = centroid / norm
        cluster.centroid = centroid.tolist()
        _match_to_library(cluster)
        clusters.append(cluster)

    # Renumber
    for i, c in enumerate(clusters):
        c.cluster_id = i

    return clusters


def _match_to_library(cluster: VoiceCluster) -> None:
    """Try to match a cluster's centroid against enrolled speakers."""
    if not cluster.centroid:
        return

    name, confidence = identify_speaker(cluster.centroid, threshold=0.5)
    if name:
        cluster.suggested_name = name
        cluster.confidence = confidence
    else:
        # Use the most common label from samples
        from collections import Counter
        labels = [s.speaker_label for s in cluster.samples if not s.speaker_label.startswith("Speaker ")]
        if labels:
            cluster.suggested_name = Counter(labels).most_common(1)[0][0]
            cluster.confidence = 0.3  # low confidence — from transcript tag, not voice match


def export_for_ui(clusters: list[VoiceCluster]) -> list[dict]:
    """Export clusters as JSON-serializable dicts for the app UI."""
    result = []
    for c in clusters:
        total_duration = sum(s.duration for s in c.samples)
        result.append({
            "cluster_id": c.cluster_id,
            "suggested_name": c.suggested_name,
            "confidence": round(c.confidence, 2),
            "total_talk_time": round(total_duration),
            "meeting_count": len(set(s.meeting_name for s in c.samples)),
            "sample_count": len(c.samples),
            "samples": [
                {
                    "meeting_name": s.meeting_name,
                    "meeting_file": s.meeting_file,
                    "speaker_label": s.speaker_label,
                    "start": round(s.start, 1),
                    "end": round(s.end, 1),
                    "duration": round(s.duration),
                    "text_preview": s.text_preview,
                }
                for s in c.samples
            ],
        })
    return result


if __name__ == "__main__":
    import sys

    print("Scanning meetings...", file=sys.stderr)
    samples = scan_meetings()
    print(f"Found {len(samples)} speaker samples across meetings", file=sys.stderr)

    print("Clustering across meetings...", file=sys.stderr)
    clusters = cluster_across_meetings(samples)
    print(f"Found {len(clusters)} voice clusters", file=sys.stderr)

    data = export_for_ui(clusters)
    print(json.dumps(data, indent=2))
