"""NeMo Sortformer diarization backend.

Selectable alternative to `diarize_lite`. End-to-end neural speaker
diarization via NVIDIA's Sortformer (up to 4 speakers). Expected to be
substantially more accurate on per-turn attribution than our
Silero+TitaNet+clustering pipeline, at the cost of a ~2 GB NeMo
install and CPU-only inference on macOS (torch MPS doesn't support
Sortformer's conv2d stack).

Exposes `diarize(audio_path, whisper_segments, n_speakers) -> dict`
matching `shared.diarize_lite.diarize`'s signature. The Sortformer
model returns its own speaker turns without needing Whisper
segments; we still accept `whisper_segments` so we can emit the
same consumer-friendly output shape (per-segment speaker labels
aligned to Whisper's text).

Reference implementation: `~/Downloads/transcribe.py` (Chris Laidler),
commit 3498342 registry entry.

Raises ModuleNotFoundError at call time if NeMo isn't installed so
selecting the Lite diarizer stays functional in envs without NeMo.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np


_DIAR_MODEL_NAME = "nvidia/diar_sortformer_4spk-v1"
_WINDOW_SEC = 300.0
_OVERLAP_SEC = 30.0


def _load_diarizer():
    """Load Sortformer once and return a CPU-bound model handle.

    Imports inside the function so that `shared.diarize_sortformer` is
    import-safe in environments without NeMo — the error surfaces only
    when the user actually selects Sortformer as active.
    """
    try:
        from nemo.collections.asr.models import SortformerEncLabelModel
    except ImportError as e:
        raise ModuleNotFoundError(
            "nemo-toolkit is not installed. Install it via the Model "
            "Manager (NeMo Sortformer row > Install) before selecting "
            "Sortformer as the active diarization backend."
        ) from e

    model = SortformerEncLabelModel.from_pretrained(model_name=_DIAR_MODEL_NAME)
    # Force CPU — Sortformer's conv2d stack hits
    # `convolution_overrideable` on torch MPS and either fails or silently
    # produces garbage. MPS would help inference speed, but correctness
    # beats latency here.
    try:
        import torch
        model = model.to(torch.device("cpu"))
    except Exception:
        pass
    model.eval()
    return model


def _run_window(model, audio_window: np.ndarray, offset_s: float):
    """Diarize one 300s window of audio, returning turns offset to the
    global timeline.

    Args:
        model: loaded Sortformer model.
        audio_window: float32 mono at 16 kHz.
        offset_s: start of this window within the full recording.

    Returns:
        list of (start_s, end_s, speaker_id) tuples, timestamps absolute.
    """
    # Sortformer takes a file path; write the window to a temp wav.
    import soundfile as sf
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        tmp_path = f.name
    try:
        sf.write(tmp_path, audio_window, 16000)
        # `diarize` returns a list-of-lists of predicted segments;
        # each segment is [start_s, end_s, speaker_id].
        raw = model.diarize(audio=[tmp_path])
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    turns: list[tuple[float, float, str]] = []
    if not raw:
        return turns
    window_turns = raw[0] if isinstance(raw[0], list) else raw
    for item in window_turns:
        if isinstance(item, dict):
            s, e, spk = float(item["start"]), float(item["end"]), str(item.get("speaker") or item.get("speaker_id"))
        else:
            s, e, spk = float(item[0]), float(item[1]), str(item[2])
        turns.append((s + offset_s, e + offset_s, spk))
    return turns


def _assign_words_to_turns(whisper_segments, turns):
    """For each Whisper segment, pick the speaker from the turn with
    maximum temporal overlap. Simpler version of Chris's word-level
    alignment — works at segment granularity until transcribe.py
    starts emitting word timestamps."""
    out = []
    for seg in whisper_segments:
        s, e = float(seg["start"]), float(seg["end"])
        best_overlap = 0.0
        best_speaker: str | None = None
        for ts, te, spk in turns:
            overlap = max(0.0, min(e, te) - max(s, ts))
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = spk
        if best_speaker is None:
            # No overlap — use nearest turn centre as fallback, same
            # fallback philosophy as diarize_lite's no-overlap fix.
            mid = (s + e) / 2
            best_dist = float("inf")
            for ts, te, spk in turns:
                d = abs((ts + te) / 2 - mid)
                if d < best_dist:
                    best_dist = d
                    best_speaker = spk
        out.append({
            "start": s,
            "end": e,
            "text": seg.get("text", "").strip(),
            "speaker": best_speaker or "Speaker 1",
        })
    return out


def diarize(
    audio_path: str | Path,
    whisper_segments: list[dict],
    n_speakers: int | None = None,
) -> dict:
    """Diarize with NeMo Sortformer.

    Signature matches `shared.diarize_lite.diarize` so callers can
    swap backends without code changes. `n_speakers` is accepted but
    Sortformer caps at 4; hint is used informationally only.
    """
    from shared.audio_utils import load_audio

    audio_path = Path(audio_path)
    audio = load_audio(audio_path, sr=16000)
    total_dur = len(audio) / 16000.0

    model = _load_diarizer()

    # Window long audio — Sortformer runs out of memory on multi-hour
    # files in one shot. 300s windows with 30s overlap for speaker
    # stitching across windows (simple majority-overlap join).
    all_turns: list[tuple[float, float, str]] = []
    step = int((_WINDOW_SEC - _OVERLAP_SEC) * 16000)
    win_samples = int(_WINDOW_SEC * 16000)
    if len(audio) <= win_samples:
        all_turns = _run_window(model, audio, 0.0)
    else:
        for start in range(0, len(audio), step):
            end = min(len(audio), start + win_samples)
            window = audio[start:end]
            offset = start / 16000.0
            turns = _run_window(model, window, offset)
            all_turns.extend(turns)
            if end >= len(audio):
                break

    # Merge consecutive same-speaker turns across window boundaries.
    all_turns.sort(key=lambda t: t[0])
    merged: list[list] = []
    for s, e, spk in all_turns:
        if merged and merged[-1][2] == spk and s - merged[-1][1] < 1.0:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e, spk])
    all_turns = [(m[0], m[1], m[2]) for m in merged]

    # Normalize speaker IDs to stable "Speaker 1"/"Speaker 2" names
    # in order of first appearance (matches diarize_lite's behaviour).
    name_map: dict[str, str] = {}
    for s, e, spk in all_turns:
        if spk not in name_map:
            name_map[spk] = f"Speaker {len(name_map) + 1}"
    renamed_turns = [(s, e, name_map[spk]) for s, e, spk in all_turns]

    # Align whisper segments to the speaker turns. With word-level
    # Whisper output this would be per-word — we align per-segment
    # for now; the word-level upgrade is tracked in the Sortformer
    # plan doc.
    segments = _assign_words_to_turns(whisper_segments, renamed_turns)

    print(
        f"Sortformer: {len(renamed_turns)} turns, "
        f"{len(name_map)} speakers, "
        f"{total_dur:.0f}s audio",
        file=sys.stderr,
    )

    return {
        "segments": segments,
        "speakers": list(name_map.values()),
        "backend": "sortformer",
    }
