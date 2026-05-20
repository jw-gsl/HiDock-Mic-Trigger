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
    from transcription_pipeline import config  # transcribe.py's config
    model = whisper.load_model(config.WHISPER_MODEL, device=config.WHISPER_DEVICE)
    return model.transcribe(str(audio_path), language=language or config.WHISPER_LANGUAGE, verbose=False)


# ── Diarization ─────────────────────────────────────────────────────────────


def diarize(audio_path: str | Path, whisper_segments: list[dict], n_speakers: int | None = None) -> dict:
    """Dispatch to the user-selected diarization backend.

    Returns the same shape regardless of backend:
        {"segments": [{"start", "end", "text", "speaker", ...}], ...}
    """
    backend = _active("diarization", "lite")
    if backend == "auto":
        return _diarize_auto(audio_path, whisper_segments, n_speakers=n_speakers)
    if backend == "sortformer":
        result = _diarize_sortformer(audio_path, whisper_segments, n_speakers=n_speakers)
        if _sortformer_boundary_check_enabled() and _looks_like_sortformer_cap_hit(result):
            print(
                "Sortformer returned 4 speakers and the boundary check flagged a likely cap-hit; "
                "re-running with pyannote. Disable via pipeline_backends.json "
                "`sortformer_boundary_check: false`.",
                file=sys.stderr,
            )
            return _diarize_pyannote(audio_path, whisper_segments, n_speakers=n_speakers)
        return result
    if backend == "pyannote":
        return _diarize_pyannote(audio_path, whisper_segments, n_speakers=n_speakers)
    from shared.diarize_lite import diarize as lite_diarize
    return lite_diarize(audio_path, whisper_segments, n_speakers=n_speakers)


def _diarize_sortformer(audio_path, whisper_segments, n_speakers):
    from shared.diarize_sortformer import diarize as sortformer_diarize
    return sortformer_diarize(audio_path, whisper_segments, n_speakers=n_speakers)


def _diarize_pyannote(audio_path, whisper_segments, n_speakers):
    from shared.diarize_pyannote import diarize as pyannote_diarize
    return pyannote_diarize(audio_path, whisper_segments, n_speakers=n_speakers)


def _diarize_auto(audio_path, whisper_segments, n_speakers):
    """Pre-flight speaker-count probe → Sortformer (≤4) or pyannote (≥5).

    Logs the probe result + routing choice to stderr so the menubar
    log carries the decision. Falls back gracefully if the probe
    can't run for some reason (sortformer is the safer default — it's
    available locally and doesn't require HF terms acceptance)."""
    try:
        from shared.speaker_count_probe import estimate_speaker_count
        estimate, confidence = estimate_speaker_count(audio_path)
    except Exception as e:
        print(f"auto-diarize: probe failed ({e}); falling back to sortformer", file=sys.stderr)
        return _diarize_sortformer(audio_path, whisper_segments, n_speakers=n_speakers)

    if estimate >= 5:
        print(
            f"auto-diarize: probe estimate {estimate} (conf {confidence:.2f}) "
            "→ pyannote (Sortformer's 4-speaker cap would merge speakers)",
            file=sys.stderr,
        )
        return _diarize_pyannote(audio_path, whisper_segments, n_speakers=n_speakers)
    print(
        f"auto-diarize: probe estimate {estimate} (conf {confidence:.2f}) "
        "→ sortformer",
        file=sys.stderr,
    )
    return _diarize_sortformer(audio_path, whisper_segments, n_speakers=n_speakers)


def _sortformer_boundary_check_enabled() -> bool:
    """Read the opt-in flag from pipeline_backends.json. Default off so
    explicit `"sortformer"` selection doesn't silently re-route to
    pyannote without the user asking for it."""
    try:
        from shared.models import load_pipeline_backends
        return bool(load_pipeline_backends().get("sortformer_boundary_check", False))
    except Exception:
        return False


def _looks_like_sortformer_cap_hit(result: dict) -> bool:
    """Heuristic: when Sortformer returns exactly 4 speakers, check
    whether one of those clusters looks like it actually contains
    multiple distinct voices. We re-use the speaker count probe's
    machinery: sample a few windows per Sortformer cluster, embed
    them, and check whether any single cluster's intra-cluster cosine
    spread is much larger than the others' — that's the signature of
    two real speakers having been merged into one bucket.

    Conservative: only triggers when exactly 4 speakers are present
    AND at least one cluster's spread is > 2× the median cluster spread.
    Belt-and-braces: avoids false re-runs on legitimately 4-speaker
    meetings.

    Returns False on any error so the original Sortformer result is
    preserved on the failure path."""
    try:
        spk_names = result.get("speaker_names") or {}
        if len(spk_names) != 4:
            return False

        segments = result.get("segments") or []
        audio_file = result.get("audio_file")
        if not audio_file or not segments:
            return False

        import numpy as np
        from shared.audio_utils import extract_embedding, load_audio
        from shared.models import ensure_speaker_embed
        import onnxruntime as ort

        sr = 16000
        audio = load_audio(audio_file, sr=sr)
        session = ort.InferenceSession(str(ensure_speaker_embed()), providers=["CPUExecutionProvider"])

        # Group segment audio per speaker_id, embed up to 5 chunks each
        # (longest first so embeddings are reliable).
        per_speaker_embeds: dict[int, list] = {}
        per_speaker_segs: dict[int, list] = {}
        for s in segments:
            sid = s.get("speaker_id")
            if sid is None:
                continue
            per_speaker_segs.setdefault(int(sid), []).append((float(s["start"]), float(s["end"])))

        for sid, segs in per_speaker_segs.items():
            segs.sort(key=lambda se: se[1] - se[0], reverse=True)
            embeds = []
            for start, end in segs[:5]:
                # Cap each chunk to 4s so embeddings stay short + reliable.
                end = min(end, start + 4.0)
                sa = int(start * sr); ea = int(end * sr)
                if ea - sa < int(1.5 * sr):
                    continue
                try:
                    emb = extract_embedding(audio[sa:ea], sr=sr, onnx_session=session)
                except Exception:
                    continue
                norm = float(np.linalg.norm(emb))
                if norm > 1e-10:
                    embeds.append((emb / norm).astype(np.float32))
            if len(embeds) >= 2:
                per_speaker_embeds[sid] = embeds

        spreads: list[float] = []
        for sid, embeds in per_speaker_embeds.items():
            if len(embeds) < 2:
                continue
            # Mean intra-cluster cosine distance for this speaker.
            n = len(embeds)
            pairs = []
            for i in range(n):
                for j in range(i + 1, n):
                    pairs.append(1.0 - float(embeds[i] @ embeds[j]))
            if pairs:
                spreads.append(float(np.mean(pairs)))

        if len(spreads) < 3:
            return False

        median_spread = float(np.median(spreads))
        max_spread = max(spreads)
        # 2x median + a minimum floor so we don't trigger on three tight
        # clusters with one slightly looser legitimate one.
        triggers = max_spread > max(2.0 * median_spread, 0.30)
        if triggers:
            print(
                f"sortformer-boundary-check: per-speaker spreads={[round(s, 3) for s in spreads]}, "
                f"median={median_spread:.3f}, max={max_spread:.3f} — flagged cap-hit",
                file=sys.stderr,
            )
        return triggers
    except Exception as e:
        print(f"sortformer-boundary-check: skipped ({e})", file=sys.stderr)
        return False


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
