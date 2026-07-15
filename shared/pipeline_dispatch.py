"""Central dispatcher for pipeline stages.

Reads `pipeline_backends.json` (managed by the Model Manager UI) and
routes calls to the right backend for each stage:

  - Voice Activity Detection  →  Silero | TEN VAD
  - Transcription             →  Whisper | Parakeet
  - Diarization               →  Lite (diarize_lite) | Sortformer

Each dispatcher function silently falls back to the current default
if the selected backend's package isn't installed — so switching
Active to Parakeet without having clicked Install surfaces a clean
error in the app rather than a cryptic ImportError deep in a
transcription stack trace.
"""
from __future__ import annotations

import sys
from pathlib import Path

# The pipeline directory is hyphenated ("transcription-pipeline"), so its
# config module can't be imported as a package — path-insert the directory
# and `import config`, the same way transcribe.py's siblings do.
_PIPELINE_DIR = Path(__file__).resolve().parent.parent / "transcription-pipeline"


def _active(stage: str, default: str) -> str:
    """Return the persisted active backend_key for a stage, or a default."""
    try:
        from shared.models import load_pipeline_backends
        return load_pipeline_backends().get(stage, default)
    except Exception:
        return default


# ── Voice Activity Detection ────────────────────────────────────────────────


def detect_speech_segments(audio, sr: int = 16000, threshold: float = 0.5) -> list[tuple[float, float]]:
    """Dispatch to the user-selected VAD backend.

    Silero (default) and TEN VAD both return the same
    (start_s, end_s) tuple list, so this dispatcher is a thin router.
    """
    backend = _active("vad", "silero")
    if backend == "ten":
        from shared.vad_ten import detect_speech_segments as ten_detect
        return ten_detect(audio, sr=sr, threshold=threshold)
    # Silero is the in-tree default in diarize_lite.detect_speech_segments.
    from shared.diarize_lite import detect_speech_segments as silero_detect
    return silero_detect(audio, sr=sr)


# ── Transcription (ASR) ────────────────────────────────────────────────────


def transcribe_audio(audio_path: str | Path, language: str | None = None) -> dict:
    """Dispatch to the user-selected transcription backend.

    Returns the same shape regardless of backend:
        {"text": str, "segments": [{"start", "end", "text"}]}
    """
    backend = _active("transcription", "whisper")
    if backend == "parakeet":
        from shared.asr_parakeet import transcribe as parakeet_transcribe
        return parakeet_transcribe(audio_path, language=language)
    # Whisper is the historical default. Import inline to avoid pulling
    # torch at module import time when Parakeet is active.
    import whisper
    if str(_PIPELINE_DIR) not in sys.path:
        sys.path.insert(0, str(_PIPELINE_DIR))
    import config  # transcribe.py's config (transcription-pipeline/config.py)
    model = whisper.load_model(config.WHISPER_MODEL, device=config.WHISPER_DEVICE)
    return model.transcribe(str(audio_path), language=language or config.WHISPER_LANGUAGE, verbose=False)


# ── Diarization ─────────────────────────────────────────────────────────────


def diarize(
    audio_path: str | Path,
    whisper_segments: list[dict],
    n_speakers: int | None = None,
    calendar_context=None,
) -> dict:
    """Dispatch to the user-selected diarization backend.

    Returns the same shape regardless of backend:
        {"segments": [{"start", "end", "text", "speaker", ...}], ...}
    """
    backend = _active("diarization", "lite")
    if backend == "sortformer":
        # Sortformer is a fixed-topology model and its public inference API
        # does not honour an explicit speaker count. The viewer's re-detect
        # control promises that a selected count is used, so route that
        # deliberate/manual path through the count-aware local diariser.
        if n_speakers is not None:
            print(
                "Diarization: explicit speaker count requested; using Lite "
                "count-aware backend instead of Sortformer",
                file=sys.stderr,
            )
            from shared.diarize_lite import diarize as lite_diarize
            return lite_diarize(
                audio_path,
                whisper_segments,
                n_speakers=n_speakers,
                calendar_context=calendar_context,
            )
        from shared.diarize_sortformer import diarize as sortformer_diarize
        return sortformer_diarize(
            audio_path,
            whisper_segments,
            n_speakers=n_speakers,
            calendar_context=calendar_context,
        )
    from shared.diarize_lite import diarize as lite_diarize
    return lite_diarize(
        audio_path,
        whisper_segments,
        n_speakers=n_speakers,
        calendar_context=calendar_context,
    )


# ── Self-describing for eval / debugging ───────────────────────────────────


def active_pipeline() -> dict[str, str]:
    """Return the currently-active backend for every stage. Useful in
    eval-report headers and in `transcribe.py`'s log output."""
    try:
        from shared.models import load_pipeline_backends
        return load_pipeline_backends()
    except Exception:
        return {
            "transcription": "whisper",
            "diarization": "lite",
            "vad": "silero",
            "embedding": "titanet",
        }
