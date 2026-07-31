"""Split timestamped transcript artifacts alongside a split audio recording."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _split_segments(segments: list[dict], split_at: float, second: bool) -> list[dict]:
    """Keep a side of ``segments`` and rebase the latter side to zero.

    Word timings are the source of truth.  A turn spanning the boundary is
    divided at its word boundary, rather than copied into both meetings.
    """
    result: list[dict] = []
    for original in segments:
        words = original.get("words")
        if isinstance(words, list):
            selected = []
            for word in words:
                start, end = _number(word.get("start")), _number(word.get("end"))
                if start is None or end is None:
                    continue
                keep = start >= split_at if second else end <= split_at
                if keep:
                    word = copy.deepcopy(word)
                    if second:
                        word["start"] = start - split_at
                        word["end"] = end - split_at
                    selected.append(word)
            if not selected:
                continue
            segment = copy.deepcopy(original)
            segment["words"] = selected
            segment["start"] = selected[0]["start"]
            segment["end"] = selected[-1]["end"]
            segment["text"] = " ".join(str(word.get("word", "")).strip() for word in selected).strip()
            if segment["text"]:
                result.append(segment)
            continue

        start, end = _number(original.get("start")), _number(original.get("end"))
        if start is None or end is None:
            continue
        keep = start >= split_at if second else end <= split_at
        if not keep:
            continue
        segment = copy.deepcopy(original)
        if second:
            segment["start"] = start - split_at
            segment["end"] = end - split_at
        result.append(segment)
    return result


def split_transcript_payload(payload: dict, split_at: float, second: bool, audio_path: str) -> dict:
    """Return one valid sidecar payload for one side of a recording split."""
    output = copy.deepcopy(payload)
    output["audio_file"] = audio_path
    output["segments"] = _split_segments(payload.get("segments", []), split_at, second)
    for key in ("duration", "duration_s"):
        duration = _number(payload.get(key))
        if duration is not None:
            output[key] = max(0.0, duration - split_at) if second else min(duration, split_at)
    return output


def write_split_artifacts(
    source_audio: Path,
    first_audio: Path,
    second_audio: Path,
    transcript_dir: Path,
    split_at: float,
) -> tuple[Path | None, Path | None]:
    """Write JSON/Markdown/SRT artifacts for two audio files.

    Existing summaries intentionally are not copied: they describe the two
    meetings together and should be generated independently.
    """
    import json
    from shared.srt_writer import srt_path_for, write_srt
    from shared.transcript_writer import write_transcript

    source_stem = source_audio.stem
    diarized_path = transcript_dir / f"{source_stem}_diarized.json"
    whisper_path = transcript_dir / f"{source_stem}_whisper.json"
    diarized = json.loads(diarized_path.read_text()) if diarized_path.exists() else None
    whisper = json.loads(whisper_path.read_text()) if whisper_path.exists() else None

    outputs: list[Path | None] = []
    for second, audio in ((False, first_audio), (True, second_audio)):
        stem = audio.stem
        d = split_transcript_payload(diarized, split_at, second, str(audio)) if diarized else None
        w = split_transcript_payload(whisper, split_at, second, str(audio)) if whisper else None
        if d:
            (transcript_dir / f"{stem}_diarized.json").write_text(json.dumps(d, indent=2) + "\n")
        if w:
            (transcript_dir / f"{stem}_whisper.json").write_text(json.dumps(w, indent=2) + "\n")
        if not d and not w:
            outputs.append(None)
            continue
        md = transcript_dir / f"{stem}.md"
        duration = split_at if not second else None
        write_transcript(
            md, " ".join(s.get("text", "") for s in (d or w).get("segments", [])),
            source_path=audio, model="split-existing-transcript", duration_s=duration,
            diarized_result=d, whisper_segments=(w or {}).get("segments"),
        )
        write_srt(srt_path_for(md), diarized_result=d, whisper_segments=(w or {}).get("segments"))
        outputs.append(md)
    return tuple(outputs)  # type: ignore[return-value]
