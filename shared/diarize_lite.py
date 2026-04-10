"""Lightweight speaker diarization using Silero VAD + speaker embeddings.

Detects speech segments via Silero VAD (ONNX), extracts speaker embeddings
(neural TitaNet when available, MFCC fallback otherwise), and clusters them
with agglomerative clustering to assign speaker labels to whisper transcript
segments.

Key improvements for HiDock audio:
- Audio normalization before VAD (handles varying recording levels)
- Whisper-segment-based embedding extraction (more granular than VAD)
- Fallback to Whisper boundaries when VAD detects too little speech
- Max segment duration cap to prevent monster merged blocks
- Minimum 2 speakers for meetings >5 minutes
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

# Quality thresholds
_MIN_VAD_SEGMENTS_PER_MINUTE = 1.0  # Below this, VAD is failing
_MAX_MERGED_SEGMENT_SECONDS = 90.0  # Cap merged segments at this duration
_MIN_SPEAKERS_FOR_LONG_AUDIO = 2  # Force at least 2 speakers for >5min audio

_vad_session = None
_speaker_embed_session = None


def load_vad_model():
    """Load the Silero VAD ONNX model (cached after first call)."""
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
    """Load the TitaNet speaker embedding ONNX model (cached after first call)."""
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


def _normalize_audio(audio: np.ndarray, target_rms: float = 0.06) -> np.ndarray:
    """Normalize audio to a consistent RMS level.

    HiDock recordings vary in level (RMS 0.005-0.063). The Silero VAD
    produces near-zero probabilities below RMS ~0.04. Normalizing to
    a consistent level ensures VAD works reliably.
    """
    rms = np.sqrt(np.mean(audio ** 2))
    if rms < 1e-6:
        return audio  # silence, don't amplify noise
    gain = target_rms / rms
    # Clamp gain to avoid extreme amplification of very quiet recordings
    gain = min(gain, 20.0)
    normalized = audio * gain
    # Clip to prevent clipping
    return np.clip(normalized, -1.0, 1.0).astype(np.float32)


def detect_speech_segments(
    audio: np.ndarray,
    sr: int = 16000,
    threshold: float = 0.08,
    min_speech_duration: float = 0.15,
    min_silence_duration: float = 1.5,
) -> list[tuple[float, float]]:
    """Detect speech segments using Silero VAD."""
    if sr != _VAD_SR:
        raise ValueError(f"Silero VAD requires {_VAD_SR}Hz audio, got {sr}Hz")

    session = load_vad_model()

    state = np.zeros((2, 1, 128), dtype=np.float32)
    sr_tensor = np.array(_VAD_SR, dtype=np.int64)

    n_samples = len(audio)
    speech_probs = []

    for i in range(0, n_samples - _VAD_WINDOW_SIZE + 1, _VAD_WINDOW_SIZE):
        chunk = audio[i : i + _VAD_WINDOW_SIZE]
        chunk = chunk[np.newaxis, :]

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
            print(f"VAD inference error: {e}", file=sys.stderr)
            speech_probs.append(0.0)

    if not speech_probs:
        return []

    is_speech = [p >= threshold for p in speech_probs]

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

    if in_speech:
        end = len(is_speech) * chunk_duration
        if end - start >= min_speech_duration:
            segments.append((start, end))

    merged = []
    for seg in segments:
        if merged and seg[0] - merged[-1][1] < min_silence_duration:
            merged[-1] = (merged[-1][0], seg[1])
        else:
            merged.append(seg)

    return merged


def _whisper_segments_as_speech(whisper_segments: list[dict]) -> list[tuple[float, float]]:
    """Convert Whisper segments to (start, end) tuples for use as speech regions.

    Used as fallback when VAD fails to detect enough speech.
    """
    segments = []
    for ws in whisper_segments:
        start = ws.get("start", 0.0)
        end = ws.get("end", start)
        if end > start:
            segments.append((start, end))
    return segments


def extract_speaker_embeddings(
    audio: np.ndarray,
    sr: int,
    segments: list[tuple[float, float]],
) -> np.ndarray:
    """Extract speaker embeddings for each segment."""
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
    """Cluster speaker embeddings using agglomerative clustering."""
    n = len(embeddings)
    if n == 0:
        return []
    if n == 1:
        return [0]

    embed_dim = embeddings.shape[1]
    if embed_dim >= 128 and distance_threshold == 1.2:
        distance_threshold = 0.5

    Z = linkage(embeddings, method="average", metric="cosine")

    if n_speakers is not None and isinstance(n_speakers, int) and n_speakers > 0:
        labels = fcluster(Z, t=float(n_speakers), criterion="maxclust")
    else:
        labels = fcluster(Z, t=distance_threshold, criterion="distance")
        n_clusters = len(set(labels))
        if n_clusters > max_speakers:
            labels = fcluster(Z, t=float(max_speakers), criterion="maxclust")

    labels = [int(lbl) - 1 for lbl in labels]
    return labels


def _assign_speakers_to_whisper_segments(
    whisper_segments: list[dict],
    speech_segments: list[tuple[float, float]],
    speaker_labels: list[int],
) -> list[int]:
    """Map each whisper segment to the closest speech segment's speaker."""
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

    last_texts = [s.get("text", "").strip().lower() for s in segments[-max_repeats:]]
    if len(set(last_texts)) == 1 and len(last_texts[0]) < 20:
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


def _split_long_segments(segments: list[dict], max_duration: float = _MAX_MERGED_SEGMENT_SECONDS) -> list[dict]:
    """Split segments that exceed max_duration at sentence boundaries."""
    result = []
    for seg in segments:
        dur = seg.get("end", 0) - seg.get("start", 0)
        if dur <= max_duration:
            result.append(seg)
            continue

        # Split the text at sentence boundaries
        text = seg.get("text", "")
        sentences = text.replace("? ", "?\n").replace(". ", ".\n").replace("! ", "!\n").split("\n")
        sentences = [s.strip() for s in sentences if s.strip()]

        if len(sentences) <= 1:
            result.append(seg)
            continue

        # Distribute sentences proportionally across the time range
        total_chars = sum(len(s) for s in sentences)
        if total_chars == 0:
            result.append(seg)
            continue

        start = seg["start"]
        total_dur = seg["end"] - seg["start"]
        current_start = start
        current_text = []
        current_chars = 0

        for sentence in sentences:
            current_text.append(sentence)
            current_chars += len(sentence)
            elapsed = (current_chars / total_chars) * total_dur
            current_end = start + elapsed

            if current_end - current_start >= max_duration and len(current_text) > 0:
                result.append({
                    **seg,
                    "start": current_start,
                    "end": current_end,
                    "text": " ".join(current_text),
                })
                current_start = current_end
                current_text = []

        if current_text:
            result.append({
                **seg,
                "start": current_start,
                "end": seg["end"],
                "text": " ".join(current_text),
            })

    return result


def diarize(
    audio_path: str | Path,
    whisper_segments: list[dict],
    n_speakers: int | None = None,
) -> dict:
    """Run full diarization on an audio file with pre-computed whisper segments."""
    audio_path = Path(audio_path)
    print(f"Diarizing {audio_path.name}...", file=sys.stderr)

    # Filter Whisper hallucinations
    whisper_segments = _filter_hallucinations(whisper_segments)
    print(f"  Whisper segments: {len(whisper_segments)}", file=sys.stderr)

    # Load and NORMALIZE audio — critical for consistent VAD
    audio_raw = load_audio(audio_path, sr=_VAD_SR)
    raw_rms = np.sqrt(np.mean(audio_raw ** 2))
    audio = _normalize_audio(audio_raw)
    norm_rms = np.sqrt(np.mean(audio ** 2))
    audio_duration = len(audio) / _VAD_SR
    print(f"  Audio: {audio_duration:.1f}s, RMS {raw_rms:.4f} → {norm_rms:.4f} (normalized)", file=sys.stderr)

    # Detect speech segments via VAD
    speech_segments = detect_speech_segments(audio, sr=_VAD_SR)
    total_speech = sum(e - s for s, e in speech_segments)
    vad_segs_per_min = len(speech_segments) / max(audio_duration / 60, 0.1)
    print(f"  VAD: {len(speech_segments)} segments, {total_speech:.0f}s speech ({100 * total_speech / max(audio_duration, 1):.0f}%), {vad_segs_per_min:.1f}/min", file=sys.stderr)

    # FALLBACK: if VAD found too few segments, use Whisper segment boundaries
    # This handles cases where even normalized audio doesn't trigger VAD well
    use_whisper_boundaries = False
    if len(whisper_segments) > 5 and vad_segs_per_min < _MIN_VAD_SEGMENTS_PER_MINUTE:
        print(f"  VAD insufficient ({vad_segs_per_min:.1f}/min < {_MIN_VAD_SEGMENTS_PER_MINUTE}), using Whisper boundaries", file=sys.stderr)
        speech_segments = _whisper_segments_as_speech(whisper_segments)
        use_whisper_boundaries = True
        print(f"  Whisper boundaries: {len(speech_segments)} segments", file=sys.stderr)

    if not speech_segments:
        # Still nothing — assign all to Speaker 1
        segments_out = _build_single_speaker_output(whisper_segments)
        return {
            "version": 1,
            "audio_file": str(audio_path),
            "segments": segments_out,
            "speaker_names": {"0": "Speaker 1"},
        }

    # Extract speaker embeddings
    # Use the ORIGINAL (non-normalized) audio for embeddings — normalization
    # can distort the speaker characteristics
    embeddings = extract_speaker_embeddings(audio_raw, _VAD_SR, speech_segments)
    print(f"  Embeddings: {embeddings.shape}", file=sys.stderr)

    # Force minimum 2 speakers for long meetings
    effective_n_speakers = n_speakers
    if effective_n_speakers is None and audio_duration > 300:  # >5 min
        effective_n_speakers = max(2, effective_n_speakers or 0) or None
        # Only force if we have enough data points
        if len(speech_segments) >= 4:
            effective_n_speakers = _MIN_SPEAKERS_FOR_LONG_AUDIO
            print(f"  Forcing min {effective_n_speakers} speakers (audio >{audio_duration / 60:.0f}min)", file=sys.stderr)

    # Cluster speakers
    speaker_labels = cluster_speakers(embeddings, n_speakers=effective_n_speakers)
    n_detected = len(set(speaker_labels))
    print(f"  Speakers detected: {n_detected}", file=sys.stderr)

    # Assign speakers to Whisper segments
    if use_whisper_boundaries:
        # When using Whisper boundaries, the speech_segments ARE the whisper segments
        # so the assignment is 1:1
        ws_speakers = speaker_labels
    else:
        ws_speakers = _assign_speakers_to_whisper_segments(
            whisper_segments, speech_segments, speaker_labels
        )

    # Renumber speaker IDs contiguously
    seen_ids = []
    for spk_id in ws_speakers:
        if spk_id not in seen_ids:
            seen_ids.append(spk_id)
    id_map = {old: new for new, old in enumerate(seen_ids)}
    ws_speakers = [id_map[s] for s in ws_speakers]
    n_detected = len(seen_ids)
    print(f"  Speakers (renumbered): {n_detected}", file=sys.stderr)

    # Build labeled segments, merge consecutive same-speaker
    speaker_names = {}
    raw_segments = []
    for ws, spk_id in zip(whisper_segments, ws_speakers):
        speaker_names[str(spk_id)] = f"Speaker {spk_id + 1}"
        raw_segments.append({
            "start": ws["start"],
            "end": ws["end"],
            "text": ws.get("text", "").strip(),
            "speaker": f"Speaker {spk_id + 1}",
            "speaker_id": spk_id,
        })

    # Merge consecutive same-speaker segments
    segments_out = []
    for seg in raw_segments:
        if not seg["text"]:
            continue
        if segments_out and segments_out[-1]["speaker_id"] == seg["speaker_id"]:
            segments_out[-1]["end"] = seg["end"]
            segments_out[-1]["text"] += " " + seg["text"]
        else:
            segments_out.append(dict(seg))

    # Split any monster segments that exceeded the cap
    segments_out = _split_long_segments(segments_out, max_duration=_MAX_MERGED_SEGMENT_SECONDS)

    print(f"  Output segments: {len(segments_out)} (merged from {len(raw_segments)})", file=sys.stderr)

    return {
        "version": 1,
        "audio_file": str(audio_path),
        "segments": segments_out,
        "speaker_names": speaker_names,
    }


def _build_single_speaker_output(whisper_segments: list[dict]) -> list[dict]:
    """Build output with all segments assigned to Speaker 1."""
    segments_out = []
    for ws in whisper_segments:
        text = ws.get("text", "").strip()
        if not text:
            continue
        if segments_out:
            segments_out[-1]["end"] = ws["end"]
            segments_out[-1]["text"] += " " + text
        else:
            segments_out.append({
                "start": ws["start"],
                "end": ws["end"],
                "text": text,
                "speaker": "Speaker 1",
                "speaker_id": 0,
            })

    # Split at the cap
    segments_out = _split_long_segments(segments_out, max_duration=_MAX_MERGED_SEGMENT_SECONDS)
    return segments_out
