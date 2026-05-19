"""Layer 2 of the voice-training plan: re-diarize a transcript using
the user's already-confirmed speaker labels as anchor centroids.

Algorithm (kept deliberately small for clarity — see
PLAN-voice-training-layers-2026-04-26.md for the design rationale):

  1. Read the diarized JSON. Collect the set of segments whose
     speaker has a non-default name (anything other than
     "Speaker N" auto-labels) — these are the user's anchors.
  2. Load the audio file and the TitaNet embedding model.
  3. Compute one embedding per anchor segment, average per name to
     get the anchor centroid for that speaker.
  4. For every segment in the transcript, embed and find the
     nearest anchor by cosine similarity. If similarity is above
     `THRESHOLD`, reassign to that anchor's speaker_id. Otherwise
     leave the segment's existing speaker_id alone (the user's
     correction-then-re-cluster workflow shouldn't aggressively
     re-stamp segments the algorithm isn't confident about).
  5. After reassignment, run the same consecutive-merge pass the
     original diarize_lite uses, so the transcript reads cleanly.
  6. Write the updated JSON back in place. The transcript writer in
     the Mac app will see the new speaker_id assignments next time
     the viewer loads the file.

This module only exposes one entry point — `recluster_with_anchors` —
which transcribe.py wires up as the `recluster-with-anchors`
subcommand.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.spatial.distance import cosine as cosine_distance

from shared.audio_utils import extract_embedding, load_audio


# Tuned conservatively: 0.55 cosine similarity (= 0.45 cosine distance)
# is the threshold below which we KEEP the segment's existing label
# rather than risk a wrong reassignment. Will move into eval-suite
# scope once we have ground-truth recordings to measure DER on.
SIMILARITY_THRESHOLD = 0.55
_SAMPLE_RATE = 16000
_MIN_EMBEDDING_DURATION = 1.5


def _is_user_named(speaker_id: int, names: dict[str, str]) -> bool:
    """A speaker counts as user-anchored when their name has been
    edited away from the auto-generated 'Speaker N' format. The Mac
    transcript viewer writes default names back into speaker_names
    only when the user explicitly types something — but we still
    guard against the value matching the default literal in case the
    user typed it back manually for some reason."""
    raw = names.get(str(speaker_id), "").strip()
    if not raw:
        return False
    if raw == f"Speaker {speaker_id + 1}":
        return False
    return True


def _embed_segment(
    audio: np.ndarray,
    start_s: float,
    end_s: float,
    onnx_session,
) -> np.ndarray | None:
    """Slice the audio + return a TitaNet embedding, or None if the
    slice is too short to embed cleanly. Mirrors diarize_lite's
    1.5 s minimum so we're consistent across the pipeline."""
    if (end_s - start_s) < _MIN_EMBEDDING_DURATION:
        return None
    s = max(0, int(start_s * _SAMPLE_RATE))
    e = min(len(audio), int(end_s * _SAMPLE_RATE))
    chunk = audio[s:e]
    if len(chunk) == 0:
        return None
    return extract_embedding(chunk, sr=_SAMPLE_RATE, onnx_session=onnx_session)


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """1 - cosine_distance, clamped into [-1, 1]. Higher = more similar."""
    return float(1.0 - cosine_distance(a, b))


def _merge_consecutive_same_speaker(segments: list[dict]) -> list[dict]:
    """After reassignment we may end up with adjacent segments now
    sharing a speaker_id. Collapse those into one for readability —
    this matches the post-cluster pass diarize_lite already runs after
    its initial clustering."""
    if not segments:
        return segments
    merged = [dict(segments[0])]
    for seg in segments[1:]:
        last = merged[-1]
        if last.get("speaker_id") == seg.get("speaker_id"):
            last["end"] = seg["end"]
            last["text"] = (last.get("text", "") + " " + seg.get("text", "")).strip()
        else:
            merged.append(dict(seg))
    return merged


def recluster_with_anchors(
    diarized_json_path: Path,
    similarity_threshold: float = SIMILARITY_THRESHOLD,
) -> dict:
    """Re-diarize the transcript at `diarized_json_path` using its
    already-named segments as anchors.

    Returns a small JSON-friendly summary dict for the CLI to print:
      {
        "anchors": {"James": 12, "Chris": 5, ...},   # segs per anchor
        "reassigned": int,    # how many segments were reassigned
        "kept": int,          # how many segments left untouched
        "skipped_short": int, # how many couldn't be embedded
        "audio_file": "...",
      }
    """
    data = json.loads(diarized_json_path.read_text(encoding="utf-8"))
    audio_path = data.get("audio_file", "")
    segments = data.get("segments", [])
    names = data.get("speaker_names", {})

    if not segments:
        return {"error": "no segments in JSON", "audio_file": audio_path}
    if not Path(audio_path).exists():
        return {"error": f"audio file not found: {audio_path}", "audio_file": audio_path}

    # Identify anchors: segments belonging to any user-named speaker.
    anchor_segments_by_name: dict[str, list[dict]] = defaultdict(list)
    for seg in segments:
        sid = int(seg.get("speaker_id", 0))
        if _is_user_named(sid, names):
            anchor_segments_by_name[names[str(sid)].strip()].append(seg)

    if not anchor_segments_by_name:
        return {
            "error": "no user-named speakers to anchor against",
            "audio_file": audio_path,
        }

    # Load audio + embedding model once.
    print("Loading audio and speaker embedding model…", file=sys.stderr, flush=True)
    audio = load_audio(audio_path, sr=_SAMPLE_RATE)
    from shared.diarize_lite import _load_speaker_embed_model
    onnx_session = _load_speaker_embed_model()

    # Compute anchor centroids: average of every embedding from segments
    # the user labelled as that name. Skips segments below the
    # min-duration floor (consistent with diarize_lite).
    centroids: dict[str, np.ndarray] = {}
    skipped_short_anchors = 0
    for name, segs in anchor_segments_by_name.items():
        embs: list[np.ndarray] = []
        for seg in segs:
            emb = _embed_segment(audio, seg["start"], seg["end"], onnx_session)
            if emb is None:
                skipped_short_anchors += 1
                continue
            embs.append(emb)
        if not embs:
            continue
        centroid = np.mean(np.vstack(embs), axis=0)
        # L2-normalize so cosine similarity is well-behaved.
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroid = centroid / norm
        centroids[name] = centroid

    if not centroids:
        return {
            "error": "all anchor segments were too short to embed",
            "audio_file": audio_path,
        }

    # Map each anchor name to a canonical speaker_id (the lowest one
    # in `names` matching that name — the user might have multiple IDs
    # collapsed under one name via the existing merge action).
    name_to_id: dict[str, int] = {}
    for sid_str, nm in names.items():
        try:
            sid = int(sid_str)
        except ValueError:
            continue
        nm_clean = nm.strip()
        if nm_clean in centroids:
            if nm_clean not in name_to_id or sid < name_to_id[nm_clean]:
                name_to_id[nm_clean] = sid

    # Reassign every segment.
    reassigned = 0
    kept = 0
    skipped_short = 0
    for seg in segments:
        emb = _embed_segment(audio, seg["start"], seg["end"], onnx_session)
        if emb is None:
            skipped_short += 1
            continue
        norm = np.linalg.norm(emb)
        if norm > 0:
            emb = emb / norm
        # Find the closest anchor.
        best_name, best_sim = None, -1.0
        for name, centroid in centroids.items():
            sim = _cosine_similarity(emb, centroid)
            if sim > best_sim:
                best_name, best_sim = name, sim
        if best_name is None or best_sim < similarity_threshold:
            kept += 1
            continue
        new_id = name_to_id.get(best_name, int(seg.get("speaker_id", 0)))
        if int(seg.get("speaker_id", 0)) != new_id:
            seg["speaker_id"] = new_id
            reassigned += 1
        else:
            kept += 1

    # Make sure every name we used has a row in speaker_names. If the
    # user merged two IDs, the merge action already pruned the loser
    # name, but a re-cluster might have just resurrected the loser ID
    # in a non-anchor segment that's NOW being assigned to that
    # winner's centroid — so re-write speaker_names from the canonical
    # name_to_id map to keep things consistent.
    cleaned_names: dict[str, str] = {}
    for name, sid in name_to_id.items():
        cleaned_names[str(sid)] = name
    # Preserve any default-named entries that still appear in segments.
    for seg in segments:
        sid = str(int(seg.get("speaker_id", 0)))
        if sid not in cleaned_names and sid in names:
            cleaned_names[sid] = names[sid]
    data["speaker_names"] = cleaned_names

    # Final consecutive-same-speaker merge for clean reading.
    data["segments"] = _merge_consecutive_same_speaker(segments)

    diarized_json_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return {
        "audio_file": audio_path,
        "anchors": {name: len(segs) for name, segs in anchor_segments_by_name.items()},
        "reassigned": reassigned,
        "kept": kept,
        "skipped_short": skipped_short,
        "skipped_short_anchors": skipped_short_anchors,
    }
