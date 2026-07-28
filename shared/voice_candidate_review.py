"""Review-only speaker identity suggestions from an isolated candidate model.

This module deliberately does not read or write the live voice library.  It
loads the explicitly configured candidate model and verified-only shadow
library, embeds unverified transcript speakers from bounded source audio, and
returns explainable suggestions for a human reviewer.  It never changes a
sidecar or applies a name.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from shared.human_archive_evidence import _candidate_for_person, _resolve_audio
from shared.legacy_transcript_recovery import LegacyTurn
from shared.speaker_meta import is_generic_name
from shared.voice_library_lite import (
    _MAX_SAMPLES,
    _audio_quality_from_path,
    _enroll_into,
    _extract_audio_embedding,
    _get_speaker_embed_session,
    _refresh_active_samples,
    _samples_of,
    cosine_similarity,
)


ACTIVE_CANDIDATE_CONFIG = (
    Path.home() / "HiDock" / "Voice Library Candidates" / "active.json"
)
_DEFAULT_SCORER = "top3_median"
_DEFAULT_THRESHOLD = 0.71
_DEFAULT_MIN_MARGIN = 0.21
_MAX_REVIEW_MEETING_SPEAKERS = 6
_MIN_ACOUSTIC_QUALITY = 0.45


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def _resolve_config_path(raw: str | None, base: Path) -> Path | None:
    if not raw:
        return None
    path = Path(raw).expanduser()
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def load_candidate_config(path: str | Path = ACTIVE_CANDIDATE_CONFIG) -> dict:
    """Load and validate the explicit review-candidate configuration."""
    config_path = Path(path).expanduser().resolve()
    if not config_path.exists():
        return {
            "available": False,
            "review_only": True,
            "reason": "candidate_not_configured",
            "config_path": str(config_path),
        }
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "available": False,
            "review_only": True,
            "reason": f"candidate_config_unreadable: {exc}",
            "config_path": str(config_path),
        }

    base = config_path.parent
    candidate_dir = _resolve_config_path(raw.get("candidate_dir"), base) or base
    model_path = _resolve_config_path(raw.get("model_path"), candidate_dir)
    library_path = _resolve_config_path(raw.get("library_path"), candidate_dir)
    missing = [
        label
        for label, value in (("model", model_path), ("library", library_path))
        if value is None or not value.exists()
    ]
    if raw.get("enabled") is False:
        missing.append("disabled")
    return {
        **raw,
        "available": not missing,
        # Honour the config instead of pinning this on. It was hardcoded True
        # while the candidate model was being trialled, which made the flag in
        # active.json decorative — promoting a candidate to automatic naming
        # needed a code change nobody would think to look for.
        #
        # The default stays True, so a config that says nothing is still
        # review-only. Promotion is a deliberate act, and worth being deliberate
        # about: check `shared.models.speaker_embed_licence(model_key)` first,
        # because the strongest model here is CC BY-NC-SA and must not ship in a
        # distributed build.
        "review_only": bool(raw.get("review_only", True)),
        "reason": None if not missing else "candidate_unavailable: " + ", ".join(missing),
        "config_path": str(config_path),
        "candidate_dir": str(candidate_dir),
        "model_path": str(model_path) if model_path else None,
        "library_path": str(library_path) if library_path else None,
        "model_key": str(raw.get("model_key") or "wespeaker_resnet293"),
        "scorer": str(raw.get("scorer") or _DEFAULT_SCORER),
        "threshold": float(raw.get("threshold", _DEFAULT_THRESHOLD)),
        "min_margin": float(raw.get("min_margin", _DEFAULT_MIN_MARGIN)),
    }


def activate_candidate(
    candidate_dir: str | Path,
    *,
    model_path: str | Path | None = None,
    config_path: str | Path = ACTIVE_CANDIDATE_CONFIG,
) -> dict:
    """Validate and activate a benchmark candidate for app review only."""
    candidate = Path(candidate_dir).expanduser().resolve()
    library = candidate / "voice-library.json"
    model = (
        Path(model_path).expanduser().resolve()
        if model_path is not None
        else candidate / "voxceleb_resnet293_LM.onnx"
    )
    if not candidate.is_dir():
        raise ValueError(f"candidate directory not found: {candidate}")
    if not library.is_file():
        raise ValueError(f"candidate library not found: {library}")
    if not model.is_file():
        raise ValueError(f"candidate model not found: {model}")
    expected_hash = "dbb1ccc7754caff552ebc46347a51aaee2669bb24efc740e665d1a1133d20e98"
    actual_hash = _sha256(model)
    if actual_hash != expected_hash:
        raise ValueError(
            f"candidate model hash mismatch: expected {expected_hash}, got {actual_hash}"
        )
    try:
        library_data = json.loads(library.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"candidate library unreadable: {exc}") from exc
    if not isinstance(library_data.get("speakers"), dict):
        raise ValueError("candidate library has no speakers map")

    config = {
        "schema_version": 1,
        "enabled": True,
        "review_only": True,
        "activated_at": _now(),
        "candidate_dir": str(candidate),
        "model_key": "wespeaker_resnet293",
        "model_path": str(model),
        "model_sha256": expected_hash,
        "library_path": str(library),
        "scorer": _DEFAULT_SCORER,
        "threshold": _DEFAULT_THRESHOLD,
        "min_margin": _DEFAULT_MIN_MARGIN,
        "max_active_samples": _MAX_SAMPLES,
        "speaker_count": len(library_data["speakers"]),
    }
    destination = Path(config_path).expanduser().resolve()
    _atomic_write(destination, config)
    return {**config, "config_path": str(destination)}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _rank_library(library: dict, embedding: np.ndarray, scorer: str) -> list[dict]:
    """Rank identities using one representative per source meeting."""
    ranked = []
    for name, entry in (library.get("speakers") or {}).items():
        by_meeting: dict[str, list[list[float]]] = defaultdict(list)
        for sample in _samples_of(entry):
            vector = sample.get("embedding")
            if sample.get("active") is False or not isinstance(vector, list):
                continue
            if len(vector) != len(embedding):
                continue
            source = str(
                sample.get("source_file")
                or sample.get("audio_file")
                or sample.get("id")
                or "unknown"
            )
            by_meeting[source].append(vector)
        meeting_scores = sorted(
            (
                max(cosine_similarity(embedding, vector) for vector in vectors)
                for vectors in by_meeting.values()
            ),
            reverse=True,
        )
        if not meeting_scores:
            continue
        used_scorer = scorer
        if scorer == "top3_median" and len(meeting_scores) >= 3:
            score = float(np.median(meeting_scores[:3]))
        elif scorer == "top3_median":
            # A thin identity can still be useful to a human, but it is never
            # represented as having passed the robust three-meeting policy.
            score = meeting_scores[0]
            used_scorer = "max_thin_profile"
        elif scorer == "max":
            score = meeting_scores[0]
        else:
            raise ValueError(f"Unsupported candidate scorer: {scorer}")
        ranked.append({
            "name": name,
            "score": float(score),
            "scorer": used_scorer,
            "supporting_meetings": len(meeting_scores),
        })
    return sorted(ranked, key=lambda item: item["score"], reverse=True)


def suggest_for_transcript(
    sidecar_path: str | Path,
    *,
    config_path: str | Path = ACTIVE_CANDIDATE_CONFIG,
    session=None,
) -> dict:
    """Return no-write identity suggestions for unverified speakers."""
    sidecar = Path(sidecar_path).expanduser().resolve()
    config = load_candidate_config(config_path)
    base_result = {
        "status": "completed" if config.get("available") else "unavailable",
        "source_path": str(sidecar),
        "review_only": True,
        "candidate": {
            key: config.get(key)
            for key in ("model_key", "scorer", "threshold", "min_margin", "reason")
        },
        "suggestions": {},
        "skipped_verified": [],
        "failures": [],
    }
    if not config.get("available"):
        return base_result
    if not sidecar.exists():
        base_result.update({"status": "error", "error": "sidecar_not_found"})
        return base_result

    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        base_result.update({"status": "error", "error": str(exc)})
        return base_result

    names = data.get("speaker_names") or {}
    meta = data.get("speaker_meta") or {}
    segments = data.get("segments") or []
    # Calendar context recorded on the sidecar at diarization time (if any):
    # library-matched attendee names from the recording's event. Used to badge
    # suggestions and to hold proposed names no attendee list supports —
    # never to boost a score, so the tuned gate stays calibrated.
    calendar_meta = data.get("calendar_context") or {}
    calendar_candidates = set()
    if not calendar_meta.get("calendar_ambiguous"):
        calendar_candidates = {
            str(name).casefold()
            for name in (calendar_meta.get("calendar_candidate_names") or [])
        }
    active_speakers = {str(segment.get("speaker_id")) for segment in segments}
    unverified_speakers = {
        speaker_id
        for speaker_id in active_speakers
        if (meta.get(speaker_id) or {}).get("verified") is not True
    }
    if not unverified_speakers:
        base_result["skipped_verified"] = sorted(
            active_speakers,
            key=lambda value: (0, int(value)) if value.isdigit() else (1, value),
        )
        return base_result

    model_path = Path(config["model_path"])
    expected_hash = str(config.get("model_sha256") or "").lower()
    if expected_hash and _sha256(model_path) != expected_hash:
        base_result.update({"status": "error", "error": "candidate_model_hash_mismatch"})
        return base_result
    try:
        library = json.loads(Path(config["library_path"]).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        base_result.update({"status": "error", "error": str(exc)})
        return base_result
    audio = _resolve_audio(sidecar, data)
    if audio is None or not audio.exists():
        base_result.update({"status": "error", "error": "source_audio_missing"})
        return base_result
    session = session or _get_speaker_embed_session(config["model_key"], model_path)
    if session is None:
        base_result.update({"status": "error", "error": "candidate_model_unavailable"})
        return base_result

    meeting_speaker_count = len(active_speakers)
    suggestions: dict[str, dict] = {}
    for speaker_id in sorted(
        active_speakers,
        key=lambda value: (0, int(value)) if value.isdigit() else (1, value),
    ):
        name = str(names.get(speaker_id) or f"Speaker {speaker_id}")
        state = meta.get(speaker_id) or {}
        if state.get("verified") is True:
            base_result["skipped_verified"].append(speaker_id)
            continue
        turns = [
            LegacyTurn(float(segment["start"]), float(segment["end"]), name)
            for segment in segments
            if str(segment.get("speaker_id")) == speaker_id
            and float(segment.get("end", 0)) > float(segment.get("start", 0))
        ]
        selected = _candidate_for_person(turns)
        if selected is None:
            suggestions[speaker_id] = {
                "current_name": name,
                "decision": "hold",
                "review_only": True,
                "reasons": ["insufficient_contiguous_speech"],
            }
            continue
        try:
            embedding, dimension, model = _extract_audio_embedding(
                audio,
                segment_start=selected["segment_start"],
                segment_end=selected["segment_end"],
                session=session,
                neural_model_version=config["model_key"],
            )
            ranked = _rank_library(library, embedding, config["scorer"])
            if not ranked:
                suggestions[speaker_id] = {
                    "current_name": name,
                    "decision": "hold",
                    "review_only": True,
                    "reasons": ["no_compatible_candidate_profiles"],
                }
                continue
            best = ranked[0]
            runner_up = ranked[1] if len(ranked) > 1 else None
            margin = best["score"] - (runner_up["score"] if runner_up else -1.0)
            quality = _audio_quality_from_path(
                audio,
                segment_start=selected["segment_start"],
                segment_end=selected["segment_end"],
            )
            reasons = []
            robust = best["scorer"] == "top3_median"
            if not robust:
                reasons.append("thin_profile_manual_review_only")
            if runner_up is None:
                reasons.append("single_ranked_identity")
            if best["score"] < config["threshold"]:
                reasons.append("below_similarity_threshold")
            if margin < config["min_margin"]:
                reasons.append("ambiguous_runner_up")
            if meeting_speaker_count > _MAX_REVIEW_MEETING_SPEAKERS:
                reasons.append("crowded_meeting")
            acoustic_quality = quality.get("acoustic_quality")
            if acoustic_quality is not None and float(acoustic_quality) < _MIN_ACOUSTIC_QUALITY:
                reasons.append("low_audio_cleanliness")
            in_calendar = None
            runner_up_in_calendar = None
            if calendar_candidates:
                in_calendar = best["name"].casefold() in calendar_candidates
                if runner_up is not None:
                    runner_up_in_calendar = runner_up["name"].casefold() in calendar_candidates
                if not in_calendar:
                    # The event's attendee list does not support this name.
                    # It may still be a genuine guest, so this is a review
                    # hold, not a rejection — but it must not pass strong.
                    reasons.append("not_in_calendar")
            strong = robust and not reasons
            suggestions[speaker_id] = {
                "current_name": name,
                "current_source": state.get("source") or (
                    "generic" if is_generic_name(name) else "auto"
                ),
                "proposed_name": best["name"],
                "similarity": round(best["score"], 4),
                "runner_up": runner_up["name"] if runner_up else None,
                "runner_up_similarity": round(runner_up["score"], 4) if runner_up else None,
                "margin": round(margin, 4),
                "scorer": best["scorer"],
                "supporting_meetings": best["supporting_meetings"],
                "in_calendar": in_calendar,
                "runner_up_in_calendar": runner_up_in_calendar,
                "decision": "strong_review" if strong else "review",
                "review_only": True,
                "reasons": reasons,
                "embedding_dim": dimension,
                "embedding_model": model,
                "clip_start": selected["segment_start"],
                "clip_end": selected["segment_end"],
                "meeting_speaker_count": meeting_speaker_count,
                **quality,
            }
        except Exception as exc:  # one speaker must not hide all other suggestions
            base_result["failures"].append({"speaker_id": speaker_id, "error": str(exc)})
    base_result["suggestions"] = suggestions
    return base_result


def record_suggestion_outcome(
    sidecar_path: str | Path,
    *,
    speaker_id: str | int,
    action: str,
    proposed_name: str | None = None,
    final_name: str | None = None,
    config_path: str | Path = ACTIVE_CANDIDATE_CONFIG,
    session=None,
) -> dict:
    """Record an explicit review and teach only the isolated candidate library.

    A confirmed/corrected identity is enrolled only when the saved sidecar is
    already marked verified with the same final name. This makes a user save,
    rather than a model proposal, the authority for candidate-library changes.
    Unknown outcomes are logged but never enrolled.
    """
    if action not in {"confirmed", "rejected", "unknown"}:
        raise ValueError("action must be confirmed, rejected, or unknown")

    sidecar = Path(sidecar_path).expanduser().resolve()
    config = load_candidate_config(config_path)
    if not config.get("available"):
        raise ValueError(str(config.get("reason") or "candidate unavailable"))
    if not sidecar.exists():
        raise ValueError(f"sidecar not found: {sidecar}")

    data = json.loads(sidecar.read_text(encoding="utf-8"))
    sid = str(speaker_id)
    saved_name = str((data.get("speaker_names") or {}).get(sid) or "").strip()
    saved_meta = (data.get("speaker_meta") or {}).get(sid) or {}
    proposed = " ".join(str(proposed_name or "").split()) or None
    final = " ".join(str(final_name or saved_name).split()) or None

    should_enroll = action in {"confirmed", "rejected"} and final is not None
    if should_enroll:
        if saved_meta.get("verified") is not True:
            raise ValueError("candidate learning requires a verified saved speaker")
        if saved_name.casefold() != final.casefold():
            raise ValueError("final name does not match the verified saved speaker")
        if is_generic_name(final):
            raise ValueError("candidate learning requires a real speaker name")

    event = {
        "schema_version": 1,
        "recorded_at": _now(),
        "source_path": str(sidecar),
        "speaker_id": sid,
        "action": action,
        "proposed_name": proposed,
        "final_name": final if action != "unknown" else None,
        "review_only_model": config["model_key"],
        "enrolled": False,
    }

    if should_enroll:
        audio = _resolve_audio(sidecar, data)
        if audio is None or not audio.exists():
            raise ValueError("source audio missing; review was not recorded")
        turns = [
            LegacyTurn(float(segment["start"]), float(segment["end"]), final)
            for segment in (data.get("segments") or [])
            if str(segment.get("speaker_id")) == sid
            and float(segment.get("end", 0)) > float(segment.get("start", 0))
        ]
        selected = _candidate_for_person(turns)
        if selected is None:
            raise ValueError("speaker has no suitable contiguous clip for learning")

        model_path = Path(config["model_path"])
        expected_hash = str(config.get("model_sha256") or "").lower()
        if expected_hash and _sha256(model_path) != expected_hash:
            raise ValueError("candidate model hash mismatch")
        session = session or _get_speaker_embed_session(config["model_key"], model_path)
        if session is None:
            raise ValueError("candidate model unavailable")

        embedding, dimension, model = _extract_audio_embedding(
            audio,
            segment_start=selected["segment_start"],
            segment_end=selected["segment_end"],
            session=session,
            neural_model_version=config["model_key"],
        )
        quality = _audio_quality_from_path(
            audio,
            segment_start=selected["segment_start"],
            segment_end=selected["segment_end"],
        )
        library_path = Path(config["library_path"])
        library = json.loads(library_path.read_text(encoding="utf-8"))
        provenance = {
            "source_file": str(sidecar),
            "audio_file": str(audio.resolve()),
            "speaker_id": sid,
            "segment_start": selected["segment_start"],
            "segment_end": selected["segment_end"],
            "turn_count": len(turns),
            "total_talk_seconds": round(sum(turn.end - turn.start for turn in turns), 3),
            "label_source": "user",
            "observed_name": saved_name,
            "review_action": action,
            "reviewed_at": event["recorded_at"],
            **quality,
        }
        _enroll_into(
            library,
            final,
            embedding,
            embed_dim=dimension,
            model=model,
            source="confirm",
            provenance=provenance,
            max_samples=int(config.get("max_active_samples", _MAX_SAMPLES)),
        )
        _atomic_write(library_path, library)
        event.update({
            "enrolled": True,
            "library_path": str(library_path),
            "clip_start": selected["segment_start"],
            "clip_end": selected["segment_end"],
            "acoustic_quality": quality.get("acoustic_quality"),
        })

    event_path = Path(config["candidate_dir"]) / "review-events.jsonl"
    event_path.parent.mkdir(parents=True, exist_ok=True)
    with event_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    event["event_log"] = str(event_path)
    return event


def list_candidate_speakers(
    config_path: str | Path = ACTIVE_CANDIDATE_CONFIG,
) -> list[dict]:
    """List identities in the review-only candidate library for UI display.

    Returns one row per person with sample/meeting coverage and whether the
    profile has enough active meetings to ever pass the robust three-meeting
    gate. Empty when no candidate is configured. Read-only.
    """
    config = load_candidate_config(config_path)
    if not config.get("available"):
        return []
    library = json.loads(Path(config["library_path"]).read_text(encoding="utf-8"))
    people = []
    for name, entry in (library.get("speakers") or {}).items():
        samples = _samples_of(entry)
        meetings = set()
        active_meetings = set()
        for sample in samples:
            source = str(
                sample.get("source_file") or sample.get("audio_file") or ""
            )
            if not source:
                continue
            meetings.add(source)
            if sample.get("active") is not False:
                active_meetings.add(source)
        people.append({
            "name": name,
            "sample_count": len(samples),
            "active_sample_count": sum(
                1 for sample in samples if sample.get("active") is not False
            ),
            "meeting_count": len(meetings),
            # Recording names (sidecar basename minus _diarized) so the app can
            # point a person filter at real meetings without re-reading samples.
            "meetings": sorted(_meeting_key(source) for source in meetings),
            "last_updated": str(entry.get("last_updated") or ""),
            "strong_eligible": len(active_meetings) >= 3,
        })
    return sorted(people, key=lambda item: item["name"].casefold())


def _meeting_key(source_path: str) -> str:
    """Reduce a sample source path to the recording/meeting name used by the app."""
    stem = Path(source_path).stem
    for suffix in ("_diarized", "_whisper"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    return stem


def merge_candidate_speakers(
    source_name: str,
    target_name: str,
    *,
    config_path: str | Path = ACTIVE_CANDIDATE_CONFIG,
) -> dict:
    """Merge one candidate-library identity into another (explicit human decision).

    Folds the source's samples into the target and deletes the source key, or
    renames the source outright when the target does not exist yet. Writes are
    atomic, the decision is appended to review-events.jsonl, and the active
    config's speaker_count is kept in step. Review-only boundaries are
    unchanged: this never touches the live voice library or any sidecar.
    """
    source = " ".join(str(source_name or "").split())
    target = " ".join(str(target_name or "").split())
    if not source or not target:
        raise ValueError("both source and target names are required")
    if source == target:
        raise ValueError("source and target names must differ")
    config = load_candidate_config(config_path)
    if not config.get("available"):
        raise ValueError(str(config.get("reason") or "candidate unavailable"))

    library_path = Path(config["library_path"])
    library = json.loads(library_path.read_text(encoding="utf-8"))
    speakers = library.get("speakers")
    if not isinstance(speakers, dict):
        raise ValueError("candidate library has no speakers map")
    if source not in speakers:
        raise ValueError(f"candidate speaker not found: {source}")

    moved = list(_samples_of(speakers[source]))
    if target in speakers:
        entry = speakers[target]
        entry.setdefault("samples", _samples_of(entry)).extend(moved)
        _refresh_active_samples(entry, int(config.get("max_active_samples", _MAX_SAMPLES)))
        entry["last_updated"] = _now()
        del speakers[source]
        renamed = False
    else:
        speakers[target] = speakers.pop(source)
        speakers[target]["last_updated"] = _now()
        renamed = True
    _atomic_write(library_path, library)

    config_file = Path(config["config_path"])
    saved_config = json.loads(config_file.read_text(encoding="utf-8"))
    saved_config["speaker_count"] = len(speakers)
    _atomic_write(config_file, saved_config)

    event = {
        "schema_version": 1,
        "kind": "library_merge",
        "recorded_at": _now(),
        "action": "merged",
        "source_name": source,
        "target_name": target,
        "renamed": renamed,
        "samples_moved": len(moved),
        "meetings_moved": len({
            str(sample.get("source_file") or sample.get("audio_file") or "")
            for sample in moved
            if sample.get("source_file") or sample.get("audio_file")
        }),
        "speaker_count": len(speakers),
        "library_path": str(library_path),
    }
    event_path = Path(config["candidate_dir"]) / "review-events.jsonl"
    event_path.parent.mkdir(parents=True, exist_ok=True)
    with event_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    event["event_log"] = str(event_path)
    return event


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sidecar", nargs="?", help="Path to an existing _diarized.json sidecar")
    parser.add_argument(
        "--activate",
        metavar="CANDIDATE_DIR",
        help="Validate and activate a benchmark candidate for app review",
    )
    parser.add_argument(
        "--model-path",
        help="Installed WeSpeaker ONNX path used with --activate",
    )
    parser.add_argument(
        "--config",
        default=str(ACTIVE_CANDIDATE_CONFIG),
        help="Explicit active candidate JSON (default: %(default)s)",
    )
    args = parser.parse_args()
    if args.activate:
        try:
            result = activate_candidate(
                args.activate,
                model_path=args.model_path,
                config_path=args.config,
            )
        except ValueError as exc:
            parser.error(str(exc))
        print(json.dumps(result))
        return
    if not args.sidecar:
        parser.error("sidecar is required unless --activate is used")
    print(json.dumps(suggest_for_transcript(args.sidecar, config_path=args.config)))


if __name__ == "__main__":
    main()
