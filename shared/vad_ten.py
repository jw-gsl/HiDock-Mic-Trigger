"""TEN VAD backend — Voice Activity Detection using the TEN Framework.

Selectable alternative to Silero VAD. Uses the `ten_vad` pip package
(installed via the Model Manager UI). Smaller model (306 KB bundled
ONNX) with reportedly sharper segment boundaries on fast speaker
turns than Silero.

Exposes `detect_speech_segments(audio, sr) -> list[tuple[float, float]]`
with the same contract as `shared.diarize_lite.detect_speech_segments`
so callers can switch backends without touching their code.

Inspired by Chris Laidler's transcribe.py — the `from ten_vad import
TenVad` API is fixed, but we apply the same (start, end) aggregation
and threshold our codebase expects.
"""
from __future__ import annotations

import numpy as np


# TEN VAD internals — fixed by the model, not tunable here.
# Model runs on int16 16 kHz mono audio in 16 ms frames (256 samples).
_TEN_SAMPLE_RATE = 16000
_TEN_FRAME_SAMPLES = 256


def detect_speech_segments(
    audio: np.ndarray,
    sr: int = 16000,
    threshold: float = 0.5,
    min_gap_s: float = 0.2,
) -> list[tuple[float, float]]:
    """Run TEN VAD on float32 mono audio; return speech (start, end) in seconds.

    Args:
        audio: float32 mono [-1, 1] at 16 kHz.
        sr: sample rate — currently hardcoded expectation is 16000.
        threshold: voice-probability cutoff (0.5 default, matches Chris's code).
        min_gap_s: adjacent speech frames within this gap are merged.

    Raises:
        ModuleNotFoundError: if `ten-vad` isn't installed. Install via
            the Model Manager's "Install" button on the TEN VAD row.
    """
    if sr != _TEN_SAMPLE_RATE:
        raise ValueError(f"TEN VAD expects {_TEN_SAMPLE_RATE} Hz audio; got {sr}")

    from ten_vad import TenVad

    # TEN VAD takes int16 samples. Convert once; the wrapper needs it.
    int16 = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)
    vad = TenVad(hop_size=_TEN_FRAME_SAMPLES, threshold=threshold)

    # Walk the audio in fixed-size frames; each frame yields a probability.
    probs: list[float] = []
    n_frames = len(int16) // _TEN_FRAME_SAMPLES
    for i in range(n_frames):
        frame = int16[i * _TEN_FRAME_SAMPLES : (i + 1) * _TEN_FRAME_SAMPLES]
        prob, _is_speech = vad.process(frame)
        probs.append(float(prob))

    # Convert per-frame probs to segment ranges. Frame t covers samples
    # [t * hop, (t+1) * hop). Group consecutive above-threshold frames.
    frame_duration = _TEN_FRAME_SAMPLES / _TEN_SAMPLE_RATE
    segments: list[tuple[float, float]] = []
    start_frame: int | None = None
    for t, p in enumerate(probs):
        if p >= threshold:
            if start_frame is None:
                start_frame = t
        else:
            if start_frame is not None:
                segments.append((start_frame * frame_duration, t * frame_duration))
                start_frame = None
    if start_frame is not None:
        segments.append((start_frame * frame_duration, len(probs) * frame_duration))

    # Merge segments separated by short gaps (TEN VAD is sharper than
    # Silero so the Lite diarizer's existing merge step — gap < 0.3 s
    # — is appropriate, but do a small prior merge here to match the
    # shape Silero returns so downstream code doesn't need tuning).
    if min_gap_s > 0 and segments:
        merged = [segments[0]]
        for s, e in segments[1:]:
            if s - merged[-1][1] < min_gap_s:
                merged[-1] = (merged[-1][0], e)
            else:
                merged.append((s, e))
        segments = merged

    return segments
