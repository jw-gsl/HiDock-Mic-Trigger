"""Build auditable voice evidence from human-verified HiDock text exports.

The exports are not treated as a loose name hint.  They contain timestamped
turns previously mapped by a human, so a successful, unambiguous link to a
recording can provide both diarization anchors and bounded audio clips for a
separate shadow voice library.  Nothing in this module modifies a transcript
or the live voice library.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from shared.models import SPEAKER_EMBED_MODELS
from shared.legacy_transcript_recovery import (
    LegacyTurn,
    _legacy_segment_assignments,
    _raw_datetime,
    _recording_id,
    _refresh_markdown,
    parse_legacy_transcript,
)
from shared.srt_writer import srt_path_for, write_srt
from shared.voice_library_lite import (
    _AUDIO_QUALITY_VERSION,
    _MAX_SAMPLES,
    _NEURAL_MODEL_VERSION,
    _assess_sample_quality,
    _audio_quality_from_path,
    _deduplicate_diarized_samples,
    _enroll_into,
    _extract_audio_embedding,
    _get_speaker_embed_session,
    _refresh_active_samples,
    _samples_of,
    cosine_similarity,
    load_backfill_aliases,
)

_MAX_MATCH_DELTA_SECONDS = 5 * 60
_MIN_CLIP_SECONDS = 8.0
_MAX_CLIP_SECONDS = 30.0
_MERGE_GAP_SECONDS = 2.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_audio(sidecar: Path, data: dict) -> Path | None:
    value = data.get("audio_file")
    if not value:
        return None
    path = Path(str(value))
    if not path.is_absolute():
        path = sidecar.parent / path
    return path.resolve()


def _match_candidates(export: Path, created: datetime, sidecars: list[Path]) -> list[tuple[float, Path]]:
    candidates = [
        (abs((_raw_datetime(sidecar) - created).total_seconds()), sidecar)
        for sidecar in sidecars
        if _raw_datetime(sidecar) is not None and _raw_datetime(sidecar).date() == created.date()
    ]
    recording_id = _recording_id(export)
    if recording_id:
        identified = [(delta, path) for delta, path in candidates if recording_id in path.name.casefold()]
        if identified:
            candidates = identified
    return sorted((item for item in candidates if item[0] <= _MAX_MATCH_DELTA_SECONDS), key=lambda item: (item[0], item[1].name))


def _merge_turns(turns: list[LegacyTurn]) -> list[tuple[float, float]]:
    """Merge nearby labelled turns; a short pause does not change speaker."""
    merged: list[list[float]] = []
    for turn in sorted(turns, key=lambda item: (item.start, item.end)):
        if not merged or turn.start - merged[-1][1] > _MERGE_GAP_SECONDS:
            merged.append([turn.start, turn.end])
        else:
            merged[-1][1] = max(merged[-1][1], turn.end)
    return [(start, end) for start, end in merged if end > start]


def _candidate_for_person(turns: list[LegacyTurn]) -> dict | None:
    spans = _merge_turns(turns)
    usable = [(end - start, start, end) for start, end in spans if end - start >= _MIN_CLIP_SECONDS]
    if not usable:
        return None
    duration, start, end = max(usable)
    end = min(end, start + _MAX_CLIP_SECONDS)
    return {
        "segment_start": round(start, 3),
        "segment_end": round(end, 3),
        "segment_seconds": round(min(duration, _MAX_CLIP_SECONDS), 3),
    }


def build_human_archive_inventory(
    exports_dir: str | Path,
    transcripts_dir: str | Path,
    *,
    aliases: dict[str, str] | None = None,
) -> dict:
    """Return a no-write inventory of human-verified archive evidence.

    A match is accepted only when the nearest same-day sidecar is unique.  The
    manifest intentionally keeps unmatched/ambiguous exports visible instead
    of guessing, and yields at most one representative clip per person and
    meeting so the later library is diverse by construction.
    """
    exports = Path(exports_dir).expanduser().resolve()
    transcripts = Path(transcripts_dir).expanduser().resolve()
    sidecars = sorted(transcripts.glob("*_diarized.json"))
    aliases = {str(key).casefold(): value for key, value in (aliases or {}).items()}
    meetings: list[dict] = []
    candidates: list[dict] = []
    counts = defaultdict(int)

    for export in sorted(exports.glob("*.txt")):
        counts["exports"] += 1
        try:
            created, turns = parse_legacy_transcript(export)
        except OSError:
            counts["unreadable_exports"] += 1
            continue
        if not turns:
            continue
        counts["named_exports"] += 1
        base = {
            "export": str(export),
            "export_sha256": _sha256(export),
            "created_at": created.isoformat() if created else None,
            "recording_id": _recording_id(export),
            "named_turn_count": len(turns),
            "names": sorted({turn.name for turn in turns}, key=str.casefold),
        }
        if created is None:
            meetings.append({**base, "status": "unmatched_missing_creation_time"})
            counts["unmatched"] += 1
            continue
        matches = _match_candidates(export, created, sidecars)
        if not matches:
            meetings.append({**base, "status": "unmatched"})
            counts["unmatched"] += 1
            continue
        best_delta = matches[0][0]
        tied = [path for delta, path in matches if delta == best_delta]
        if len(tied) != 1:
            meetings.append({
                **base, "status": "ambiguous_sidecar_match", "match_delta_seconds": best_delta,
                "matching_sidecars": [str(path) for path in tied],
            })
            counts["ambiguous"] += 1
            continue
        sidecar = tied[0]
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meetings.append({**base, "status": "unreadable_sidecar", "sidecar": str(sidecar)})
            counts["unreadable_sidecars"] += 1
            continue
        audio = _resolve_audio(sidecar, data)
        grouped: dict[str, list[LegacyTurn]] = defaultdict(list)
        observed_by_person: dict[str, set[str]] = defaultdict(set)
        for turn in turns:
            canonical = aliases.get(turn.name.casefold(), turn.name)
            grouped[canonical].append(LegacyTurn(turn.start, turn.end, canonical))
            observed_by_person[canonical].add(turn.name)
        meeting_candidates = []
        for person, person_turns in sorted(grouped.items(), key=lambda item: item[0].casefold()):
            selected = _candidate_for_person(person_turns)
            if selected is None:
                counts["short_person_evidence"] += 1
                continue
            candidate = {
                "person": person,
                "observed_names": sorted(observed_by_person[person], key=str.casefold),
                "source": "human_archive_verified",
                "source_export": str(export),
                "source_export_sha256": base["export_sha256"],
                "source_sidecar": str(sidecar),
                "audio_file": str(audio) if audio else None,
                "turn_count": len(person_turns),
                "total_labelled_seconds": round(sum(turn.end - turn.start for turn in person_turns), 3),
                **selected,
            }
            candidate["eligible"] = bool(audio and audio.exists())
            candidate["reason"] = "eligible" if candidate["eligible"] else "source_audio_missing"
            meeting_candidates.append(candidate)
            candidates.append(candidate)
            counts["candidates"] += 1
            if candidate["eligible"]:
                counts["eligible_candidates"] += 1
        meetings.append({
            **base,
            "status": "matched",
            "sidecar": str(sidecar),
            "audio_file": str(audio) if audio else None,
            "match_delta_seconds": round(best_delta, 3),
            "candidate_count": len(meeting_candidates),
            "candidates": meeting_candidates,
        })
        counts["matched"] += 1

    return {
        "schema_version": 1,
        "generated_at": _now(),
        "policy": {
            "source": "human_archive_verified",
            "minimum_clip_seconds": _MIN_CLIP_SECONDS,
            "maximum_clip_seconds": _MAX_CLIP_SECONDS,
            "maximum_match_delta_seconds": _MAX_MATCH_DELTA_SECONDS,
            "one_clip_per_person_per_meeting": True,
            "aliases_applied": bool(aliases),
        },
        "summary": dict(sorted(counts.items())),
        "meetings": meetings,
        "candidates": candidates,
    }


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        Path(temp).replace(path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def build_shadow_library(
    inventory: dict | str | Path,
    *,
    output_path: str | Path | None = None,
    max_active_samples: int = _MAX_SAMPLES,
    dry_run: bool = False,
    start: int = 0,
    limit: int | None = None,
    model_key: str | None = None,
    model_path: str | Path | None = None,
) -> dict:
    """Embed eligible human-labelled clips into an isolated shadow library."""
    if not isinstance(inventory, dict):
        inventory = json.loads(Path(inventory).read_text(encoding="utf-8"))
    model_label = model_key or _NEURAL_MODEL_VERSION
    library = {"speakers": {}, "provenance": {
        "kind": "human_archive_shadow", "created_at": _now(), "embedding_model": model_label,
    }}
    session = _get_speaker_embed_session(model_key, model_path)
    if session is None:
        raise RuntimeError(f"Speaker embedding model is unavailable: {model_label}")
    all_candidates = inventory.get("candidates") or []
    selected = all_candidates[max(0, start):None if limit is None else max(0, start) + max(0, limit)]
    result = {
        "dry_run": dry_run, "candidate_start": max(0, start), "candidate_count": len(selected),
        "total_candidates": len(all_candidates), "attempted": 0, "enrolled": 0,
        "skipped": 0, "failures": [], "candidates": [],
    }
    for candidate in selected:
        if not candidate.get("eligible"):
            result["skipped"] += 1
            continue
        result["attempted"] += 1
        audio = Path(candidate["audio_file"])
        try:
            embedding, dimension, model = _extract_audio_embedding(
                audio, segment_start=candidate["segment_start"], segment_end=candidate["segment_end"],
                session=session, neural_model_version=model_label,
            )
            provenance = {
                "source_file": candidate["source_sidecar"],
                "speaker_id": f"human-archive:{candidate['person']}",
                "audio_file": str(audio),
                "segment_start": candidate["segment_start"],
                "segment_end": candidate["segment_end"],
                "turn_count": candidate["turn_count"],
                "total_talk_seconds": candidate["total_labelled_seconds"],
                "label_source": "human_archive_verified",
                "observed_name": candidate["person"],
                "human_archive": {
                    "export": candidate["source_export"],
                    "export_sha256": candidate["source_export_sha256"],
                },
            }
            provenance.update(_audio_quality_from_path(audio, segment_start=candidate["segment_start"], segment_end=candidate["segment_end"]))
            if not dry_run:
                _enroll_into(library, candidate["person"], embedding, dimension, model, "human_archive_verified", provenance, max_active_samples)
            quality = _assess_sample_quality("human_archive_verified", provenance)
            result["candidates"].append({
                "person": candidate["person"], "source_sidecar": candidate["source_sidecar"],
                "quality_score": quality["quality_score"], "active_eligible": quality["quality_state"] == "active_candidate",
            })
            result["enrolled"] += 1
        except Exception as exc:  # retain an auditable failure instead of silently dropping evidence
            result["failures"].append({"candidate": candidate, "error": str(exc)})
    for entry in library["speakers"].values():
        _refresh_active_samples(entry, max_active_samples)
    result["speaker_count"] = len(library["speakers"])
    result["sample_count"] = sum(len(_samples_of(entry)) for entry in library["speakers"].values())
    result["active_sample_count"] = sum(
        1 for entry in library["speakers"].values() for sample in _samples_of(entry) if sample.get("active") is not False
    )
    if output_path is not None and not dry_run:
        path = Path(output_path).expanduser().resolve()
        _atomic_write(path, library)
        result["output_path"] = str(path)
    return result


def merge_shadow_libraries(
    shards: list[str | Path],
    *,
    output_path: str | Path,
    max_active_samples: int = _MAX_SAMPLES,
) -> dict:
    """Combine independently-built shadow shards without ever touching live data."""
    library = {"speakers": {}, "provenance": {"kind": "human_archive_shadow", "created_at": _now(), "shards": []}}
    embedding_models: set[str] = set()
    for raw_path in shards:
        path = Path(raw_path).expanduser().resolve()
        data = json.loads(path.read_text(encoding="utf-8"))
        library["provenance"]["shards"].append(str(path))
        recorded_model = (data.get("provenance") or {}).get("embedding_model")
        if recorded_model:
            embedding_models.add(str(recorded_model))
        for name, incoming in (data.get("speakers") or {}).items():
            entry = library["speakers"].setdefault(name, {
                "enrolled_at": incoming.get("enrolled_at", _now()),
                "last_updated": incoming.get("last_updated", _now()),
                "embedding_dim": incoming.get("embedding_dim"),
                "model": incoming.get("model"),
                "samples": [],
            })
            entry["samples"].extend(_samples_of(incoming))
    for entry in library["speakers"].values():
        entry["samples"] = _deduplicate_diarized_samples(entry["samples"])
        _refresh_active_samples(entry, max_active_samples)
    library["provenance"]["embedding_models"] = sorted(embedding_models)
    if len(embedding_models) == 1:
        library["provenance"]["embedding_model"] = next(iter(embedding_models))
    path = Path(output_path).expanduser().resolve()
    _atomic_write(path, library)
    return {
        "output_path": str(path), "shard_count": len(shards), "speaker_count": len(library["speakers"]),
        "sample_count": sum(len(_samples_of(entry)) for entry in library["speakers"].values()),
        "active_sample_count": sum(1 for entry in library["speakers"].values() for sample in _samples_of(entry) if sample.get("active") is not False),
    }


def _scores(library: dict, embedding: np.ndarray, *, scorer: str = "max") -> list[tuple[str, float]]:
    scores = []
    for name, entry in library.get("speakers", {}).items():
        by_meeting: dict[str, list] = defaultdict(list)
        for sample in _samples_of(entry):
            if sample.get("active") is False or len(sample.get("embedding", [])) != len(embedding):
                continue
            source = str(sample.get("source_file") or sample.get("audio_file") or sample.get("id") or "unknown")
            by_meeting[source].append(sample["embedding"])
        meeting_vectors = [
            max(vectors, key=lambda vector: cosine_similarity(embedding, vector))
            for vectors in by_meeting.values()
        ]
        if not meeting_vectors:
            continue
        similarities = sorted((cosine_similarity(embedding, vector) for vector in meeting_vectors), reverse=True)
        if scorer == "max":
            score = similarities[0]
        elif scorer == "top3_median":
            if len(similarities) < 3:
                continue
            score = float(np.median(similarities[:3]))
        elif scorer == "centroid":
            if len(meeting_vectors) < 3:
                continue
            centroid = np.asarray(meeting_vectors, dtype=np.float32).mean(axis=0)
            score = cosine_similarity(embedding, centroid)
        else:
            raise ValueError(f"Unsupported scorer: {scorer}")
        scores.append((name, float(score)))
    return sorted(scores, key=lambda item: item[1], reverse=True)


def evaluate_shadow_library(
    shadow_path: str | Path,
    transcripts_dir: str | Path,
    *,
    threshold: float = 0.70,
    min_margin: float = 0.04,
    model_key: str | None = None,
    model_path: str | Path | None = None,
    scorer: str = "max",
) -> dict:
    """Evaluate only user-verified, held-out sidecar clusters against a shadow library."""
    library = json.loads(Path(shadow_path).read_text(encoding="utf-8"))
    training_sources = {
        sample.get("source_file") for entry in library.get("speakers", {}).values()
        for sample in _samples_of(entry) if sample.get("source_file")
    }
    if model_key is None:
        recorded = str((library.get("provenance") or {}).get("embedding_model") or "")
        model_key = recorded if recorded in SPEAKER_EMBED_MODELS else None
    session = _get_speaker_embed_session(model_key, model_path)
    cases, failures = [], []
    for sidecar in sorted(Path(transcripts_dir).glob("*_diarized.json")):
        if str(sidecar.resolve()) in training_sources:
            continue
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        meta = data.get("speaker_meta") or {}
        names = data.get("speaker_names") or {}
        audio = _resolve_audio(sidecar, data)
        for speaker_id, name in names.items():
            state = meta.get(str(speaker_id)) or {}
            if state.get("source") != "user" or state.get("verified") is not True or name not in library["speakers"]:
                continue
            try:
                turns = [
                    LegacyTurn(float(seg["start"]), float(seg["end"]), name)
                    for seg in data.get("segments") or []
                    if str(seg.get("speaker_id")) == str(speaker_id) and float(seg.get("end", 0)) > float(seg.get("start", 0))
                ]
                candidate = _candidate_for_person(turns)
                if audio and audio.exists() and candidate is not None:
                    embedding, _dimension, _model = _extract_audio_embedding(
                        audio, segment_start=candidate["segment_start"], segment_end=candidate["segment_end"],
                        session=session, neural_model_version=model_key or _NEURAL_MODEL_VERSION,
                    )
                    embedding_source = "audio_clip"
                else:
                    embedding = (data.get("speaker_embeddings") or {}).get(str(speaker_id))
                    if embedding is None:
                        continue
                    embedding_source = "sidecar_embedding_fallback"
                scores = _scores(library, np.asarray(embedding, dtype=np.float32), scorer=scorer)
                if not scores:
                    continue
                best_name, best = scores[0]
                runner_up = scores[1][1] if len(scores) > 1 else -1.0
                # A stale diarizer centroid may be useful for investigation, but it
                # is never evidence that the new audio-embedding matcher is safe.
                prediction = best_name if (
                    embedding_source == "audio_clip"
                    and best >= threshold and best - runner_up >= min_margin
                ) else None
                cases.append({
                    "sidecar": str(sidecar), "speaker_id": str(speaker_id), "actual": name,
                    "prediction": prediction, "best_name": best_name, "best_score": round(best, 4),
                    "runner_up_score": round(runner_up, 4), "margin": round(best - runner_up, 4),
                    "embedding_source": embedding_source,
                })
            except Exception as exc:
                failures.append({"sidecar": str(sidecar), "speaker_id": str(speaker_id), "error": str(exc)})
    evaluated = len(cases)
    auto = [case for case in cases if case["prediction"]]
    correct = [case for case in auto if case["prediction"] == case["actual"]]
    return {
        "threshold": threshold, "min_margin": min_margin, "scorer": scorer, "evaluated": evaluated,
        "auto_decisions": len(auto), "correct_auto_decisions": len(correct),
        "incorrect_auto_decisions": len(auto) - len(correct), "abstentions": evaluated - len(auto),
        "top1_correct": sum(case["best_name"] == case["actual"] for case in cases),
        "audio_clip_cases": sum(case["embedding_source"] == "audio_clip" for case in cases),
        "fallback_embedding_cases": sum(case["embedding_source"] == "sidecar_embedding_fallback" for case in cases),
        "cases": cases, "failures": failures,
    }


def _canonical_turns(turns: list[LegacyTurn], aliases: dict[str, str]) -> list[LegacyTurn]:
    return [
        LegacyTurn(turn.start, turn.end, aliases.get(turn.name.casefold(), turn.name))
        for turn in turns
    ]


def _source_segments_for_replacement(sidecar: Path, data: dict) -> tuple[list[dict], str]:
    """Prefer word-timed Whisper turns; fall back to the current transcript."""
    whisper = sidecar.with_name(sidecar.stem.replace("_diarized", "_whisper") + ".json")
    try:
        raw = json.loads(whisper.read_text(encoding="utf-8"))
        if raw.get("segments"):
            return raw["segments"], "whisper"
    except (OSError, json.JSONDecodeError):
        pass
    return list(data.get("segments") or []), "current_sidecar"


def _replacement_for_export(
    sidecar: Path,
    export: Path,
    export_sha256: str,
    turns: list[LegacyTurn],
    aliases: dict[str, str],
) -> tuple[str, dict | None, dict]:
    """Prepare one sidecar replacement without writing it.

    The user-confirmed export is the attribution authority.  Existing user
    confirmations are preserved when they agree, but a disagreement is held
    for review rather than silently overwriting newer direct evidence.
    """
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return "unreadable_sidecar", None, {"error": str(exc)}
    turns = _canonical_turns(turns, aliases)
    export_names = {turn.name for turn in turns}
    names = data.get("speaker_names") or {}
    meta = data.get("speaker_meta") or {}
    current_verified = {
        aliases.get(str(name).casefold(), str(name))
        for speaker_id, name in names.items()
        if ((meta.get(str(speaker_id)) or {}).get("source") == "user"
            and (meta.get(str(speaker_id)) or {}).get("verified") is True)
    }
    conflicts = sorted(current_verified - export_names, key=str.casefold)
    if conflicts:
        return "held_user_verified_conflict", None, {"conflicting_names": conflicts}

    source_segments, segment_source = _source_segments_for_replacement(sidecar, data)
    assignments, assigned_seconds = _legacy_segment_assignments(source_segments, turns)
    total_turn_seconds = sum(max(0.0, turn.end - turn.start) for turn in turns)
    assigned_total = sum(max(0.0, float(segment["end"]) - float(segment["start"])) for segment in assignments)
    coverage = assigned_total / max(total_turn_seconds, 1e-9)
    details = {
        "segment_source": segment_source,
        "assigned_segments": len(assignments),
        "assigned_seconds": round(assigned_total, 3),
        "export_turn_seconds": round(total_turn_seconds, 3),
        "coverage": round(coverage, 3),
    }
    if assigned_total < 3.0 or coverage < 0.35:
        return "held_insufficient_timestamp_coverage", None, details

    names_in_order: list[str] = []
    for assignment in assignments:
        if assignment["name"] not in names_in_order:
            names_in_order.append(assignment["name"])
    name_to_id = {name: index for index, name in enumerate(names_in_order)}
    replacement = dict(data)
    replacement["segments"] = []
    for assignment in assignments:
        item = {key: value for key, value in assignment.items() if key not in {"name", "speaker", "speaker_id"}}
        item["speaker_id"] = name_to_id[assignment["name"]]
        item["speaker"] = assignment["name"]
        replacement["segments"].append(item)
    replacement["speaker_names"] = {str(identifier): name for name, identifier in name_to_id.items()}
    replacement["speaker_meta"] = {
        str(identifier): {
            "source": "human_archive_verified",
            "verified": True,
            "confidence": 1.0,
            "source_file": str(export.resolve()),
            "source_sha256": export_sha256,
        }
        for name, identifier in name_to_id.items()
    }
    # Old cluster centroids no longer correspond to the timestamp-derived
    # identities, so retaining them would recreate the Riley-class error.
    replacement.pop("speaker_embeddings", None)
    replacement.setdefault("speaker_lineage", {})["human_archive_verified"] = {
        "source_file": str(export.resolve()),
        "source_sha256": export_sha256,
        **details,
    }
    return "ready", replacement, details


def plan_sidecar_replacements(
    inventory: dict | str | Path,
    *,
    aliases: dict[str, str] | None = None,
) -> tuple[dict, list[tuple[Path, dict]]]:
    """Build a report and in-memory replacements for verified archive matches."""
    if not isinstance(inventory, dict):
        inventory = json.loads(Path(inventory).read_text(encoding="utf-8"))
    aliases = {str(key).casefold(): value for key, value in (aliases or {}).items()}
    report, replacements = {"generated_at": _now(), "operations": [], "summary": {}}, []
    counts = defaultdict(int)
    matches_by_sidecar: dict[str, list[dict]] = defaultdict(list)
    for meeting in inventory.get("meetings") or []:
        if meeting.get("status") == "matched":
            matches_by_sidecar[str(meeting["sidecar"])].append(meeting)

    for sidecar_name, matches in matches_by_sidecar.items():
        hashes = {str(match["export_sha256"]) for match in matches}
        if len(hashes) > 1:
            counts["held_competing_verified_exports"] += 1
            report["operations"].append({
                "status": "held_competing_verified_exports",
                "sidecar": sidecar_name,
                "exports": [str(match["export"]) for match in matches],
                "export_sha256": sorted(hashes),
            })
            continue

        meeting = matches[0]
        export, sidecar = Path(meeting["export"]), Path(sidecar_name)
        try:
            _created, turns = parse_legacy_transcript(export)
        except OSError as exc:
            status, replacement, details = "unreadable_export", None, {"error": str(exc)}
        else:
            status, replacement, details = _replacement_for_export(
                sidecar, export, meeting["export_sha256"], turns, aliases,
            )
        counts[status] += 1
        operation = {
            "status": status, "sidecar": str(sidecar), "export": str(export),
            "export_sha256": meeting["export_sha256"], **details,
        }
        if len(matches) > 1:
            operation["identical_export_copies"] = [str(match["export"]) for match in matches]
        report["operations"].append(operation)
        if status == "ready" and replacement is not None:
            replacements.append((sidecar, replacement))
    report["summary"] = dict(sorted(counts.items()))
    report["ready_count"] = len(replacements)
    return report, replacements


def apply_sidecar_replacements(
    inventory: dict | str | Path,
    *,
    snapshot_dir: str | Path,
    aliases: dict[str, str] | None = None,
) -> dict:
    """Snapshot then atomically apply the ready human-verified replacements."""
    report, replacements = plan_sidecar_replacements(inventory, aliases=aliases)
    snapshot = Path(snapshot_dir).expanduser().resolve()
    snapshot.mkdir(parents=True, exist_ok=False)
    snapshot_sidecars = snapshot / "sidecars"
    snapshot_sidecars.mkdir()
    for sidecar, replacement in replacements:
        shutil.copy2(sidecar, snapshot_sidecars / sidecar.name)
        markdown = sidecar.with_name(sidecar.stem.replace("_diarized", "") + ".md")
        srt = srt_path_for(markdown)
        for related in (markdown, srt):
            if related.exists():
                shutil.copy2(related, snapshot_sidecars / related.name)
        _atomic_write(sidecar, replacement)
        _refresh_markdown(markdown, replacement)
        try:
            write_srt(srt, diarized_result=replacement)
        except OSError:
            pass
    report.update({"applied": True, "applied_count": len(replacements), "snapshot_dir": str(snapshot)})
    _atomic_write(snapshot / "apply-report.json", report)
    return report


def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inventory = sub.add_parser("inventory", help="Build a no-write human evidence manifest")
    inventory.add_argument("--exports", required=True)
    inventory.add_argument("--transcripts", required=True)
    inventory.add_argument("--report", required=True)
    inventory.add_argument("--alias-file", help="Explicit observed-label to canonical-name mapping")
    shadow = sub.add_parser("shadow", help="Build an isolated voice library from an evidence manifest")
    shadow.add_argument("--manifest", required=True)
    shadow.add_argument("--output", required=True)
    shadow.add_argument("--max-active-samples", type=int, default=_MAX_SAMPLES)
    shadow.add_argument("--dry-run", action="store_true")
    shadow.add_argument("--start", type=int, default=0, help="Zero-based manifest candidate offset")
    shadow.add_argument("--limit", type=int, help="Maximum candidates to embed in this shard")
    shadow.add_argument(
        "--model",
        choices=("titanet", "campp", "eres2net", "wespeaker_resnet293", "wavlm_base_plus_sv"),
        default="titanet",
    )
    shadow.add_argument("--model-path", help="Explicit isolated ONNX model path")
    merge = sub.add_parser("merge", help="Merge isolated shadow-library shards")
    merge.add_argument("--input", action="append", required=True, help="Shadow shard path; repeat")
    merge.add_argument("--output", required=True)
    merge.add_argument("--max-active-samples", type=int, default=_MAX_SAMPLES)
    evaluate = sub.add_parser("evaluate", help="Evaluate a shadow library against held-out user confirmations")
    evaluate.add_argument("--shadow", required=True)
    evaluate.add_argument("--transcripts", required=True)
    evaluate.add_argument("--report", required=True)
    evaluate.add_argument(
        "--model",
        choices=("titanet", "campp", "eres2net", "wespeaker_resnet293", "wavlm_base_plus_sv"),
    )
    evaluate.add_argument("--model-path", help="Explicit isolated ONNX model path")
    evaluate.add_argument("--scorer", choices=("max", "top3_median", "centroid"), default="max")
    sidecars = sub.add_parser("sidecar-plan", help="Report verified-export sidecar replacements without writing")
    sidecars.add_argument("--manifest", required=True)
    sidecars.add_argument("--report", required=True)
    sidecars.add_argument("--alias-file")
    apply_sidecars = sub.add_parser("apply-sidecars", help="Snapshot and apply ready verified-export sidecar replacements")
    apply_sidecars.add_argument("--manifest", required=True)
    apply_sidecars.add_argument("--snapshot-dir", required=True)
    apply_sidecars.add_argument("--report", required=True)
    apply_sidecars.add_argument("--alias-file")
    args = parser.parse_args()
    if args.command == "inventory":
        result = build_human_archive_inventory(
            args.exports, args.transcripts,
            aliases=load_backfill_aliases(args.alias_file) if args.alias_file else None,
        )
        path = Path(args.report).expanduser().resolve()
        _atomic_write(path, result)
        result = {"ok": True, "summary": result["summary"], "report_path": str(path)}
    elif args.command == "shadow":
        result = build_shadow_library(
            args.manifest, output_path=args.output, max_active_samples=max(1, args.max_active_samples),
            dry_run=args.dry_run, start=args.start, limit=args.limit,
            model_key=args.model, model_path=args.model_path,
        )
        failure_count = len(result.get("failures", []))
        result = {
            key: value for key, value in result.items()
            if key not in {"candidates", "failures"}
        }
        result.update({"ok": True, "failure_count": failure_count})
    elif args.command == "merge":
        result = merge_shadow_libraries(args.input, output_path=args.output, max_active_samples=max(1, args.max_active_samples))
        result["ok"] = True
    elif args.command == "sidecar-plan":
        report, _replacements = plan_sidecar_replacements(
            args.manifest, aliases=load_backfill_aliases(args.alias_file) if args.alias_file else None,
        )
        path = Path(args.report).expanduser().resolve()
        _atomic_write(path, report)
        result = {"ok": True, "summary": report["summary"], "ready_count": report["ready_count"], "report_path": str(path)}
    elif args.command == "apply-sidecars":
        report = apply_sidecar_replacements(
            args.manifest, snapshot_dir=args.snapshot_dir,
            aliases=load_backfill_aliases(args.alias_file) if args.alias_file else None,
        )
        path = Path(args.report).expanduser().resolve()
        _atomic_write(path, report)
        result = {"ok": True, "summary": report["summary"], "applied_count": report["applied_count"], "snapshot_dir": report["snapshot_dir"], "report_path": str(path)}
    else:
        result = evaluate_shadow_library(
            args.shadow, args.transcripts, model_key=args.model,
            model_path=args.model_path, scorer=args.scorer,
        )
        path = Path(args.report).expanduser().resolve()
        _atomic_write(path, result)
        result = {key: value for key, value in result.items() if key not in {"cases", "failures"}}
        result.update({"ok": True, "report_path": str(path)})
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    _main()
