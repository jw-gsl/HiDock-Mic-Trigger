"""Speaker diarization using pyannote.audio.

Requires:
    - pyannote.audio >= 3.3 (uncomment in requirements.txt)
    - HuggingFace token with access to pyannote/speaker-diarization-3.1
      and pyannote/segmentation-3.0 (store in config.json)
"""
from __future__ import annotations

import json
from pathlib import Path

import config

CONFIG_JSON = Path(__file__).parent / "config.json"


def _load_hf_token() -> str:
    """Load HuggingFace token from config.json."""
    if not CONFIG_JSON.exists():
        raise RuntimeError(
            f"config.json not found at {CONFIG_JSON}. "
            "Create it with your HuggingFace token."
        )
    data = json.loads(CONFIG_JSON.read_text())
    token = data.get("huggingface_token", "")
    if not token:
        raise RuntimeError(
            "huggingface_token is empty in config.json. "
            "Add your HuggingFace read token."
        )
    return token


def _load_pipeline():
    """Load pyannote speaker diarization pipeline onto MPS."""
    import torch
    from pyannote.audio import Pipeline

    token = _load_hf_token()
    device = config.WHISPER_DEVICE
    if device == "mps" and not torch.backends.mps.is_available():
        device = "cpu"

    pipeline = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-3.1",
        use_auth_token=token,
    )
    pipeline.to(torch.device(device))
    return pipeline


def _format_time(seconds: float) -> str:
    """Format seconds as MM:SS."""
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def _assign_text_to_segments(diarization, whisper_segments: list[dict]) -> list[dict]:
    """Map whisper transcript segments onto diarization speaker turns."""
    turns = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        turn_text_parts = []
        for seg in whisper_segments:
            seg_start = seg.get("start", 0)
            seg_end = seg.get("end", 0)
            # Check overlap between diarization turn and whisper segment
            overlap_start = max(turn.start, seg_start)
            overlap_end = min(turn.end, seg_end)
            if overlap_end > overlap_start:
                seg_duration = seg_end - seg_start
                if seg_duration > 0:
                    overlap_ratio = (overlap_end - overlap_start) / seg_duration
                    if overlap_ratio > 0.5:
                        turn_text_parts.append(seg.get("text", "").strip())
        turns.append({
            "speaker": speaker,
            "start": turn.start,
            "end": turn.end,
            "text": " ".join(turn_text_parts),
        })
    return turns


def diarize(audio_path: str, plain_text: str, whisper_segments: list[dict]) -> str:
    """Run diarization and return formatted transcript with speaker labels.

    Args:
        audio_path: Path to the audio file.
        plain_text: Plain text transcript from Whisper (fallback).
        whisper_segments: Whisper segment dicts with start/end/text.

    Returns:
        Formatted string with speaker-labelled segments.
    """
    pipeline = _load_pipeline()
    diarization = pipeline(audio_path)

    # Try to match speakers to voice library
    speaker_names = {}
    try:
        from voice_library import identify_speakers
        speaker_names = identify_speakers(audio_path, diarization)
    except ImportError:
        pass
    except Exception:
        pass

    turns = _assign_text_to_segments(diarization, whisper_segments)

    if not turns:
        return plain_text

    lines = []
    for turn in turns:
        speaker_id = turn["speaker"]
        name = speaker_names.get(speaker_id, speaker_id)
        start = _format_time(turn["start"])
        end = _format_time(turn["end"])
        text = turn["text"]
        if text:
            lines.append(f"[{start}-{end}] {name}: {text}")

    return "\n".join(lines) if lines else plain_text
