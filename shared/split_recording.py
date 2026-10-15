"""Split timestamped transcript artifacts alongside a split audio recording."""
from __future__ import annotations

import copy
import math
from pathlib import Path

from shared.asr_sidecar import find_raw_asr, raw_asr_write_path
from typing import Any


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _crop_segments(segments: list[dict], lo: float, hi: float) -> list[dict]:
    """Keep what lies within ``[lo, hi]`` of ``segments``, rebased so ``lo`` is zero.

    Word timings are the source of truth.  A turn spanning a boundary is
    divided at its word boundary, so no word is copied into both sides of a
    split, and a trim drops the words whose audio it cut.
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
                if start >= lo and end <= hi:
                    word = copy.deepcopy(word)
                    word["start"] = start - lo
                    word["end"] = end - lo
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
        if not (start >= lo and end <= hi):
            continue
        segment = copy.deepcopy(original)
        segment["start"] = start - lo
        segment["end"] = end - lo
        result.append(segment)
    return result


def _split_segments(segments: list[dict], split_at: float, second: bool) -> list[dict]:
    """Keep a side of ``segments`` and rebase the latter side to zero."""
    if second:
        return _crop_segments(segments, split_at, math.inf)
    return _crop_segments(segments, 0.0, split_at)


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


def trim_transcript_payload(payload: dict, start: float, end: float) -> dict:
    """Return the sidecar payload for audio trimmed to ``[start, end]``."""
    output = copy.deepcopy(payload)
    output["segments"] = _crop_segments(payload.get("segments", []), start, end)
    for key in ("duration", "duration_s"):
        duration = _number(payload.get(key))
        if duration is not None:
            output[key] = max(0.0, min(duration, end) - start)
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
    whisper_path = find_raw_asr(transcript_dir / f"{source_stem}.json")
    diarized = json.loads(diarized_path.read_text()) if diarized_path.exists() else None
    whisper = json.loads(whisper_path.read_text()) if whisper_path else None

    outputs: list[Path | None] = []
    for second, audio in ((False, first_audio), (True, second_audio)):
        stem = audio.stem
        d = split_transcript_payload(diarized, split_at, second, str(audio)) if diarized else None
        w = split_transcript_payload(whisper, split_at, second, str(audio)) if whisper else None
        if d:
            (transcript_dir / f"{stem}_diarized.json").write_text(json.dumps(d, indent=2) + "\n")
        if w:
            raw_asr_write_path(transcript_dir / f"{stem}.json").write_text(
                json.dumps(w, indent=2) + "\n")
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


def write_trimmed_artifacts(
    audio: Path,
    transcript_dir: Path,
    start: float,
    end: float,
) -> Path | None:
    """Crop a recording's JSON/Markdown/SRT artifacts in place to match its trimmed audio.

    The audio has already been cut to ``[start, end]`` of the original, so
    everything outside that range is dropped and the rest rebased to zero.
    Returns the rewritten Markdown, or None when the recording had no
    transcript.  The summary is not touched: it describes the untrimmed
    meeting and is regenerated separately.
    """
    import json
    from shared.srt_writer import srt_path_for, write_srt
    from shared.transcript_writer import parse_frontmatter, write_transcript

    stem = audio.stem
    diarized_path = transcript_dir / f"{stem}_diarized.json"
    whisper_path = find_raw_asr(transcript_dir / f"{stem}.json")
    diarized = json.loads(diarized_path.read_text()) if diarized_path.exists() else None
    whisper = json.loads(whisper_path.read_text()) if whisper_path else None
    if not diarized and not whisper:
        return None

    d = trim_transcript_payload(diarized, start, end) if diarized else None
    w = trim_transcript_payload(whisper, start, end) if whisper else None
    if d:
        diarized_path.write_text(json.dumps(d, indent=2) + "\n")
    if w:
        whisper_path.write_text(json.dumps(w, indent=2) + "\n")

    md = transcript_dir / f"{stem}.md"
    # Keep the model the transcript was made with; the trim changed its extent, not its source.
    model = "trim-existing-transcript"
    if md.exists():
        try:
            meta, _ = parse_frontmatter(md.read_text(encoding="utf-8"))
            model = str(meta.get("model") or model)
        except Exception:  # noqa: BLE001 - frontmatter is best-effort
            pass
    write_transcript(
        md, " ".join(s.get("text", "") for s in (d or w).get("segments", [])),
        source_path=audio, model=model,
        diarized_result=d, whisper_segments=(w or {}).get("segments"),
    )
    write_srt(srt_path_for(md), diarized_result=d, whisper_segments=(w or {}).get("segments"))
    return md
