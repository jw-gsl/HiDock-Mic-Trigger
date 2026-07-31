"""Locate the raw-ASR sidecar without naming a specific model.

The raw transcript sidecar has always been called `<stem>_whisper.json`, but
Whisper stopped being the transcriber: `pipeline_backends.json` selects Parakeet,
and `transcribe_parakeet.py` writes Parakeet segments under the Whisper name. The
file therefore misdescribes its own contents, which invites exactly the wrong
conclusion when someone is reasoning about which model produced a transcript.

New writes use the model-agnostic `_asr.json`. Reads accept either, preferring the
new name — there are ~1,500 existing sidecars on disk and renaming them would be a
migration with no upside, so both names are supported indefinitely.

Every caller goes through here rather than concatenating a suffix. A missed read
site would not raise: it would silently fall back to "no raw transcript
available", degrading re-diarisation quality with no error. One chokepoint is what
makes that failure mode impossible to reintroduce.
"""
from __future__ import annotations

from pathlib import Path

RAW_ASR_SUFFIX = "_asr.json"
LEGACY_RAW_ASR_SUFFIX = "_whisper.json"

# Stem suffixes a transcript path may carry that are not part of the recording's
# identity, longest first so `_diarized` wins before a bare stem match.
_TRANSCRIPT_STEM_SUFFIXES = ("_diarized", "_asr", "_whisper")


def recording_stem(path: str | Path) -> str:
    """The recording's base stem, given any of its sidecar paths.

    `Rec79_diarized.json`, `Rec79_asr.json`, `Rec79_whisper.json` and `Rec79.md`
    all describe the same recording and must resolve to `Rec79`.
    """
    stem = Path(path).stem
    for suffix in _TRANSCRIPT_STEM_SUFFIXES:
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def raw_asr_write_path(path: str | Path) -> Path:
    """Where a *new* raw-ASR sidecar should be written, for any sidecar path."""
    source = Path(path)
    return source.with_name(f"{recording_stem(source)}{RAW_ASR_SUFFIX}")


def raw_asr_candidates(path: str | Path) -> list[Path]:
    """Both possible raw-ASR paths, current name first."""
    source = Path(path)
    stem = recording_stem(source)
    return [
        source.with_name(f"{stem}{RAW_ASR_SUFFIX}"),
        source.with_name(f"{stem}{LEGACY_RAW_ASR_SUFFIX}"),
    ]


def find_raw_asr(path: str | Path) -> Path | None:
    """The existing raw-ASR sidecar for a recording, or None.

    Prefers the model-agnostic name so a re-transcribe supersedes a legacy file
    rather than being shadowed by it.
    """
    for candidate in raw_asr_candidates(path):
        if candidate.exists():
            return candidate
    return None


def has_raw_asr(path: str | Path) -> bool:
    return find_raw_asr(path) is not None
