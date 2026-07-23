"""Restore speaker names from the older HiDock text-export archive.

Some historical exports contain timestamped, named turns while the matching
canonical diarized sidecar only contains ``Speaker N`` labels.  This module
matches those exports to sidecars by creation time and transfers names using
timestamp overlap.  It is intentionally a separate, reportable operation:
generic or ambiguous evidence is left untouched.
"""
from __future__ import annotations

import argparse
import json
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from shared.speaker_meta import is_generic_name
from shared.srt_writer import srt_path_for, write_srt
from shared.transcript_writer import format_diarized_transcript


_CREATED_RE = re.compile(r"Creation Time:\s*(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}(?::\d{2})?)")
_TURN_RE = re.compile(
    r"^\s*(\d{1,2}:\d{2}:\d{2})\s*-\s*(\d{1,2}:\d{2}:\d{2})\s+(.+?):\s*$",
    re.MULTILINE,
)
_MARKDOWN_TURN_RE = re.compile(
    r"^\s*\[(\d{1,2}:\d{2}(?::\d{2})?)\s*-\s*(\d{1,2}:\d{2}(?::\d{2})?)\]\s+\*\*(.+?):\*\*(?:\s|$)",
    re.MULTILINE,
)
_RAW_NAME_RE = re.compile(
    r"^(?P<year>\d{4})(?P<month>[A-Za-z]{3})(?P<day>\d{2})-"
    r"(?P<hour>\d{2})(?P<minute>\d{2})(?P<second>\d{2})-"
)
_RECORDING_ID_RE = re.compile(r"\b(?:Rec|HiD)\d+\b", re.IGNORECASE)
_GENERIC_LEGACY_NAMES = {"unknown speaker", "unknown"}
_MIN_OVERLAP_SECONDS = 0.75
_MAX_MATCH_DELTA_SECONDS = 5 * 60
_MIN_SEGMENT_LABEL_OVERLAP = 0.20


@dataclass(frozen=True)
class LegacyTurn:
    start: float
    end: float
    name: str


def _timestamp(value: str) -> float:
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        hours, minutes, seconds = 0, parts[0], parts[1]
    else:
        hours, minutes, seconds = parts
    return float(hours * 3600 + minutes * 60 + seconds)


def _legacy_name_is_generic(name: str) -> bool:
    cleaned = " ".join(name.split())
    return is_generic_name(cleaned) or cleaned.casefold() in _GENERIC_LEGACY_NAMES


def parse_legacy_transcript(path: str | Path) -> tuple[datetime | None, list[LegacyTurn]]:
    """Parse creation time and named timestamped turns from an export."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    created_match = _CREATED_RE.search(text)
    created = None
    if created_match:
        value = created_match.group(1)
        created = datetime.strptime(value, "%Y/%m/%d %H:%M:%S" if value.count(":") == 2 else "%Y/%m/%d %H:%M")

    turns = []
    for match in _TURN_RE.finditer(text):
        start = _timestamp(match.group(1))
        end = _timestamp(match.group(2))
        name = " ".join(match.group(3).split())
        if end <= start or _legacy_name_is_generic(name):
            continue
        turns.append(LegacyTurn(start, end, name))
    return created, turns


def parse_human_named_transcript(path: str | Path) -> tuple[datetime | None, list[LegacyTurn]]:
    """Parse human speaker names from either an old export or a HiDock .md.

    The app's current markdown format uses ``[00:00-00:04] **Alice:**``;
    older exports use ``00:00:00 - 00:00:04 Alice:``. Generic labels are
    ignored so this only returns human naming evidence.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    created_match = _CREATED_RE.search(text)
    created = None
    if created_match:
        value = created_match.group(1)
        created = datetime.strptime(
            value,
            "%Y/%m/%d %H:%M:%S" if value.count(":") == 2 else "%Y/%m/%d %H:%M",
        )

    turns: list[LegacyTurn] = []
    for match in _TURN_RE.finditer(text):
        start = _timestamp(match.group(1))
        end = _timestamp(match.group(2))
        name = " ".join(match.group(3).split())
        if end > start and not _legacy_name_is_generic(name):
            turns.append(LegacyTurn(start, end, name))
    for match in _MARKDOWN_TURN_RE.finditer(text):
        start = _timestamp(match.group(1))
        end = _timestamp(match.group(2))
        name = " ".join(match.group(3).split())
        if end > start and not _legacy_name_is_generic(name):
            turns.append(LegacyTurn(start, end, name))
    return created, turns


def _raw_datetime(path: Path) -> datetime | None:
    match = _RAW_NAME_RE.match(path.stem)
    if not match:
        return None
    try:
        return datetime.strptime(
            "{year}{month}{day}{hour}{minute}{second}".format(**match.groupdict()),
            "%Y%b%d%H%M%S",
        )
    except ValueError:
        return None


def _recording_id(path: Path) -> str | None:
    match = _RECORDING_ID_RE.search(path.name)
    return match.group(0).casefold() if match else None


def _match_sidecar(
    export_path: Path,
    created: datetime,
    sidecars: list[Path],
) -> Path | None:
    candidates = [(abs((_raw_datetime(path) - created).total_seconds()), path)
                  for path in sidecars if _raw_datetime(path) is not None
                  and _raw_datetime(path).date() == created.date()]
    if not candidates:
        return None

    export_id = _recording_id(export_path)
    if export_id:
        identified = [(delta, path) for delta, path in candidates if export_id in path.name.casefold()]
        if identified:
            candidates = identified

    delta, path = min(candidates, key=lambda item: (item[0], item[1].name))
    return path if delta <= _MAX_MATCH_DELTA_SECONDS else None


def _overlap(left_start: float, left_end: float, right_start: float, right_end: float) -> float:
    return max(0.0, min(left_end, right_end) - max(left_start, right_start))


def _speaker_name_map(data: dict, turns: list[LegacyTurn]) -> dict[str, tuple[str, float]]:
    """Choose the strongest imported name for each diarized speaker cluster."""
    scores: dict[str, dict[str, float]] = {}
    name_totals: dict[str, float] = {}
    for turn in turns:
        name_totals[turn.name] = name_totals.get(turn.name, 0.0) + turn.end - turn.start
        for segment in data.get("segments", []):
            try:
                speaker_id = str(segment.get("speaker_id", ""))
                start = float(segment.get("start", 0.0))
                end = float(segment.get("end", 0.0))
            except (TypeError, ValueError):
                continue
            overlap = _overlap(turn.start, turn.end, start, end)
            if overlap <= 0:
                continue
            scores.setdefault(speaker_id, {})[turn.name] = (
                scores.setdefault(speaker_id, {}).get(turn.name, 0.0) + overlap
            )

    result: dict[str, tuple[str, float]] = {}
    for speaker_id, names in scores.items():
        name, overlap = max(names.items(), key=lambda item: item[1])
        coverage = overlap / max(name_totals.get(name, 0.0), 1e-9)
        if overlap >= _MIN_OVERLAP_SECONDS and coverage >= 0.25:
            result[speaker_id] = (name, coverage)
    return result


def _has_verified_labels(data: dict) -> bool:
    return any(
        (meta or {}).get("verified") is True
        for meta in (data.get("speaker_meta") or {}).values()
    )


def _legacy_segment_assignments(
    whisper_segments: list[dict],
    turns: list[LegacyTurn],
) -> tuple[list[dict], dict[str, float]]:
    """Assign Whisper segments to named imported turns by timestamp overlap.

    The older text export contains human-readable turn boundaries while the
    diarizer may have collapsed both voices into one acoustic cluster.  Using
    those boundaries lets us repair speaker attribution without rerunning ASR.
    A Whisper segment is assigned only when a named turn covers a meaningful
    portion of it; this leaves silence/gaps conservative rather than inventing
    a speaker.
    """
    assignments: list[dict] = []
    assigned_seconds: dict[str, float] = {}
    for raw in whisper_segments:
        try:
            start = float(raw.get("start", 0.0))
            end = float(raw.get("end", 0.0))
        except (TypeError, ValueError):
            continue
        if end <= start or not str(raw.get("text", "")).strip():
            continue

        candidates = [
            (turn.name, _overlap(start, end, turn.start, turn.end))
            for turn in turns
        ]
        name, overlap = max(candidates, key=lambda item: item[1], default=(None, 0.0))
        duration = end - start
        if name is None or overlap < _MIN_OVERLAP_SECONDS or overlap / duration < _MIN_SEGMENT_LABEL_OVERLAP:
            continue
        assignment = dict(raw)
        assignment.update({
            "start": start,
            "end": end,
            "text": str(raw.get("text", "")).strip(),
            "name": name,
        })
        assignments.append(assignment)
        assigned_seconds[name] = assigned_seconds.get(name, 0.0) + duration
    return assignments, assigned_seconds


def find_human_label_source(
    sidecar_path: str | Path,
    transcript_path: str | Path | None = None,
    exports_dir: str | Path | None = None,
) -> tuple[Path, list[LegacyTurn]] | None:
    """Find the strongest historical human naming source for one meeting.

    The canonical markdown transcript is checked first because it may contain
    names typed or corrected in the app. If it has no named turns, look for a
    matching named export in ``~/Downloads/HiDock Files`` using the same
    timestamp/recording-ID matching rules as the recovery report.
    """
    candidates: list[Path] = []
    if transcript_path is not None:
        candidates.append(Path(transcript_path))
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            _created, turns = parse_human_named_transcript(candidate)
        except OSError:
            continue
        if turns:
            return candidate, turns

    exports = Path(exports_dir) if exports_dir is not None else Path.home() / "Downloads" / "HiDock Files"
    sidecar = Path(sidecar_path)
    if not exports.exists():
        return None
    for export_path in sorted(exports.glob("*.txt")):
        try:
            created, turns = parse_legacy_transcript(export_path)
        except OSError:
            continue
        if created is None or not turns:
            continue
        if _match_sidecar(export_path, created, [sidecar]) == sidecar:
            return export_path, turns
    return None


def build_human_label_anchor_result(
    whisper_segments: list[dict],
    turns: list[LegacyTurn],
    *,
    source_file: str | Path,
) -> dict | None:
    """Build a label-only result suitable for timestamp preservation.

    This does not replace the new diarization. It gives the preservation layer
    timestamped human anchors so a new cluster can inherit a previous name.
    Any word arrays on the current ASR segments are retained for provenance.
    """
    assignments, assigned_seconds = _legacy_segment_assignments(whisper_segments, turns)
    if not assignments:
        return None
    total_whisper_time = sum(
        max(0.0, float(segment["end"]) - float(segment["start"]))
        for segment in assignments
    )
    total_turn_time = sum(max(0.0, turn.end - turn.start) for turn in turns)
    if total_whisper_time < 3.0 or total_whisper_time / max(total_turn_time, 1e-9) < 0.35:
        return None

    names_in_order: list[str] = []
    for assignment in assignments:
        if assignment["name"] not in names_in_order:
            names_in_order.append(assignment["name"])
    name_to_id = {name: index for index, name in enumerate(names_in_order)}
    speaker_names = {str(index): name for name, index in name_to_id.items()}
    speaker_meta = {}
    for name, speaker_id in name_to_id.items():
        source_duration = sum(turn.end - turn.start for turn in turns if turn.name == name)
        coverage = assigned_seconds.get(name, 0.0) / max(source_duration, 1e-9)
        speaker_meta[str(speaker_id)] = {
            "source": "legacy_import",
            "verified": False,
            "confidence": round(min(1.0, coverage), 3),
            "source_file": str(Path(source_file).resolve()),
        }

    segments = []
    for assignment in assignments:
        segment = {
            key: value for key, value in assignment.items()
            if key not in {"name", "speaker", "speaker_id"}
        }
        segment["speaker_id"] = name_to_id[assignment["name"]]
        segment["speaker"] = assignment["name"]
        segments.append(segment)
    return {
        "version": 1,
        "segments": segments,
        "speaker_names": speaker_names,
        "speaker_meta": speaker_meta,
        "speaker_lineage": {
            "legacy_import_timestamps": {
                "source_file": str(Path(source_file).resolve()),
                "assigned_segments": len(assignments),
                "assigned_seconds": round(total_whisper_time, 3),
                "source_turns": len(turns),
            }
        },
    }


def restore_legacy_timed_segments(
    data: dict,
    whisper_data: dict,
    turns: list[LegacyTurn],
    *,
    export_path: str | Path,
) -> dict | None:
    """Rebuild speaker assignments from a timestamped legacy transcript.

    Returns a replacement diarized sidecar, or ``None`` when the evidence is
    not safe to apply. Confirmed user labels are never overwritten. Imported
    names remain unverified so the normal speaker-confirmation workflow still
    teaches the voice library.
    """
    if _has_verified_labels(data) or not turns:
        return None
    whisper_segments = whisper_data.get("segments") or []
    assignments, assigned_seconds = _legacy_segment_assignments(whisper_segments, turns)
    if not assignments:
        return None

    # Require more than a token fragment of named evidence. This prevents a
    # loosely matched export from replacing a complete sidecar accidentally.
    total_whisper_time = sum(max(0.0, float(seg["end"]) - float(seg["start"])) for seg in assignments)
    total_turn_time = sum(max(0.0, turn.end - turn.start) for turn in turns)
    if total_whisper_time < 3.0 or total_whisper_time / max(total_turn_time, 1e-9) < 0.35:
        return None

    names_in_order: list[str] = []
    for item in assignments:
        if item["name"] not in names_in_order:
            names_in_order.append(item["name"])
    name_to_id = {name: index for index, name in enumerate(names_in_order)}
    speaker_names = {str(index): name for name, index in name_to_id.items()}
    speaker_meta = {}
    for name, speaker_id in name_to_id.items():
        source_duration = sum(turn.end - turn.start for turn in turns if turn.name == name)
        coverage = assigned_seconds.get(name, 0.0) / max(source_duration, 1e-9)
        speaker_meta[str(speaker_id)] = {
            "source": "legacy_import",
            "verified": False,
            "confidence": round(min(1.0, coverage), 3),
            "source_file": str(Path(export_path).resolve()),
        }

    replacement = dict(data)
    replacement["segments"] = []
    for item in assignments:
        speaker_id = name_to_id[item["name"]]
        replacement["segments"].append({
            "start": item["start"],
            "end": item["end"],
            "text": item["text"],
            "speaker_id": speaker_id,
            "speaker": item["name"],
        })
    replacement["speaker_names"] = speaker_names
    replacement["speaker_meta"] = speaker_meta
    # Embeddings belonged to the collapsed diarizer clusters and cannot be
    # safely carried to the new timestamp-derived identities.
    replacement.pop("speaker_embeddings", None)
    replacement["speaker_lineage"] = {
        "legacy_import_timestamps": {
            "source_file": str(Path(export_path).resolve()),
            "assigned_segments": len(assignments),
            "assigned_seconds": round(total_whisper_time, 3),
            "source_turns": len(turns),
        }
    }
    replacement["legacy_import_recovered"] = True
    return replacement


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _refresh_markdown(path: Path, data: dict) -> None:
    """Replace only the transcript section, preserving existing summaries."""
    if not path.exists():
        return
    original = path.read_text(encoding="utf-8", errors="replace")
    marker = "## Transcript"
    marker_index = original.find(marker)
    if marker_index < 0:
        return
    body_start = marker_index + len(marker)
    remainder = original[body_start:]
    next_section = re.search(r"\n## [^\n]+", remainder)
    body_end = body_start + (next_section.start() if next_section else len(remainder))
    body = format_diarized_transcript(data)
    updated = original[:body_start] + "\n\n" + body + original[body_end:]
    if updated != original:
        path.write_text(updated, encoding="utf-8")


def restore_legacy_labels(
    exports_dir: str | Path,
    transcripts_dir: str | Path,
    *,
    apply: bool = False,
) -> dict:
    """Report or apply names recovered from the historical text exports."""
    exports = Path(exports_dir)
    transcripts = Path(transcripts_dir)
    sidecars = sorted(transcripts.glob("*_diarized.json"))
    report = {
        "exports": 0,
        "named_exports": 0,
        "matched_sidecars": 0,
        "changed_sidecars": 0,
        "restored_speakers": 0,
        "recovered_timed_sidecars": 0,
        "recovered_timed_segments": 0,
        "unmatched_named_exports": [],
        "changes": [],
        "applied": apply,
    }

    for export_path in sorted(exports.glob("*.txt")):
        report["exports"] += 1
        try:
            created, turns = parse_legacy_transcript(export_path)
        except OSError:
            continue
        if not turns:
            continue
        report["named_exports"] += 1
        if created is None:
            report["unmatched_named_exports"].append(export_path.name)
            continue
        sidecar_path = _match_sidecar(export_path, created, sidecars)
        if sidecar_path is None:
            report["unmatched_named_exports"].append(export_path.name)
            continue
        report["matched_sidecars"] += 1
        try:
            data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        whisper_path = sidecar_path.with_name(
            sidecar_path.stem.replace("_diarized", "_whisper") + ".json"
        )
        try:
            whisper_data = json.loads(whisper_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            whisper_data = {}

        recovered = restore_legacy_timed_segments(
            data,
            whisper_data,
            turns,
            export_path=export_path,
        )
        if recovered is not None:
            data = recovered
            report["recovered_timed_sidecars"] += 1
            report["recovered_timed_segments"] += len(recovered.get("segments") or [])
            names = data.get("speaker_names") or {}
            restored = [
                {"speaker_id": str(speaker_id), "name": name}
                for speaker_id, name in names.items()
            ]
        else:
            proposed = _speaker_name_map(data, turns)
            names = data.setdefault("speaker_names", {})
            meta = data.setdefault("speaker_meta", {})
            restored = []
            for speaker_id, (name, coverage) in proposed.items():
                current = names.get(speaker_id)
                current_meta = meta.get(speaker_id) or {}
                # Verified labels and old named labels are protected. An
                # unverified automatic voice-library match is provisional and
                # may be corrected by stronger timestamped source evidence.
                if current and not is_generic_name(current):
                    if current_meta.get("verified") is True or not current_meta:
                        continue
                    if current_meta.get("source") not in {"auto", "legacy_import"}:
                        continue
                names[speaker_id] = name
                meta[speaker_id] = {
                    "source": "legacy_import",
                    "verified": False,
                    "confidence": round(coverage, 3),
                    "source_file": str(export_path.resolve()),
                }
                restored.append({"speaker_id": speaker_id, "name": name, "coverage": round(coverage, 3)})

        if not restored:
            continue
        for segment in data.get("segments", []):
            speaker_id = str(segment.get("speaker_id", ""))
            if speaker_id in names:
                segment["speaker"] = names[speaker_id]
        data.setdefault("speaker_lineage", {})["legacy_import"] = {
            "source_file": str(export_path.resolve()),
            "restored": restored,
        }
        report["changed_sidecars"] += 1
        report["restored_speakers"] += len(restored)
        report["changes"].append({
            "sidecar": str(sidecar_path),
            "export": str(export_path),
            "restored": restored,
        })
        if apply:
            _atomic_write_json(sidecar_path, data)
            _refresh_markdown(sidecar_path.with_name(sidecar_path.stem.replace("_diarized", "") + ".md"), data)
            write_srt(srt_path_for(sidecar_path.with_name(sidecar_path.stem.replace("_diarized", "") + ".md")), diarized_result=data)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exports", default=str(Path.home() / "Downloads" / "HiDock Files"))
    parser.add_argument("--transcripts", default=str(Path.home() / "HiDock" / "Raw Transcripts"))
    parser.add_argument("--apply", action="store_true", help="Write recovered labels; default is report-only")
    args = parser.parse_args()
    print(json.dumps(restore_legacy_labels(args.exports, args.transcripts, apply=args.apply), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
