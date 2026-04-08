"""Lightweight speaker diarization using Silero VAD + speaker embeddings.

Detects speech segments via Silero VAD (ONNX), extracts speaker embeddings
(neural TitaNet when available, MFCC fallback otherwise), and clusters them
with agglomerative clustering to assign speaker labels to whisper transcript
segments.

Neural embeddings require the TitaNet ONNX model (~10MB download).
Falls back to MFCC automatically if the model is not available.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage

from shared.audio_utils import extract_embedding, load_audio, segment_audio
from shared.models import ensure_silero_vad

# Silero VAD operates on 16kHz audio in chunks
_VAD_SR = 16000
_VAD_WINDOW_SIZE = 256  # 16ms at 16kHz (Silero VAD v5)

_vad_session = None
_speaker_embed_session = None


def load_vad_model():
    """Load the Silero VAD ONNX model (cached after first call).

    Returns:
        onnxruntime.InferenceSession for Silero VAD.
    """
    global _vad_session
    if _vad_session is not None:
        return _vad_session

    import onnxruntime as ort

    model_path = ensure_silero_vad()
    _vad_session = ort.InferenceSession(
        str(model_path),
        providers=["CPUExecutionProvider"],
    )
    return _vad_session


def _load_speaker_embed_model():
    """Load the TitaNet speaker embedding ONNX model (cached after first call).

    Returns:
        onnxruntime.InferenceSession or None if not available.
    """
    global _speaker_embed_session
    if _speaker_embed_session is not None:
        return _speaker_embed_session

    try:
        from shared.models import ensure_speaker_embed

        model_path = ensure_speaker_embed()

        import onnxruntime as ort

        _speaker_embed_session = ort.InferenceSession(
            str(model_path),
            providers=["CPUExecutionProvider"],
        )
        return _speaker_embed_session
    except ImportError:
        return None
    except Exception as e:
        print(f"Failed to load speaker embedding model: {e}", file=sys.stderr)
        return None


def detect_speech_segments(
    audio: np.ndarray,
    sr: int = 16000,
    threshold: float = 0.08,
    min_speech_duration: float = 0.15,
    min_silence_duration: float = 1.5,
) -> list[tuple[float, float]]:
    """Detect speech segments using Silero VAD.

    Args:
        audio: 1-D float32 audio at the given sample rate.
        sr: Sample rate (must be 16000 for Silero VAD).
        threshold: Speech probability threshold (0-1).
        min_speech_duration: Minimum speech segment duration in seconds.
        min_silence_duration: Minimum silence gap to split segments.

    Returns:
        List of (start_seconds, end_seconds) tuples for speech regions.
    """
    if sr != _VAD_SR:
        raise ValueError(f"Silero VAD requires {_VAD_SR}Hz audio, got {sr}Hz")

    session = load_vad_model()

    # Silero VAD v5 expects: audio chunk, state (2, 1, 128), sr
    # State is (h, c) for the LSTM — initialized to zeros
    state = np.zeros((2, 1, 128), dtype=np.float32)
    sr_tensor = np.array(_VAD_SR, dtype=np.int64)

    # Process in chunks
    n_samples = len(audio)
    speech_probs = []

    for i in range(0, n_samples - _VAD_WINDOW_SIZE + 1, _VAD_WINDOW_SIZE):
        chunk = audio[i : i + _VAD_WINDOW_SIZE]
        chunk = chunk[np.newaxis, :]  # batch dim

        try:
            ort_inputs = {
                "input": chunk,
                "state": state,
                "sr": sr_tensor,
            }
            out, state_out = session.run(None, ort_inputs)
            state = state_out
            prob = out[0][0] if out[0].ndim > 0 else float(out[0])
            speech_probs.append(float(prob))
        except Exception as e:
            # If the model interface differs, fall back to simpler approach
            print(f"VAD inference error: {e}", file=sys.stderr)
            speech_probs.append(0.0)

    if not speech_probs:
        return []

    # Convert probabilities to binary speech/silence decisions
    is_speech = [p >= threshold for p in speech_probs]

    # Merge into segments
    segments = []
    chunk_duration = _VAD_WINDOW_SIZE / _VAD_SR
    in_speech = False
    start = 0.0

    for i, sp in enumerate(is_speech):
        t = i * chunk_duration
        if sp and not in_speech:
            start = t
            in_speech = True
        elif not sp and in_speech:
            end = t
            if end - start >= min_speech_duration:
                segments.append((start, end))
            in_speech = False

    # Close trailing speech segment
    if in_speech:
        end = len(is_speech) * chunk_duration
        if end - start >= min_speech_duration:
            segments.append((start, end))

    # Merge segments separated by short silence
    merged = []
    for seg in segments:
        if merged and seg[0] - merged[-1][1] < min_silence_duration:
            merged[-1] = (merged[-1][0], seg[1])
        else:
            merged.append(seg)

    return merged


def extract_speaker_embeddings(
    audio: np.ndarray,
    sr: int,
    segments: list[tuple[float, float]],
) -> np.ndarray:
    """Extract speaker embeddings for each segment.

    Uses neural TitaNet embeddings when the model is available,
    otherwise falls back to MFCC-based embeddings.

    Args:
        audio: Full audio array.
        sr: Sample rate.
        segments: List of (start, end) tuples in seconds.

    Returns:
        Numpy array of shape (N, embedding_dim) where N = len(segments).
    """
    # Try to load neural embedding model
    speaker_session = _load_speaker_embed_model()
    if speaker_session is not None:
        print("  Using neural speaker embeddings (TitaNet)", file=sys.stderr)
    else:
        print("  Using MFCC speaker embeddings (fallback)", file=sys.stderr)

    chunks = segment_audio(audio, sr, segments)
    embeddings = []
    for chunk in chunks:
        emb = extract_embedding(chunk, sr=sr, onnx_session=speaker_session)
        embeddings.append(emb)
    return np.array(embeddings, dtype=np.float32)


def cluster_speakers(
    embeddings: np.ndarray,
    n_speakers: int | None = None,
    max_speakers: int = 10,
    distance_threshold: float = 1.2,
) -> list[int]:
    """Cluster speaker embeddings using agglomerative clustering.

    Args:
        embeddings: Array of shape (N, embedding_dim).
        n_speakers: If known, force this many clusters. Otherwise auto-detect.
        max_speakers: Maximum number of speakers to detect.
        distance_threshold: Cosine distance threshold for auto-detection.

    Returns:
        List of integer speaker IDs (0-indexed), one per embedding.
    """
    n = len(embeddings)
    if n == 0:
        return []
    if n == 1:
        return [0]

    # Adjust threshold for neural embeddings (unit-normalized, tighter clusters)
    embed_dim = embeddings.shape[1]
    if embed_dim >= 128 and distance_threshold == 1.2:
        # Neural embeddings use cosine distance; tighter threshold works better
        distance_threshold = 0.5

    # Compute linkage using cosine distance
    Z = linkage(embeddings, method="average", metric="cosine")

    if n_speakers is not None and isinstance(n_speakers, int) and n_speakers > 0:
        labels = fcluster(Z, t=float(n_speakers), criterion="maxclust")
    else:
        labels = fcluster(Z, t=distance_threshold, criterion="distance")
        # Cap at max_speakers
        n_clusters = len(set(labels))
        if n_clusters > max_speakers:
            labels = fcluster(Z, t=float(max_speakers), criterion="maxclust")

    # Convert to 0-indexed
    labels = [int(lbl) - 1 for lbl in labels]
    return labels


def _assign_speakers_to_whisper_segments(
    whisper_segments: list[dict],
    speech_segments: list[tuple[float, float]],
    speaker_labels: list[int],
) -> list[int]:
    """Map each whisper segment to the closest speech segment's speaker.

    Uses overlap-based matching: each whisper segment is assigned the speaker
    label of the speech segment it overlaps with the most.

    Args:
        whisper_segments: Dicts with "start" and "end" keys.
        speech_segments: VAD-detected (start, end) tuples.
        speaker_labels: Speaker ID for each speech segment.

    Returns:
        Speaker ID for each whisper segment.
    """
    result = []
    for ws in whisper_segments:
        ws_start = ws["start"]
        ws_end = ws["end"]
        best_overlap = 0.0
        best_speaker = 0

        for (ss_start, ss_end), spk in zip(speech_segments, speaker_labels):
            overlap_start = max(ws_start, ss_start)
            overlap_end = min(ws_end, ss_end)
            overlap = max(0.0, overlap_end - overlap_start)
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = spk

        result.append(best_speaker)
    return result


def _filter_hallucinations(segments: list[dict], max_repeats: int = 3) -> list[dict]:
    """Remove repeated short segments at the end (Whisper hallucination)."""
    if len(segments) < max_repeats + 1:
        return segments

    # Check if the last N segments are all the same short text
    last_texts = [s.get("text", "").strip().lower() for s in segments[-max_repeats:]]
    if len(set(last_texts)) == 1 and len(last_texts[0]) < 20:
        # Find where the repetition starts
        repeated = last_texts[0]
        cutoff = len(segments)
        for i in range(len(segments) - 1, -1, -1):
            if segments[i].get("text", "").strip().lower() == repeated:
                cutoff = i
            else:
                break
        removed = len(segments) - cutoff
        if removed > 0:
            print(f"  Filtered {removed} hallucinated segments ('{repeated}')", file=sys.stderr)
            return segments[:cutoff]

    return segments


def _merge_whisper_segments(segments: list[dict], max_gap: float = 1.5) -> list[dict]:
    """Merge Whisper micro-segments into longer conversational turns."""
    if not segments:
        return segments

    merged = []
    current = {
        "start": segments[0].get("start", 0),
        "end": segments[0].get("end", 0),
        "text": segments[0].get("text", "").strip(),
    }

    for seg in segments[1:]:
        gap = seg.get("start", 0) - current["end"]
        if gap <= max_gap:
            # Merge: extend the current segment
            current["end"] = seg.get("end", current["end"])
            current["text"] += " " + seg.get("text", "").strip()
        else:
            # Gap too large: start a new segment
            merged.append(current)
            current = {
                "start": seg.get("start", 0),
                "end": seg.get("end", 0),
                "text": seg.get("text", "").strip(),
            }

    merged.append(current)
    return merged


def diarize(
    audio_path: str | Path,
    whisper_segments: list[dict],
    n_speakers: int | None = None,
) -> dict:
    """Run full diarization on an audio file with pre-computed whisper segments.

    Args:
        audio_path: Path to the audio file.
        whisper_segments: List of dicts with "start", "end", "text" keys
            (as returned by pywhispercpp).
        n_speakers: Optional known number of speakers.

    Returns:
        Diarized transcript dict with the structure:
        {
            "version": 1,
            "audio_file": "...",
            "segments": [
                {"start": 0.0, "end": 1.5, "text": "...", "speaker": "Speaker 1"},
                ...
            ],
            "speaker_names": {"Speaker 1": "Speaker 1", ...}
        }
    """
    audio_path = Path(audio_path)
    print(f"Diarizing {audio_path.name}...", file=sys.stderr)

    # Filter Whisper hallucinations: remove repeated short segments at the end
    whisper_segments = _filter_hallucinations(whisper_segments)

    # Merge Whisper's micro-segments into conversational turns (by pause gap)
    whisper_segments = _merge_whisper_segments(whisper_segments, max_gap=1.5)
    print(f"  Whisper segments after merge: {len(whisper_segments)}", file=sys.stderr)

    # Load audio
    audio = load_audio(audio_path, sr=_VAD_SR)
    print(f"  Audio loaded: {len(audio) / _VAD_SR:.1f}s", file=sys.stderr)

    # Detect speech segments via VAD
    speech_segments = detect_speech_segments(audio, sr=_VAD_SR)
    print(f"  Speech segments: {len(speech_segments)}", file=sys.stderr)

    if not speech_segments:
        # No speech detected — assign all to Speaker 1
        segments_out = []
        for ws in whisper_segments:
            segments_out.append({
                "start": ws["start"],
                "end": ws["end"],
                "text": ws["text"],
                "speaker": "Speaker 1",
                "speaker_id": 0,
            })
        return {
            "version": 1,
            "audio_file": str(audio_path),
            "segments": segments_out,
            "speaker_names": {"0": "Speaker 1"},
        }

    # Extract speaker embeddings for each speech segment
    embeddings = extract_speaker_embeddings(audio, _VAD_SR, speech_segments)
    print(f"  Embeddings: {embeddings.shape}", file=sys.stderr)

    # Cluster speakers
    speaker_labels = cluster_speakers(embeddings, n_speakers=n_speakers)
    n_detected = len(set(speaker_labels))
    print(f"  Speakers detected: {n_detected}", file=sys.stderr)

    # Assign speaker labels to whisper segments
    ws_speakers = _assign_speakers_to_whisper_segments(
        whisper_segments, speech_segments, speaker_labels
    )

    # Build output — use integer speaker_id to match the app's decoder
    speaker_names = {}
    segments_out = []
    for ws, spk_id in zip(whisper_segments, ws_speakers):
        speaker_names[str(spk_id)] = f"Speaker {spk_id + 1}"
        segments_out.append({
            "start": ws["start"],
            "end": ws["end"],
            "text": ws["text"],
            "speaker": f"Speaker {spk_id + 1}",
            "speaker_id": spk_id,
        })

    return {
        "version": 1,
        "audio_file": str(audio_path),
        "segments": segments_out,
        "speaker_names": speaker_names,
    }
