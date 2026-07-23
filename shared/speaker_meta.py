"""Speaker provenance + review-state helpers for the tagging verify loop.

The diarized sidecar (`<base>_diarized.json`) carries, alongside `speaker_names`:

    "speaker_meta":       { "<id>": {"source": ..., "confidence": ..., "verified": bool} }
    "speaker_embeddings": { "<id>": [float, ...] }        # L2-normalised TitaNet

`source` is one of:
    "auto"    — matched from the voice library (unverified until the user confirms)
    "user"    — a name the user typed / confirmed
    "unknown" — an acknowledged guest the user chose not to name
    "generic" — untouched "Speaker N"
    "legacy" / "legacy_import" — timestamped historical naming evidence

This module owns the `rematch` operation: after the voice library grows, sweep a
meeting's still-generic speakers and try to auto-match them against the current
library — using the stored per-speaker embeddings when present, or re-deriving
them from the audio for legacy sidecars. It never overwrites a name the user has
confirmed.
"""
from __future__ import annotations

import re
import sys
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

_GENERIC_RE = re.compile(r"^Speaker \d+$")
_MATCH_THRESHOLD = 0.65
_NON_IDENTITY_NAMES = {"unknown", "unknown speaker"}
_LEGACY_SOURCES = {"legacy", "legacy_import"}
_HUMAN_ARCHIVE_SOURCE = "human_archive_verified"

# A Rematch preflight is intentionally stricter than the legacy auto-match
# threshold. It is a review queue, not an identity decision: noisy/crowded
# meetings should remain generic even when a raw cosine happens to be high.
_PREFLIGHT_MATCH_THRESHOLD = 0.70
_PREFLIGHT_MIN_MARGIN = 0.08
_PREFLIGHT_MIN_TALK_SECONDS = 8.0
_PREFLIGHT_MIN_TURNS = 3
_PREFLIGHT_MAX_SPEAKERS = 6
_REMATCH_CORRECTIONS_FILE = Path.home() / "HiDock" / "Voice Library" / "rematch-corrections.jsonl"


def is_generic_name(name: str | None) -> bool:
    """True for an untouched "Speaker N" label (or empty)."""
    if not name:
        return True
    return bool(_GENERIC_RE.match(name.strip()))


def backfill_label_source(
    name: str | None,
    metadata: dict | None,
    *,
    include_legacy: bool = False,
) -> str | None:
    """Return the trustworthy source class for backfill, or ``None``.

    This is the single admission policy for the voice-library backfill and
    historical-coverage counts.  It deliberately keeps an acknowledged
    ``unknown`` guest, generic labels, all automatic matches, and metadata-free
    historical labels out of the training set.  A sidecar predating provenance
    metadata is only usable through an explicit legacy migration or a
    human-verified archive manifest.
    """
    cleaned = " ".join(str(name or "").split())
    if is_generic_name(cleaned) or cleaned.casefold() in _NON_IDENTITY_NAMES:
        return None

    meta = metadata if isinstance(metadata, dict) else {}
    if not meta:
        return "legacy_import" if include_legacy else None

    source = str(meta.get("source") or "").casefold()
    if source == "user" and meta.get("verified") is True:
        return "user"
    if source == _HUMAN_ARCHIVE_SOURCE and meta.get("verified") is True:
        return _HUMAN_ARCHIVE_SOURCE
    if source in _LEGACY_SOURCES and include_legacy:
        return source
    return None


def infer_source(name: str | None) -> str:
    """Best-effort provenance for a sidecar with no speaker_meta (legacy).
    A generic label is "generic"; a non-generic label is retained as
    ``legacy_import`` rather than being silently reclassified as an unverified
    automatic match."""
    return "generic" if is_generic_name(name) else "legacy_import"


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
    from shared.voice_library_lite import library_scores

    names = data.get("speaker_names", {}) or {}
    embeddings = data.get("speaker_embeddings") or {}

    out: dict[str, dict] = {}
    for sid, name in names.items():
        emb = embeddings.get(sid)
        if emb is None:
            continue
        sims = library_scores(emb)   # best-of-exemplars per enrolled speaker
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


def _rematch_speaker_evidence(data: dict, speaker_id: str) -> dict:
    """Return explainable meeting-local evidence for one generic cluster."""
    segments = [
        segment for segment in data.get("segments", [])
        if str(segment.get("speaker_id", "")) == str(speaker_id)
    ]
    durations = [
        max(0.0, float(segment.get("end", 0.0)) - float(segment.get("start", 0.0)))
        for segment in segments
    ]
    active_ids = {str(segment.get("speaker_id", "")) for segment in data.get("segments", [])}
    return {
        "turn_count": len(segments),
        "talk_seconds": round(sum(durations), 3),
        "longest_turn_seconds": round(max(durations, default=0.0), 3),
        "meeting_speaker_count": len(active_ids),
    }


def rematch_preflight(
    data: dict,
    *,
    threshold: float = _PREFLIGHT_MATCH_THRESHOLD,
    min_margin: float = _PREFLIGHT_MIN_MARGIN,
    min_talk_seconds: float = _PREFLIGHT_MIN_TALK_SECONDS,
    min_turns: int = _PREFLIGHT_MIN_TURNS,
    max_meeting_speakers: int = _PREFLIGHT_MAX_SPEAKERS,
) -> dict:
    """Create a no-write review queue for generic speakers with embeddings.

    Raw cosine similarity is only one signal. This preflight deliberately
    withholds a candidate when the speaker cluster is too short, too fragmented,
    ambiguous against another enrolled person, or comes from a crowded meeting.
    The result is suitable for a human reviewer and does not alter ``data``.
    """
    from shared.voice_library_lite import library_scores

    names = data.get("speaker_names", {}) or {}
    meta = data.get("speaker_meta", {}) or {}
    embeddings = data.get("speaker_embeddings", {}) or {}
    candidates = []
    for speaker_id, name in names.items():
        if not is_generic_name(name) or (meta.get(speaker_id, {}) or {}).get("verified", False):
            continue
        evidence = _rematch_speaker_evidence(data, str(speaker_id))
        entry = {"id": str(speaker_id), "current_name": name, **evidence}
        embedding = embeddings.get(str(speaker_id))
        reasons = []
        if embedding is None:
            reasons.append("missing_stored_embedding")
            entry.update({"decision": "hold", "reasons": reasons})
            candidates.append(entry)
            continue

        scores = sorted(library_scores(embedding), key=lambda pair: pair[1], reverse=True)
        if not scores:
            reasons.append("no_compatible_library_profile")
            entry.update({"decision": "hold", "reasons": reasons})
            candidates.append(entry)
            continue
        best_name, best_score = scores[0]
        runner_up = scores[1] if len(scores) > 1 else None
        margin = best_score - runner_up[1] if runner_up else None
        entry.update({
            "proposed_name": best_name,
            "similarity": best_score,
            "runner_up": runner_up[0] if runner_up else None,
            "runner_up_similarity": runner_up[1] if runner_up else None,
            "margin": round(margin, 4) if margin is not None else None,
        })
        if best_score < threshold:
            reasons.append("below_similarity_threshold")
        if margin is not None and margin < min_margin:
            reasons.append("ambiguous_runner_up")
        if evidence["talk_seconds"] < min_talk_seconds:
            reasons.append("insufficient_attributable_speech")
        if evidence["turn_count"] < min_turns:
            reasons.append("insufficient_turns")
        if evidence["meeting_speaker_count"] > max_meeting_speakers:
            reasons.append("crowded_meeting")
        entry["decision"] = "review" if not reasons else "hold"
        entry["reasons"] = reasons
        candidates.append(entry)

    return {
        "eligible": len(candidates),
        "review_candidates": [entry for entry in candidates if entry["decision"] == "review"],
        "hold_candidates": [entry for entry in candidates if entry["decision"] == "hold"],
        "policy": {
            "threshold": threshold,
            "min_margin": min_margin,
            "min_talk_seconds": min_talk_seconds,
            "min_turns": min_turns,
            "max_meeting_speakers": max_meeting_speakers,
        },
    }


def record_rematch_correction(
    sidecar_path: str | Path,
    *,
    speaker_id: str | int,
    action: str,
    proposed_name: str | None = None,
    final_name: str | None = None,
    event_path: str | Path = _REMATCH_CORRECTIONS_FILE,
) -> dict:
    """Append an immutable human-review outcome to the Rematch evaluation log.

    A rejected suggestion is evidence about matching policy, not negative
    training data for the proposed person's voice profile. Likewise, a confirmed
    result is recorded independently from any later enrollment action.
    """
    normalized_action = action.strip().lower()
    if normalized_action not in {"confirmed", "rejected", "unknown"}:
        raise ValueError("action must be confirmed, rejected, or unknown")
    event = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "sidecar_path": str(Path(sidecar_path).expanduser().resolve()),
        "speaker_id": str(speaker_id),
        "action": normalized_action,
        "proposed_name": proposed_name,
        "final_name": final_name,
    }
    target = Path(event_path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


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
