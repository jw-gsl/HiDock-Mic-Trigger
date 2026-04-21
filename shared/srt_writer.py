"""SRT subtitle writer.

Turns a diarized transcript (or plain Whisper segments) into a standards-compliant
.srt file. Shares inputs with :func:`shared.transcript_writer.write_transcript`:
each segment is a dict with ``start`` / ``end`` (seconds) and ``text``; diarized
segments additionally carry ``speaker`` that is resolved against ``speaker_names``.

Use when:
- the user asks for captions for video
- downstream tooling expects timecoded blocks rather than markdown

Not emitted when there are no segments with timestamps — an SRT without timings
is meaningless, so we fail fast rather than writing garbage.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable


def _format_srt_timestamp(seconds: float) -> str:
    """Format seconds as ``HH:MM:SS,mmm`` per the SRT spec."""
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _iter_segments_with_timings(
    segments: Iterable[dict],
) -> Iterable[tuple[float, float, str | None, str]]:
    """Yield ``(start, end, speaker, text)`` for each usable segment.

    Skips segments without a usable time range or text. ``speaker`` is the raw
    speaker key from diarization (or None for non-diarized input); caller is
    responsible for resolving it to a display name.
    """
    for seg in segments:
        start = seg.get("start")
        end = seg.get("end")
        text = (seg.get("text") or "").strip()
        if start is None or end is None or not text:
            continue
        if end <= start:
            # Zero- or negative-duration segments are useless in SRT and some
            # players bail on the whole file. Nudge the end forward.
            end = start + 0.5
        yield float(start), float(end), seg.get("speaker"), text


def format_srt(
    diarized_result: dict | None = None,
    whisper_segments: list[dict] | None = None,
    *,
    include_speakers: bool = True,
) -> str:
    """Render an SRT document as a string.

    Args:
        diarized_result: A dict with ``segments`` and ``speaker_names`` keys
            (same shape :mod:`shared.transcript_writer` consumes).
        whisper_segments: Fallback when there is no diarization — plain Whisper
            segments with ``start`` / ``end`` / ``text``.
        include_speakers: Prepend ``Speaker: `` to each cue when we have
            diarized speakers. Turn off for pure caption output.

    Returns:
        SRT-formatted text. Empty string if there is nothing to render.
    """
    if diarized_result and diarized_result.get("segments"):
        segments = diarized_result["segments"]
        names = diarized_result.get("speaker_names") or {}
    elif whisper_segments:
        segments = whisper_segments
        names = {}
    else:
        return ""

    blocks: list[str] = []
    idx = 0
    for start, end, speaker, text in _iter_segments_with_timings(segments):
        idx += 1
        display = names.get(speaker, speaker) if speaker else None
        if include_speakers and display:
            cue_body = f"{display}: {text}"
        else:
            cue_body = text
        blocks.append(
            f"{idx}\n"
            f"{_format_srt_timestamp(start)} --> {_format_srt_timestamp(end)}\n"
            f"{cue_body}"
        )

    if not blocks:
        return ""
    # SRT convention: blocks separated by blank line, trailing newline.
    return "\n\n".join(blocks) + "\n"


def write_srt(
    output_path: Path,
    *,
    diarized_result: dict | None = None,
    whisper_segments: list[dict] | None = None,
    include_speakers: bool = True,
) -> Path | None:
    """Write an SRT file alongside a transcript.

    Returns the path if an SRT was written, or ``None`` if there were no usable
    timed segments (caller can log and move on).
    """
    body = format_srt(
        diarized_result=diarized_result,
        whisper_segments=whisper_segments,
        include_speakers=include_speakers,
    )
    if not body:
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(body, encoding="utf-8")
    return output_path


def srt_path_for(transcript_path: Path) -> Path:
    """Return the .srt path that pairs with a .md transcript path."""
    return transcript_path.with_suffix(".srt")


def _main() -> int:
    """CLI: regenerate an SRT from an existing _diarized.json or _whisper.json sidecar.

    Used by the desktop apps when a user asks to export SRT for a transcript
    that was produced before SRT auto-emit was added. The file at ``input`` is
    expected to contain ``segments`` (list of dicts with start/end/text) and
    optionally ``speaker_names`` for diarized output.
    """
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(description=_main.__doc__)
    parser.add_argument("input", type=Path, help="Path to _diarized.json or _whisper.json")
    parser.add_argument("output", type=Path, help="Path to write the .srt to")
    parser.add_argument(
        "--no-speakers",
        action="store_true",
        help="Omit speaker prefixes even if the input has them (plain captions).",
    )
    args = parser.parse_args()

    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"srt_writer: input not found: {args.input}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"srt_writer: invalid JSON in {args.input}: {exc}", file=sys.stderr)
        return 2

    # Diarized sidecars carry ``speaker_names``; Whisper-only sidecars do not.
    if "speaker_names" in data:
        result = write_srt(
            args.output,
            diarized_result=data,
            include_speakers=not args.no_speakers,
        )
    else:
        result = write_srt(args.output, whisper_segments=data.get("segments") or [])

    if result is None:
        print("srt_writer: no usable timed segments in input", file=sys.stderr)
        return 1
    print(str(result))
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised via subprocess
    raise SystemExit(_main())
