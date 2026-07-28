"""Diarize with a pyannote pipeline, as an alternative to Sortformer.

Why bother, given Sortformer is already wired in: the harness measured a **-1.25
speaker-count bias** across 16 reviewed meetings, and Rec79 Part 2 showed the
mechanism. `diar_sortformer_4spk-v1` caps at four speakers and predicts a fixed
speaker set per 300 s window, so a participant who speaks 3.8% of a meeting and
straddles a window boundary never gets a cluster of their own — and nothing
downstream can reshape a cluster that was never created.

pyannote is architecturally different in exactly that respect: it segments, embeds,
then clusters *globally*, so speaker count is an outcome of clustering rather than
a per-window prediction with a hard ceiling. pyannote's own notes for
`speaker-diarization-community-1` claim "significant improvements to speaker
assignment and counting, with marked reductions in speaker confusion" over 3.1,
which is the failure this pipeline actually has.

Licence, which matters here because the current strongest identity model does not
ship: pyannote's pipeline code and the gated HF models are MIT and free for
research *and* commercial use — the gate is usage tracking, not payment. Unlike
ReDimNet2 (CC BY-NC-SA) this could go into a distributed build.

Everything after "who spoke when" is deliberately shared with the Sortformer
backend — segment assignment, voice-library naming, collision resolution, empty
speaker pruning — so a backend comparison measures the diarizer and not two
divergent post-processing stacks.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

# Preferred first. `community-1` supersedes 3.1; 3.1 is the fallback because it
# may already be cached locally, and an uncached gated model needs a HF token the
# user has to opt into.
_MODEL_PREFERENCE = (
    "pyannote/speaker-diarization-community-1",
    "pyannote/speaker-diarization-3.1",
)

_pipeline_cache: dict[str, object] = {}


def available_models() -> list[str]:
    return list(_MODEL_PREFERENCE)


def _hf_token() -> str | bool | None:
    """A Hugging Face token, if the user has provided one.

    pyannote's models are gated: downloading needs an accepted licence and a
    token. Locally cached models load without one, which is why this is optional
    rather than required.
    """
    import os

    for name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_TOKEN"):
        value = os.environ.get(name)
        if value:
            return value
    token_file = Path.home() / ".cache" / "huggingface" / "token"
    if token_file.is_file():
        content = token_file.read_text(encoding="utf-8").strip()
        if content:
            return content
    return None


def load_pipeline(model_id: str | None = None):
    """Load a pyannote pipeline, preferring the newest available model.

    Returns None when nothing can be loaded, so callers degrade to another
    backend instead of failing a transcription.
    """
    candidates = [model_id] if model_id else list(_MODEL_PREFERENCE)
    token = _hf_token()
    for candidate in candidates:
        if candidate in _pipeline_cache:
            return _pipeline_cache[candidate]
        try:
            from pyannote.audio import Pipeline

            pipeline = Pipeline.from_pretrained(candidate, token=token)
        except Exception as exc:  # noqa: BLE001 - try the next candidate
            print(f"pyannote: {candidate} unavailable ({type(exc).__name__}: {exc})",
                  file=sys.stderr)
            continue
        if pipeline is None:
            # from_pretrained returns None for a gated model without access,
            # rather than raising — which is easy to mistake for success.
            print(
                f"pyannote: {candidate} is gated and no token grants access. "
                "Accept the licence on huggingface.co and set HF_TOKEN.",
                file=sys.stderr,
            )
            continue
        _pipeline_cache[candidate] = pipeline
        print(f"pyannote: diarizing via {candidate}", file=sys.stderr)
        return pipeline
    return None


def _turns_from_annotation(annotation) -> list[tuple[float, float, str]]:
    """(start, end, label) turns from a pyannote Annotation or DiarizeOutput."""
    source = getattr(annotation, "speaker_diarization", annotation)
    turns: list[tuple[float, float, str]] = []
    for segment, _track, label in source.itertracks(yield_label=True):
        start, end = float(segment.start), float(segment.end)
        if end > start:
            turns.append((start, end, str(label)))
    turns.sort(key=lambda item: item[0])
    return turns


def diarize(
    audio_path: str | Path,
    whisper_segments: list[dict],
    n_speakers: int | None = None,
    calendar_context=None,
    **_ignored,
) -> dict:
    """Same signature and return shape as the other backends.

    `_ignored` absorbs Sortformer-only refinements (`two_sided_partition`,
    `refine_assignments`, `pinned_intervals`): they describe passes that exist to
    compensate for Sortformer's fixed-topology output, and silently accepting
    them keeps the dispatcher simple. If pyannote's own clustering makes them
    unnecessary, that is the point of measuring it.
    """
    from shared.audio_utils import load_audio
    from shared.diarize_lite import (
        _MAX_MERGED_SEGMENT_SECONDS,
        _anonymize_non_speech,
        _split_long_segments,
    )
    from shared.diarize_sortformer import (
        _assign_speakers_segment_level,
        _assign_speakers_word_level,
        _expected_speakers_from_calendar,
        _prune_empty_speakers,
        _resolve_speaker_names,
    )

    audio_path = Path(audio_path)
    pipeline = load_pipeline()
    if pipeline is None:
        raise RuntimeError(
            "pyannote pipeline unavailable — accept the model licence on "
            "huggingface.co and set HF_TOKEN, or select another diarization backend"
        )

    audio = load_audio(audio_path, sr=16000)
    total_duration = len(audio) / 16000.0

    # In-memory waveform, not a path: torchcodec's audio decoding is commonly
    # broken on this machine, and pyannote explicitly supports being handed a
    # preloaded tensor instead.
    import torch

    waveform = torch.from_numpy(np.asarray(audio, dtype=np.float32)).unsqueeze(0)
    request = {"waveform": waveform, "sample_rate": 16000}

    # An external count is evidence, so pass it through rather than post-hoc
    # merging as Sortformer needs. Unlike Sortformer there is no 4-speaker cap.
    expected = n_speakers or _expected_speakers_from_calendar(calendar_context)
    try:
        annotation = pipeline(request, num_speakers=expected) if expected else pipeline(request)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"pyannote diarization failed: {exc}") from exc

    turns = _turns_from_annotation(annotation)
    if not turns:
        print("pyannote: no speech turns detected", file=sys.stderr)
        return {
            "version": 1,
            "audio_file": str(audio_path),
            "segments": [],
            "speaker_names": {},
            "speaker_meta": {},
            "speaker_embeddings": {},
            "backend": "pyannote",
        }

    # Normalise to the "Speaker N" space the rest of the pipeline expects, in
    # order of first appearance, so labels are stable and human-readable.
    label_map: dict[str, str] = {}
    for _, _, label in turns:
        if label not in label_map:
            label_map[label] = f"Speaker {len(label_map) + 1}"
    renamed = [(start, end, label_map[label]) for start, end, label in turns]
    internal_labels = list(label_map.values())

    speaker_info, naming_model = _resolve_speaker_names(
        audio, renamed, internal_labels, sr=16000,
        allowed_names=getattr(calendar_context, "candidate_names", None),
    )

    has_words = any((segment.get("words") or []) for segment in whisper_segments)
    raw_segments = (
        _assign_speakers_word_level(whisper_segments, renamed)
        if has_words
        else _assign_speakers_segment_level(whisper_segments, renamed)
    )

    label_to_id = {label: index for index, label in enumerate(internal_labels)}
    speaker_names = {
        str(spk_id): (speaker_info.get(label) or {}).get("name", label)
        for label, spk_id in label_to_id.items()
    }
    speaker_meta: dict[str, dict] = {}
    speaker_embeddings: dict[str, list] = {}
    for label, spk_id in label_to_id.items():
        info = speaker_info.get(label) or {}
        speaker_meta[str(spk_id)] = {
            "source": info.get("source", "generic"),
            "confidence": info.get("confidence"),
            "verified": False,
        }
        if info.get("embedding") is not None:
            speaker_embeddings[str(spk_id)] = info["embedding"]

    try:
        from shared.speaker_meta import resolve_name_collisions

        resolve_name_collisions(speaker_names, speaker_meta)
    except Exception as exc:  # noqa: BLE001 - naming must not break diarization
        print(f"pyannote: name-collision resolve skipped ({exc})", file=sys.stderr)

    for segment in raw_segments:
        label = segment.get("speaker") or internal_labels[0]
        spk_id = label_to_id.get(label, 0)
        segment["speaker_id"] = spk_id
        segment["speaker"] = speaker_names.get(str(spk_id), label)

    raw_segments = _anonymize_non_speech(raw_segments)

    segments_out: list[dict] = []
    for segment in raw_segments:
        if not segment.get("text"):
            continue
        if (
            segments_out
            and segments_out[-1]["speaker_id"] == segment["speaker_id"]
            and segment["start"] - segments_out[-1]["end"] < 1.0
        ):
            previous = segments_out[-1]
            previous["end"] = max(previous["end"], segment["end"])
            previous["text"] = f"{previous['text']} {segment['text']}".strip()
            if segment.get("words"):
                previous.setdefault("words", []).extend(segment["words"])
        else:
            segments_out.append(dict(segment))
    segments_out = _split_long_segments(
        segments_out, max_duration=_MAX_MERGED_SEGMENT_SECONDS
    )

    speaker_names, speaker_meta, speaker_embeddings = _prune_empty_speakers(
        speaker_names, speaker_meta, speaker_embeddings, segments_out
    )

    print(
        f"pyannote: {len(turns)} turns, {len(speaker_names)} speakers, "
        f"{total_duration:.0f}s audio, "
        f"{'word' if has_words else 'segment'}-level alignment, "
        f"{len(segments_out)} output segments",
        file=sys.stderr,
    )
    return {
        "version": 1,
        "audio_file": str(audio_path),
        "segments": segments_out,
        "speaker_names": speaker_names,
        "speaker_meta": speaker_meta,
        "speaker_embeddings": speaker_embeddings,
        "speaker_embedding_model": naming_model,
        "backend": "pyannote",
        "speaker_count_strategy": "manual" if n_speakers else (
            "calendar" if expected else "clustered"
        ),
    }
