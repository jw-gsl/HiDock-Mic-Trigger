"""Voice training — collect speaker samples across meetings for review and enrollment.

Scans diarized transcripts, extracts per-speaker embeddings, clusters across
meetings to find recurring voices. Supports:
- Smart sample selection (picks clearest, longest segments)
- Multiple clips per speaker for verification
- Persistent review state (confirmed/unconfirmed)
- Auto-matching against enrolled voice library
- Per-sample reassignment data for the UI
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from shared.audio_utils import extract_embedding, load_audio
from shared.voice_library_lite import (
    identify_speaker,
    list_speakers as list_enrolled,
)

REVIEW_STATE_PATH = Path.home() / "HiDock" / "voice_training_state.json"


@dataclass
class VoiceSample:
    """A single speaker clip from a meeting."""
    meeting_file: str
    meeting_name: str
    speaker_id: int
    speaker_label: str
    start: float
    end: float
    duration: float         # this clip's duration
    total_talk_time: float  # speaker's total talk time in this meeting
    text_preview: str
    embedding: list[float]
    quality_score: float = 0.0  # higher = cleaner sample


@dataclass
class VoiceCluster:
    """A group of samples believed to be the same person."""
    cluster_id: int
    suggested_name: str | None = None
    confidence: float = 0.0
    confirmed: bool = False
    samples: list[VoiceSample] = field(default_factory=list)
    centroid: list[float] = field(default_factory=list)


def _pick_best_samples(segments: list[dict], max_samples: int = 3) -> list[dict]:
    """Pick the best audio samples for a speaker — longest uninterrupted segments.

    Prefers segments that are:
    - 5-15 seconds long (ideal for embedding)
    - Not too short (noisy) or too long (mixed speakers)
    - Spread across the meeting (not all from the same minute)
    """
    scored = []
    for seg in segments:
        dur = seg.get("end", 0) - seg.get("start", 0)
        if dur < 2:
            continue
        # Score: prefer 5-15s, penalise very short or very long
        if 5 <= dur <= 15:
            score = 1.0
        elif 3 <= dur < 5:
            score = 0.7
        elif 15 < dur <= 30:
            score = 0.8
        elif dur < 3:
            score = 0.3
        else:
            score = 0.5
        # Boost segments with more words (more speech content)
        words = len(seg.get("text", "").split())
        score += min(words / 30, 0.3)
        scored.append((score, seg))

    scored.sort(key=lambda x: -x[0])

    # Pick top samples, spread across the meeting
    selected = []
    used_times = []
    for score, seg in scored:
        start = seg.get("start", 0)
        # Skip if too close to an already selected sample
        if any(abs(start - t) < 60 for t in used_times):
            continue
        selected.append(seg)
        used_times.append(start)
        if len(selected) >= max_samples:
            break

    # Fill remaining slots if we couldn't spread enough
    if len(selected) < max_samples:
        for score, seg in scored:
            if seg not in selected:
                selected.append(seg)
                if len(selected) >= max_samples:
                    break

    return selected


def scan_meetings(transcripts_dir: Path | None = None) -> list[VoiceSample]:
    """Scan all diarized transcripts and extract per-speaker samples."""
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

        # Group segments by speaker
        by_speaker: dict[int, list[dict]] = {}
        for seg in segments:
            sid = seg.get("speaker_id", 0)
            by_speaker.setdefault(sid, []).append(seg)

        # Load audio once per meeting
        try:
            audio = load_audio(audio_file, sr=16000)
        except Exception:
            continue

        from shared.voice_library_lite import _get_speaker_embed_session
        session = _get_speaker_embed_session()

        for sid, speaker_segs in by_speaker.items():
            total_talk = sum(s.get("end", 0) - s.get("start", 0) for s in speaker_segs)
            if total_talk < 5:
                continue

            label = speaker_names.get(str(sid), f"Speaker {sid + 1}")

            # Pick best samples for this speaker
            best = _pick_best_samples(speaker_segs, max_samples=3)

            for seg in best:
                start = seg.get("start", 0)
                end = min(seg.get("end", 0), start + 15)  # cap clip at 15s
                sr = 16000
                s_idx = int(start * sr)
                e_idx = int(end * sr)
                chunk = audio[max(0, s_idx):min(len(audio), e_idx)]

                if len(chunk) < sr:
                    continue

                try:
                    emb = extract_embedding(chunk, sr=sr, onnx_session=session)
                except Exception:
                    continue

                clip_dur = (end - start)
                quality = clip_dur / 15.0  # normalise to 0-1

                samples.append(VoiceSample(
                    meeting_file=audio_file,
                    meeting_name=meeting_name,
                    speaker_id=sid,
                    speaker_label=label,
                    start=start,
                    end=end,
                    duration=clip_dur,
                    total_talk_time=total_talk,
                    text_preview=seg.get("text", "")[:120],
                    embedding=emb.tolist(),
                    quality_score=quality,
                ))

    return samples


def cluster_across_meetings(
    samples: list[VoiceSample],
    distance_threshold: float = 0.4,
) -> list[VoiceCluster]:
    """Cluster samples across meetings to find recurring voices."""
    if not samples:
        return []

    embeddings = np.array([s.embedding for s in samples], dtype=np.float32)
    n = len(embeddings)

    if n == 1:
        cluster = VoiceCluster(cluster_id=0, samples=[samples[0]])
        cluster.centroid = samples[0].embedding
        _match_to_library(cluster)
        return [cluster]

    from scipy.cluster.hierarchy import fcluster, linkage

    Z = linkage(embeddings, method="average", metric="cosine")
    labels = fcluster(Z, t=distance_threshold, criterion="distance")
    labels = [int(l) - 1 for l in labels]

    clusters_dict: dict[int, VoiceCluster] = {}
    for sample, label in zip(samples, labels):
        if label not in clusters_dict:
            clusters_dict[label] = VoiceCluster(cluster_id=label, samples=[])
        clusters_dict[label].samples.append(sample)

    clusters = []
    for cluster in sorted(clusters_dict.values(), key=lambda c: -sum(s.total_talk_time for s in c.samples)):
        mask = [i for i, l in enumerate(labels) if l == cluster.cluster_id]
        centroid = embeddings[mask].mean(axis=0)
        norm = np.linalg.norm(centroid)
        if norm > 1e-10:
            centroid = centroid / norm
        cluster.centroid = centroid.tolist()
        _match_to_library(cluster)
        clusters.append(cluster)

    # Load persisted review state
    state = _load_review_state()
    for cluster in clusters:
        if cluster.suggested_name and cluster.suggested_name in state.get("confirmed", {}):
            cluster.confirmed = True

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
        from collections import Counter
        labels = [s.speaker_label for s in cluster.samples if not s.speaker_label.startswith("Speaker ")]
        if labels:
            cluster.suggested_name = Counter(labels).most_common(1)[0][0]
            cluster.confidence = 0.3


def _load_review_state() -> dict:
    """Load persisted review state."""
    if REVIEW_STATE_PATH.exists():
        try:
            return json.loads(REVIEW_STATE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"confirmed": {}, "reassignments": {}}


def save_review_state(state: dict) -> None:
    """Save review state to disk."""
    REVIEW_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    REVIEW_STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def confirm_speaker(name: str) -> None:
    """Mark a speaker as confirmed in the review state."""
    state = _load_review_state()
    state["confirmed"][name] = True
    save_review_state(state)


def export_for_ui(clusters: list[VoiceCluster]) -> list[dict]:
    """Export clusters as JSON for the app UI."""
    enrolled = {s["name"] for s in list_enrolled()}

    result = []
    for c in clusters:
        meetings = sorted(set(s.meeting_name for s in c.samples))
        total_talk = sum(s.total_talk_time for s in c.samples)
        result.append({
            "cluster_id": c.cluster_id,
            "suggested_name": c.suggested_name,
            "confidence": round(c.confidence, 2),
            "confirmed": c.confirmed,
            "enrolled": c.suggested_name in enrolled if c.suggested_name else False,
            "total_talk_time": round(total_talk),
            "meeting_count": len(meetings),
            "sample_count": len(c.samples),
            "meetings": meetings,
            "enrolled_speakers": sorted(enrolled),  # for reassignment dropdown
            "samples": [
                {
                    "meeting_name": s.meeting_name,
                    "meeting_file": s.meeting_file,
                    "speaker_label": s.speaker_label,
                    "start": round(s.start, 1),
                    "end": round(s.end, 1),
                    "duration": round(s.duration, 1),
                    "total_talk_time": round(s.total_talk_time),
                    "text_preview": s.text_preview,
                    "quality_score": round(s.quality_score, 2),
                }
                for s in sorted(c.samples, key=lambda x: -x.quality_score)
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

    for c in clusters:
        name = c.suggested_name or "Unknown"
        conf = f" ({c.confidence:.0%})" if c.confidence > 0 else ""
        meetings = len(set(s.meeting_name for s in c.samples))
        print(f"  {name}{conf}: {len(c.samples)} samples, {meetings} meetings", file=sys.stderr)

    data = export_for_ui(clusters)
    print(json.dumps(data, indent=2))
