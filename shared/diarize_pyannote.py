"""pyannote.audio diarization backend.

Selected when Sortformer's 4-speaker cap would silently merge real
speakers, or when the user explicitly picks pyannote in the Model
Manager. Pyannote's pipeline auto-discovers the number of speakers
via clustering, with no hard cap.

Exposes `diarize(audio_path, whisper_segments, n_speakers) -> dict`
matching `shared.diarize_lite.diarize` and `shared.diarize_sortformer.diarize`
so the dispatcher can swap backends without code changes.

Raises ModuleNotFoundError at call time if `pyannote.audio` isn't
installed — keeps the module import-safe in envs without it, so
selecting a different diarizer stays functional.

Token resolution lives in `shared.models._read_hf_token` — same
order of precedence (env vars, gitignored config.json field, the
`huggingface-cli login` cache).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np


_PYANNOTE_PIPELINE = "pyannote/speaker-diarization-3.1"

# Cache the loaded pipeline so repeated calls in the same process
# (batch transcription, rediarize loops) don't re-download / re-init.
_pipeline_cache = None


def _load_pipeline():
    """Load pyannote's speaker-diarization-3.1 pipeline lazily.

    Reuses any cached instance. Resolves the HF token through the
    same helper used by the Model Manager so the install probe and
    the runtime use the same token. Raises ModuleNotFoundError if
    pyannote.audio isn't installed; raises RuntimeError with a clear
    message if the HF token is missing or the gated terms haven't
    been accepted.
    """
    global _pipeline_cache
    if _pipeline_cache is not None:
        return _pipeline_cache

    try:
        from pyannote.audio import Pipeline
    except ImportError as e:
        raise ModuleNotFoundError(
            "pyannote.audio is not installed. Install it via the Model "
            "Manager (pyannote row > Install) before selecting pyannote "
            "as the active diarization backend."
        ) from e

    from shared.models import _read_hf_token
    token = _read_hf_token()
    if not token:
        raise RuntimeError(
            "No HuggingFace token configured. Set HF_TOKEN in the shell, "
            "add `huggingface_token` to transcription-pipeline/config.json, "
            "or run `huggingface-cli login`."
        )

    pipeline = Pipeline.from_pretrained(_PYANNOTE_PIPELINE, token=token)
    if pipeline is None:
        raise RuntimeError(
            f"Pipeline.from_pretrained({_PYANNOTE_PIPELINE!r}) returned None. "
            "Most likely the model terms haven't been accepted — visit "
            f"https://huggingface.co/{_PYANNOTE_PIPELINE} and click "
            "'Agree and access repository'."
        )

    # CPU is the only fully-supported device for pyannote on Apple
    # Silicon as of pyannote.audio 4.x — MPS doesn't yet cover all
    # the ops in their conv1d stack. CPU performance is fine for
    # meeting-length audio.
    try:
        import torch
        pipeline.to(torch.device("cpu"))
    except Exception:
        pass

    _pipeline_cache = pipeline
    return pipeline


def _run_pipeline(pipeline, audio_path: Path, n_speakers: int | None):
    """Invoke pyannote on the audio file and return a list of
    (start_s, end_s, raw_speaker_id) tuples sorted by start time.

    `n_speakers` is honoured as a hint when truthy: pyannote accepts
    `num_speakers` to force a count, otherwise it auto-clusters.
    """
    kwargs: dict = {}
    if n_speakers and n_speakers > 0:
        kwargs["num_speakers"] = int(n_speakers)

    annotation = pipeline(str(audio_path), **kwargs)

    turns: list[tuple[float, float, str]] = []
    for segment, _track, speaker in annotation.itertracks(yield_label=True):
        turns.append((float(segment.start), float(segment.end), str(speaker)))
    turns.sort(key=lambda t: t[0])
    return turns


def diarize(
    audio_path: str | Path,
    whisper_segments: list[dict],
    n_speakers: int | None = None,
) -> dict:
    """Diarize with pyannote.audio.

    Signature and return shape match `shared.diarize_lite.diarize` so
    callers can swap backends without code changes. Returns a dict
    with `version`, `audio_file`, `segments` (each with `start`, `end`,
    `text`, `speaker`, `speaker_id`), `speaker_names` (id → display
    name), and `backend: "pyannote"`.
    """
    # Import helpers from the sortformer adapter — they're generic over
    # the turn shape (start, end, label) and don't actually care which
    # neural diarizer produced them. Keeping them in one place avoids
    # drift between the two adapters.
    from shared.audio_utils import load_audio
    from shared.diarize_lite import (
        _anonymize_non_speech,
        _split_long_segments,
        _MAX_MERGED_SEGMENT_SECONDS,
    )
    from shared.diarize_sortformer import (
        _assign_speakers_segment_level,
        _assign_speakers_word_level,
        _resolve_speaker_names,
    )

    audio_path = Path(audio_path)
    audio = load_audio(audio_path, sr=16000)
    total_dur = len(audio) / 16000.0

    pipeline = _load_pipeline()
    raw_turns = _run_pipeline(pipeline, audio_path, n_speakers)

    if not raw_turns:
        # Pyannote returned no speakers — fall through to a single-
        # speaker result rather than crashing the diarization stage.
        print("pyannote: no turns detected, returning single-speaker result", file=sys.stderr)
        segments_out = []
        for ws in whisper_segments:
            text = (ws.get("text") or "").strip()
            if not text:
                continue
            segments_out.append({
                "start": float(ws["start"]),
                "end": float(ws["end"]),
                "text": text,
                "speaker": "Speaker 1",
                "speaker_id": 0,
            })
        return {
            "version": 1,
            "audio_file": str(audio_path),
            "segments": segments_out,
            "speaker_names": {"0": "Speaker 1"},
            "backend": "pyannote",
        }

    # Normalise pyannote's "SPEAKER_00" / "SPEAKER_01" / … to stable
    # "Speaker 1" / "Speaker 2" labels in order of first appearance —
    # matches diarize_lite and diarize_sortformer's behaviour so
    # downstream consumers see one consistent label scheme.
    label_map: dict[str, str] = {}
    for _, _, spk in raw_turns:
        if spk not in label_map:
            label_map[spk] = f"Speaker {len(label_map) + 1}"
    renamed_turns = [(s, e, label_map[spk]) for s, e, spk in raw_turns]
    internal_labels = list(label_map.values())

    # Voice-library matching: per-speaker, concatenate up to 10s of
    # their longest turns and try identify_speaker against the user's
    # enrolled library. Parity with the Sortformer path.
    display_names = _resolve_speaker_names(audio, renamed_turns, internal_labels, sr=16000)

    # Word-level alignment when Whisper carries per-word timestamps;
    # falls back to whole-segment overlap otherwise (legacy cached
    # `_whisper.json` from before word_timestamps was enabled).
    has_word_timestamps = any((seg.get("words") or []) for seg in whisper_segments)
    if has_word_timestamps:
        raw_segments = _assign_speakers_word_level(whisper_segments, renamed_turns)
        align_mode = "word-level"
    else:
        raw_segments = _assign_speakers_segment_level(whisper_segments, renamed_turns)
        align_mode = "segment-level"

    # Build the integer speaker_id space + speaker_names dict that
    # downstream consumers (macOS viewer, rediarize stats, voice
    # library tagging) expect. Same scheme as diarize_lite + sortformer.
    label_to_id: dict[str, int] = {label: i for i, label in enumerate(internal_labels)}
    speaker_names: dict[str, str] = {
        str(spk_id): display_names.get(label, label)
        for label, spk_id in label_to_id.items()
    }

    for seg in raw_segments:
        label = seg.get("speaker") or internal_labels[0]
        spk_id = label_to_id.get(label, 0)
        seg["speaker_id"] = spk_id
        seg["speaker"] = speaker_names.get(str(spk_id), label)

    # Same post-processing as the other backends so the output shape
    # is identical: anonymise non-speech tokens, merge consecutive
    # same-speaker runs, cap long blocks.
    raw_segments = _anonymize_non_speech(raw_segments)

    segments_out: list[dict] = []
    for seg in raw_segments:
        if not seg.get("text"):
            continue
        if segments_out and segments_out[-1]["speaker_id"] == seg["speaker_id"]:
            segments_out[-1]["end"] = seg["end"]
            segments_out[-1]["text"] = (segments_out[-1]["text"] + " " + seg["text"]).strip()
        else:
            segments_out.append(dict(seg))

    segments_out = _split_long_segments(segments_out, max_duration=_MAX_MERGED_SEGMENT_SECONDS)
    segments_out = _split_long_segments(segments_out, max_duration=_MAX_MERGED_SEGMENT_SECONDS)

    max_dur = max((s["end"] - s["start"] for s in segments_out), default=0)
    print(
        f"pyannote: {len(renamed_turns)} turns, {len(internal_labels)} speakers, "
        f"{total_dur:.0f}s audio, {align_mode} alignment, "
        f"{len(segments_out)} output segments (max {max_dur:.0f}s)",
        file=sys.stderr,
    )

    return {
        "version": 1,
        "audio_file": str(audio_path),
        "segments": segments_out,
        "speaker_names": speaker_names,
        "backend": "pyannote",
    }
