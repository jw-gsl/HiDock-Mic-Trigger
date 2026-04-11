"""Lightweight speaker diarization using Silero VAD + speaker embeddings.

Techniques inspired by silverstein/minutes:
- Audio normalization with peak-based retry for VAD
- Running-average speaker templates for better clustering
- Minimum 1.5s segment threshold for embeddings (short segments inherit)
- Post-clustering merge pass for over-split speakers
- Silence-to-padding replacement to prevent Whisper hallucination
- Whisper boundary fallback when VAD detects too little speech
- Max segment duration cap to prevent monster blocks
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import cosine as cosine_distance

from shared.audio_utils import extract_embedding, load_audio, segment_audio
from shared.models import ensure_silero_vad

_VAD_SR = 16000
_VAD_WINDOW_SIZE = 256  # 16ms at 16kHz (Silero VAD v5)

# Quality thresholds
_MIN_VAD_SEGMENTS_PER_MINUTE = 1.0
_MAX_MERGED_SEGMENT_SECONDS = 90.0
_MIN_SPEAKERS_FOR_LONG_AUDIO = 2
_MIN_EMBEDDING_DURATION = 1.5  # Skip segments shorter than this for embedding

_vad_session = None
_speaker_embed_session = None


def load_vad_model():
    """Load the Silero VAD ONNX model (cached after first call)."""
    global _vad_session
    if _vad_session is not None:
        return _vad_session
    import onnxruntime as ort
    model_path = ensure_silero_vad()
    _vad_session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
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
        _speaker_embed_session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        return _speaker_embed_session
    except ImportError:
        return None
    except Exception as e:
        print(f"Failed to load speaker embedding model: {e}", file=sys.stderr)
        return None


# ── Audio Preprocessing (inspired by minutes) ──────────────────────────────

def _normalize_audio(audio: np.ndarray, target_rms: float = 0.06) -> np.ndarray:
    """Normalize audio to a consistent RMS level for VAD."""
    rms = np.sqrt(np.mean(audio ** 2))
    if rms < 1e-6:
        return audio
    gain = min(target_rms / rms, 20.0)
    return np.clip(audio * gain, -1.0, 1.0).astype(np.float32)


def _normalize_peak(audio: np.ndarray, target_peak: float = 0.5) -> np.ndarray:
    """Normalize audio by peak amplitude (minutes approach for retry)."""
    peak = np.max(np.abs(audio))
    if peak < 1e-6:
        return audio
    gain = min(target_peak / peak, 20.0)
    return np.clip(audio * gain, -1.0, 1.0).astype(np.float32)


def _replace_silence_with_padding(
    audio: np.ndarray, sr: int = 16000,
    silence_threshold_s: float = 0.5, padding_s: float = 0.3,
) -> np.ndarray:
    """Replace long silence with short padding to prevent Whisper hallucination loops.

    From minutes: silence >500ms is replaced with 300ms of zeros, giving
    Whisper a natural segment boundary without triggering repetitive output.
    """
    chunk_size = int(0.05 * sr)  # 50ms chunks
    padding_samples = int(padding_s * sr)
    silence_chunks_needed = int(silence_threshold_s / 0.05)

    rms_values = []
    for i in range(0, len(audio) - chunk_size + 1, chunk_size):
        rms = np.sqrt(np.mean(audio[i:i + chunk_size] ** 2))
        rms_values.append(rms)

    if not rms_values:
        return audio

    # Adaptive noise floor from quietest 20% (minutes approach)
    sorted_rms = sorted(rms_values)
    noise_floor = sorted_rms[len(sorted_rms) // 5] * 4

    result_chunks = []
    silence_count = 0
    for i, rms in enumerate(rms_values):
        start = i * chunk_size
        end = start + chunk_size
        if rms < noise_floor:
            silence_count += 1
            if silence_count == silence_chunks_needed:
                # Replace accumulated silence with short padding
                result_chunks.append(np.zeros(padding_samples, dtype=np.float32))
            elif silence_count > silence_chunks_needed:
                continue  # Skip additional silence
            else:
                result_chunks.append(audio[start:end])
        else:
            silence_count = 0
            result_chunks.append(audio[start:end])

    return np.concatenate(result_chunks) if result_chunks else audio


# ── VAD ─────────────────────────────────────────────────────────────────────

def detect_speech_segments(
    audio: np.ndarray, sr: int = 16000,
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

    speech_probs = []
    for i in range(0, len(audio) - _VAD_WINDOW_SIZE + 1, _VAD_WINDOW_SIZE):
        chunk = audio[i:i + _VAD_WINDOW_SIZE][np.newaxis, :]
        try:
            out, state = session.run(None, {"input": chunk, "state": state, "sr": sr_tensor})
            speech_probs.append(float(out[0][0] if out[0].ndim > 0 else out[0]))
        except Exception:
            speech_probs.append(0.0)

    if not speech_probs:
        return []

    chunk_dur = _VAD_WINDOW_SIZE / _VAD_SR
    segments = []
    in_speech = False
    start = 0.0

    for i, p in enumerate(speech_probs):
        t = i * chunk_dur
        if p >= threshold and not in_speech:
            start = t
            in_speech = True
        elif p < threshold and in_speech:
            if t - start >= min_speech_duration:
                segments.append((start, t))
            in_speech = False

    if in_speech:
        end = len(speech_probs) * chunk_dur
        if end - start >= min_speech_duration:
            segments.append((start, end))

    # Merge across short silence gaps
    merged = []
    for seg in segments:
        if merged and seg[0] - merged[-1][1] < min_silence_duration:
            merged[-1] = (merged[-1][0], seg[1])
        else:
            merged.append(seg)

    return merged


def _merge_adjacent_speech(
    segments: list[tuple[float, float]],
    gap_threshold: float = 0.3,
    short_threshold: float = 0.5,
    absorb_gap: float = 1.0,
) -> list[tuple[float, float]]:
    """Merge adjacent speech segments for more stable embeddings (minutes approach).

    - Adjacent segments <300ms apart get merged
    - Short segments <500ms can absorb neighbors across up to 1s gaps
    """
    if len(segments) <= 1:
        return segments

    # First pass: merge across small gaps
    merged = [segments[0]]
    for seg in segments[1:]:
        if seg[0] - merged[-1][1] < gap_threshold:
            merged[-1] = (merged[-1][0], seg[1])
        else:
            merged.append(seg)

    # Second pass: short segments absorb neighbors
    result = []
    i = 0
    while i < len(merged):
        start, end = merged[i]
        dur = end - start
        if dur < short_threshold and i + 1 < len(merged):
            next_start, next_end = merged[i + 1]
            if next_start - end < absorb_gap:
                result.append((start, next_end))
                i += 2
                continue
        result.append((start, end))
        i += 1

    return result


def _whisper_segments_as_speech(whisper_segments: list[dict]) -> list[tuple[float, float]]:
    """Convert Whisper segments to speech regions (fallback when VAD fails)."""
    return [(ws["start"], ws["end"]) for ws in whisper_segments
            if ws.get("end", 0) > ws.get("start", 0)]


# ── Embeddings with minimum duration threshold ─────────────────────────────

def extract_speaker_embeddings(
    audio: np.ndarray, sr: int, segments: list[tuple[float, float]],
) -> tuple[np.ndarray, list[int]]:
    """Extract speaker embeddings, skipping segments shorter than 1.5s.

    Returns:
        Tuple of (embeddings array, valid_indices) where valid_indices
        maps each embedding back to its segment index.
    """
    speaker_session = _load_speaker_embed_model()
    if speaker_session is not None:
        print("  Using neural speaker embeddings (TitaNet)", file=sys.stderr)
    else:
        print("  Using MFCC speaker embeddings (fallback)", file=sys.stderr)

    chunks = segment_audio(audio, sr, segments)
    embeddings = []
    valid_indices = []

    for i, chunk in enumerate(chunks):
        dur = len(chunk) / sr
        if dur < _MIN_EMBEDDING_DURATION:
            continue  # Too short for reliable embedding
        emb = extract_embedding(chunk, sr=sr, onnx_session=speaker_session)
        embeddings.append(emb)
        valid_indices.append(i)

    skipped = len(segments) - len(valid_indices)
    if skipped > 0:
        print(f"  Skipped {skipped} segments <{_MIN_EMBEDDING_DURATION}s for embedding", file=sys.stderr)

    if not embeddings:
        # All segments too short — fall back to extracting from all anyway
        embeddings = [extract_embedding(c, sr=sr, onnx_session=speaker_session) for c in chunks]
        valid_indices = list(range(len(chunks)))

    return np.array(embeddings, dtype=np.float32), valid_indices


# ── Clustering with running-average templates + post-merge ──────────────────

def cluster_speakers(
    embeddings: np.ndarray,
    n_speakers: int | None = None,
    max_speakers: int = 10,
    distance_threshold: float = 1.2,
) -> list[int]:
    """Cluster speaker embeddings with post-clustering merge pass."""
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
        labels = list(fcluster(Z, t=float(n_speakers), criterion="maxclust"))
    else:
        labels = list(fcluster(Z, t=distance_threshold, criterion="distance"))
        if len(set(labels)) > max_speakers:
            labels = list(fcluster(Z, t=float(max_speakers), criterion="maxclust"))

    labels = [int(lbl) - 1 for lbl in labels]

    # Post-clustering merge pass (inspired by minutes)
    # Compute running-average centroid per cluster, merge similar ones
    labels = _post_cluster_merge(embeddings, labels, merge_threshold=distance_threshold * 0.8)

    return labels


def _post_cluster_merge(
    embeddings: np.ndarray, labels: list[int],
    merge_threshold: float = 0.4,
) -> list[int]:
    """Merge clusters whose centroids are close (catches over-splitting).

    Uses running-average centroids instead of just the first embedding.
    """
    unique_labels = sorted(set(labels))
    if len(unique_labels) <= 1:
        return labels

    # Compute centroid for each cluster (mean of all embeddings in cluster)
    centroids = {}
    for lbl in unique_labels:
        mask = [i for i, l in enumerate(labels) if l == lbl]
        centroid = embeddings[mask].mean(axis=0)
        norm = np.linalg.norm(centroid)
        if norm > 1e-10:
            centroid = centroid / norm
        centroids[lbl] = centroid

    # Find pairs to merge
    merge_map = {}
    for i, lbl_a in enumerate(unique_labels):
        if lbl_a in merge_map:
            continue
        for lbl_b in unique_labels[i + 1:]:
            if lbl_b in merge_map:
                continue
            dist = cosine_distance(centroids[lbl_a], centroids[lbl_b])
            if dist < merge_threshold:
                merge_map[lbl_b] = lbl_a
                print(f"  Post-merge: cluster {lbl_b} → {lbl_a} (dist={dist:.3f})", file=sys.stderr)

    if not merge_map:
        return labels

    # Apply transitive merges
    def resolve(lbl):
        while lbl in merge_map:
            lbl = merge_map[lbl]
        return lbl

    return [resolve(l) for l in labels]


# ── Speaker Assignment ──────────────────────────────────────────────────────

def _assign_speakers_to_whisper_segments(
    whisper_segments: list[dict],
    speech_segments: list[tuple[float, float]],
    speaker_labels: list[int],
    valid_indices: list[int] | None = None,
) -> list[int]:
    """Map each whisper segment to the closest speech segment's speaker.

    If valid_indices is provided, short segments that were skipped for
    embedding inherit from their nearest temporal neighbor.
    """
    # Build full label array including inherited labels for skipped segments
    full_labels = [0] * len(speech_segments)
    if valid_indices is not None:
        # Fill valid positions
        for vi, label in zip(valid_indices, speaker_labels):
            if vi < len(full_labels):
                full_labels[vi] = label

        # Forward-fill then backward-fill for gaps
        last_known = 0
        for i in range(len(full_labels)):
            if i in valid_indices:
                idx = valid_indices.index(i)
                last_known = speaker_labels[idx]
            full_labels[i] = last_known

        # Backward pass for any leading unknowns
        if valid_indices and valid_indices[0] > 0:
            first_label = speaker_labels[0]
            for i in range(valid_indices[0]):
                full_labels[i] = first_label
    else:
        full_labels = speaker_labels

    # Now assign whisper segments by overlap
    result = []
    for ws in whisper_segments:
        ws_start = ws["start"]
        ws_end = ws["end"]
        best_overlap = 0.0
        best_speaker = 0

        for idx, (ss_start, ss_end) in enumerate(speech_segments):
            if idx >= len(full_labels):
                break
            overlap = max(0.0, min(ws_end, ss_end) - max(ws_start, ss_start))
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = full_labels[idx]

        result.append(best_speaker)
    return result


# ── Hallucination filter ────────────────────────────────────────────────────

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


# ── Segment splitting ───────────────────────────────────────────────────────

def _split_long_segments(segments: list[dict], max_duration: float = _MAX_MERGED_SEGMENT_SECONDS) -> list[dict]:
    """Split segments exceeding max_duration at sentence boundaries."""
    result = []
    for seg in segments:
        dur = seg.get("end", 0) - seg.get("start", 0)
        if dur <= max_duration:
            result.append(seg)
            continue

        text = seg.get("text", "")
        # Split at sentence boundaries first
        sentences = text.replace("? ", "?\n").replace(". ", ".\n").replace("! ", "!\n").split("\n")
        sentences = [s.strip() for s in sentences if s.strip()]

        # If no/few sentence boundaries, or individual sentences are still too long, use commas
        avg_sentence_dur = dur / max(len(sentences), 1)
        if (len(sentences) <= 1 or avg_sentence_dur > max_duration) and ", " in text:
            clauses = text.split(", ")
            # Recombine into chunks of ~3-4 clauses
            sentences = []
            buf = []
            for clause in clauses:
                buf.append(clause)
                if len(buf) >= 3:
                    sentences.append(", ".join(buf))
                    buf = []
            if buf:
                sentences.append(", ".join(buf))

        # Last resort: split by word count when no punctuation at all
        if len(sentences) <= 1:
            words = text.split()
            if len(words) > 20:
                # Split into chunks of ~100 words (~90s of speech at ~130wpm)
                chunk_size = max(int(len(words) * max_duration / dur), 20)
                sentences = []
                for i in range(0, len(words), chunk_size):
                    sentences.append(" ".join(words[i:i + chunk_size]))
            else:
                result.append(seg)
                continue

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
            current_end = start + (current_chars / total_chars) * total_dur

            if current_end - current_start >= max_duration:
                result.append({**seg, "start": current_start, "end": current_end,
                               "text": " ".join(current_text)})
                current_start = current_end
                current_text = []

        if current_text:
            result.append({**seg, "start": current_start, "end": seg["end"],
                           "text": " ".join(current_text)})

    return result


# ── Main diarization pipeline ──────────────────────────────────────────────

def diarize(
    audio_path: str | Path,
    whisper_segments: list[dict],
    n_speakers: int | None = None,
) -> dict:
    """Run full diarization pipeline."""
    audio_path = Path(audio_path)
    print(f"Diarizing {audio_path.name}...", file=sys.stderr)

    whisper_segments = _filter_hallucinations(whisper_segments)
    print(f"  Whisper segments: {len(whisper_segments)}", file=sys.stderr)

    # Load audio
    audio_raw = load_audio(audio_path, sr=_VAD_SR)
    audio_duration = len(audio_raw) / _VAD_SR
    raw_rms = np.sqrt(np.mean(audio_raw ** 2))
    raw_peak = np.max(np.abs(audio_raw))

    # Step 1: RMS normalization for VAD
    audio_norm = _normalize_audio(audio_raw)
    norm_rms = np.sqrt(np.mean(audio_norm ** 2))
    print(f"  Audio: {audio_duration:.1f}s, RMS {raw_rms:.4f}→{norm_rms:.4f}, peak {raw_peak:.3f}", file=sys.stderr)

    # Step 2: Detect speech with normalized audio
    speech_segments = detect_speech_segments(audio_norm, sr=_VAD_SR)
    total_speech = sum(e - s for s, e in speech_segments)
    vad_per_min = len(speech_segments) / max(audio_duration / 60, 0.1)
    print(f"  VAD pass 1: {len(speech_segments)} segs, {total_speech:.0f}s speech, {vad_per_min:.1f}/min", file=sys.stderr)

    # Step 3: Peak normalization retry if VAD still poor (minutes approach)
    if vad_per_min < _MIN_VAD_SEGMENTS_PER_MINUTE and raw_peak < 0.5:
        audio_peak_norm = _normalize_peak(audio_raw, target_peak=0.5)
        speech_segments_retry = detect_speech_segments(audio_peak_norm, sr=_VAD_SR)
        retry_per_min = len(speech_segments_retry) / max(audio_duration / 60, 0.1)
        if retry_per_min > vad_per_min:
            speech_segments = speech_segments_retry
            audio_norm = audio_peak_norm
            total_speech = sum(e - s for s, e in speech_segments)
            vad_per_min = retry_per_min
            print(f"  VAD pass 2 (peak norm): {len(speech_segments)} segs, {total_speech:.0f}s, {vad_per_min:.1f}/min", file=sys.stderr)

    # Step 4: Whisper boundary fallback
    use_whisper_boundaries = False
    if len(whisper_segments) > 5 and vad_per_min < _MIN_VAD_SEGMENTS_PER_MINUTE:
        print(f"  VAD insufficient, using Whisper boundaries", file=sys.stderr)
        speech_segments = _whisper_segments_as_speech(whisper_segments)
        use_whisper_boundaries = True

    if not speech_segments:
        return _build_single_speaker_result(audio_path, whisper_segments)

    # Step 4b: Merge adjacent speech segments for better embeddings (minutes approach)
    # Adjacent segments <300ms apart get merged; short segments <500ms absorb neighbors up to 1s
    speech_segments = _merge_adjacent_speech(speech_segments, gap_threshold=0.3, short_threshold=0.5, absorb_gap=1.0)
    print(f"  After speech merge: {len(speech_segments)} segments", file=sys.stderr)

    # Step 5: Extract embeddings (skip <1.5s segments, use original audio)
    embeddings, valid_indices = extract_speaker_embeddings(audio_raw, _VAD_SR, speech_segments)
    print(f"  Embeddings: {embeddings.shape} ({len(valid_indices)} valid of {len(speech_segments)})", file=sys.stderr)

    # Step 6: Force minimum speakers for long meetings
    effective_n = n_speakers
    if effective_n is None and audio_duration > 300 and len(valid_indices) >= 4:
        effective_n = _MIN_SPEAKERS_FOR_LONG_AUDIO
        print(f"  Forcing min {effective_n} speakers (>{audio_duration / 60:.0f}min)", file=sys.stderr)

    # Step 7: Cluster with post-merge pass
    speaker_labels = cluster_speakers(embeddings, n_speakers=effective_n)
    n_detected = len(set(speaker_labels))
    print(f"  Speakers: {n_detected} (after post-merge)", file=sys.stderr)

    # Step 8: Assign speakers to whisper segments
    if use_whisper_boundaries:
        # 1:1 mapping — but need to handle valid_indices
        ws_speakers = [0] * len(whisper_segments)
        for vi, label in zip(valid_indices, speaker_labels):
            if vi < len(ws_speakers):
                ws_speakers[vi] = label
        # Fill gaps
        last = 0
        for i in range(len(ws_speakers)):
            if i in valid_indices:
                idx = valid_indices.index(i)
                last = speaker_labels[idx]
            ws_speakers[i] = last
    else:
        ws_speakers = _assign_speakers_to_whisper_segments(
            whisper_segments, speech_segments, speaker_labels, valid_indices
        )

    # Step 9: Renumber contiguously
    seen = []
    for s in ws_speakers:
        if s not in seen:
            seen.append(s)
    id_map = {old: new for new, old in enumerate(seen)}
    ws_speakers = [id_map[s] for s in ws_speakers]
    n_final = len(seen)
    print(f"  Final speakers: {n_final}", file=sys.stderr)

    # Step 10: Build output, merge same-speaker, split long segments
    speaker_names = {}
    raw_segments = []
    for ws, spk in zip(whisper_segments, ws_speakers):
        speaker_names[str(spk)] = f"Speaker {spk + 1}"
        raw_segments.append({
            "start": ws["start"], "end": ws["end"],
            "text": ws.get("text", "").strip(),
            "speaker": f"Speaker {spk + 1}", "speaker_id": spk,
        })

    # Merge consecutive same-speaker
    segments_out = []
    for seg in raw_segments:
        if not seg["text"]:
            continue
        if segments_out and segments_out[-1]["speaker_id"] == seg["speaker_id"]:
            segments_out[-1]["end"] = seg["end"]
            segments_out[-1]["text"] += " " + seg["text"]
        else:
            segments_out.append(dict(seg))

    segments_out = _split_long_segments(segments_out, max_duration=_MAX_MERGED_SEGMENT_SECONDS)
    # Second pass catches segments where the first split produced chunks still > max
    segments_out = _split_long_segments(segments_out, max_duration=_MAX_MERGED_SEGMENT_SECONDS)
    max_dur = max((s["end"] - s["start"] for s in segments_out), default=0)
    print(f"  Output: {len(segments_out)} segments (from {len(raw_segments)} raw), max {max_dur:.0f}s", file=sys.stderr)

    return {
        "version": 1,
        "audio_file": str(audio_path),
        "segments": segments_out,
        "speaker_names": speaker_names,
    }


def _build_single_speaker_result(audio_path, whisper_segments):
    """Build output with all segments as Speaker 1."""
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
                "start": ws["start"], "end": ws["end"], "text": text,
                "speaker": "Speaker 1", "speaker_id": 0,
            })
    segments_out = _split_long_segments(segments_out, max_duration=_MAX_MERGED_SEGMENT_SECONDS)
    return {
        "version": 1, "audio_file": str(audio_path),
        "segments": segments_out, "speaker_names": {"0": "Speaker 1"},
    }
