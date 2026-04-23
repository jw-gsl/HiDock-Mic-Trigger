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
from shared.voice_library_lite import identify_speaker

_VAD_SR = 16000
_VAD_WINDOW_SIZE = 256  # 16ms at 16kHz (Silero VAD v5)

# Quality thresholds
_MIN_VAD_SEGMENTS_PER_MINUTE = 1.0
_MAX_MERGED_SEGMENT_SECONDS = 30.0
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

def _compute_density_prior(
    speech_segs: list[tuple[float, float]],
    audio_duration_s: float,
    embeddings: np.ndarray | None = None,
) -> tuple[int, int]:
    """Compute a (min_k, preferred_k) prior for speaker count from turn-taking.

    Uses two signals that are cheap to compute and strong evidence of group size:
      1. VAD segments per minute — high density of short bursts implies many
         speakers taking short turns.
      2. Embedding spread (optional) — if pairwise distances among a sample of
         embeddings cluster into a wide spread, that's strong evidence of
         multiple distinct voice identities.

    Returns (min_k, preferred_k): a floor for clustering, and the value we'd
    bias silhouette scoring toward.
    """
    if audio_duration_s < 60 or len(speech_segs) < 4:
        return (1, 2)

    vad_per_min = len(speech_segs) / max(audio_duration_s / 60.0, 1.0)
    mean_seg_dur = np.mean([e - s for s, e in speech_segs])

    # Turn-taking density score (higher = more conversational)
    # A 1:1 tends to have ~5–10 segs/min with long segs (~6–12s avg).
    # A group of 6+ tends to have 20+ segs/min with short segs (<3s avg).
    if vad_per_min >= 30 and mean_seg_dur < 2.5:
        density_prior = 6
    elif vad_per_min >= 20 and mean_seg_dur < 3.5:
        density_prior = 5
    elif vad_per_min >= 12 and mean_seg_dur < 5.0:
        density_prior = 4
    elif vad_per_min >= 8:
        density_prior = 3
    else:
        density_prior = 2

    # Embedding spread refinement — if we have embeddings, use pairwise cosine
    # distance to refine the prior. High spread → bump prior up.
    if embeddings is not None and len(embeddings) >= 6:
        # Sample pairwise distances (not all, to stay cheap on big arrays)
        sample = embeddings[:: max(1, len(embeddings) // 20)][:20]
        dists = []
        for i in range(len(sample)):
            for j in range(i + 1, len(sample)):
                a, b = sample[i], sample[j]
                cos_sim = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))
                dists.append(1.0 - cos_sim)
        if dists:
            # Top-decile distance — how different are the most dissimilar pairs?
            top_decile = float(np.percentile(dists, 90))
            spread_ratio = float(np.mean(dists))
            # Neural embeddings: distance ~0.1 = same speaker, ~0.5+ = different
            if top_decile > 0.6 and spread_ratio > 0.35:
                density_prior = max(density_prior, 5)
            elif top_decile > 0.5 and spread_ratio > 0.28:
                density_prior = max(density_prior, 4)
            elif top_decile > 0.4 and spread_ratio > 0.22:
                density_prior = max(density_prior, 3)

    # Floor is the prior minus 1 — we'd rather slightly under-cluster than force
    # a speaker count we're not confident in. The preferred value guides scoring.
    min_k = max(2, density_prior - 1)
    return (min_k, density_prior)


def estimate_speaker_count(
    audio: np.ndarray, sr: int = 16000,
    max_speakers: int = 10, n_samples: int = 60,
) -> int:
    """Estimate the number of speakers using VAD density + embedding spread.

    Takes ~5–10 seconds. Combines:
      - Turn-taking density (VAD segs/min, mean segment duration)
      - Embedding spread (pairwise cosine distance across sampled clips)
      - Silhouette scoring on k=2..max_speakers, with a bell-curve penalty
        centred on the density-derived prior.

    Args:
        audio: Full audio array.
        sr: Sample rate.
        max_speakers: Maximum speakers to test (default 10, up from 6).
        n_samples: Number of sample points to extract (default 60, up from 30).

    Returns:
        Estimated number of speakers (minimum 2).
    """
    from sklearn.metrics import silhouette_score

    audio_duration = len(audio) / sr
    if audio_duration < 30:
        return 2  # Too short to estimate

    # Use VAD to find actual speech segments, then sample from those
    audio_norm = _normalize_audio(audio)
    speech_segs = detect_speech_segments(audio_norm, sr=sr)

    if len(speech_segs) < 4:
        return 2

    # Merge adjacent speech for more stable clips
    speech_segs = _merge_adjacent_speech(speech_segs)

    # Scale samples to recording length — long recordings need more data points
    target_samples = n_samples
    if audio_duration > 1800:  # >30 min
        target_samples = max(n_samples, 100)

    # Pick up to target_samples speech segments, spread across the meeting
    step = max(1, len(speech_segs) // target_samples)
    selected = speech_segs[::step][:target_samples]

    # Extract embeddings from speech segments (use original audio for quality)
    speaker_session = _load_speaker_embed_model()
    embeddings = []

    for start, end in selected:
        dur = end - start
        if dur < 1.5:
            continue
        # Cap at 5 seconds
        end = min(end, start + 5.0)
        s = int(start * sr)
        e = int(end * sr)
        chunk = audio[s:e]
        if len(chunk) < sr:
            continue
        try:
            emb = extract_embedding(chunk, sr=sr, onnx_session=speaker_session)
            embeddings.append(emb)
        except Exception:
            continue

    if len(embeddings) < 4:
        return 2  # Not enough data

    embeddings_arr = np.array(embeddings, dtype=np.float32)

    # Compute density-based prior (uses VAD pattern + embedding spread)
    min_k, preferred_k = _compute_density_prior(speech_segs, audio_duration, embeddings_arr)

    # Try different k values with bell-curve penalty centred on preferred_k.
    # Previously a flat penalty that favoured k<=4 regardless of content;
    # now the penalty follows our density-derived expectation.
    best_k = preferred_k
    best_score = -1.0
    k_upper = min(max_speakers + 1, len(embeddings))

    for k in range(min_k, k_upper):
        try:
            from scipy.cluster.hierarchy import fcluster, linkage
            Z = linkage(embeddings_arr, method="average", metric="cosine")
            labels = fcluster(Z, t=float(k), criterion="maxclust")
            labels = [int(l) - 1 for l in labels]

            if len(set(labels)) < 2:
                continue

            score = silhouette_score(embeddings_arr, labels, metric="cosine")

            # Bell-curve penalty: maximum weight at preferred_k, falls off
            # gradually on both sides. Tolerates ±2 from prior without penalty.
            distance_from_prior = abs(k - preferred_k)
            if distance_from_prior <= 1:
                penalty = 1.0
            elif distance_from_prior <= 2:
                penalty = 0.95
            else:
                penalty = max(0.5, 1.0 - (distance_from_prior - 2) * 0.08)
            adjusted_score = score * penalty

            if adjusted_score > best_score:
                best_score = adjusted_score
                best_k = k
        except Exception:
            continue

    # Never return below the density floor — this is the main fix for group
    # recordings where silhouette was collapsing to k=2.
    return max(best_k, min_k)


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

    # Assign whisper segments to VAD speech segments.
    #
    # First try max-overlap. When a whisper segment falls in a gap
    # between VAD segments (music, silence, non-speech, Whisper
    # hallucinating during a quiet moment) there is NO overlap with
    # any VAD segment — previously `best_speaker` stayed hardcoded at
    # 0, which meant every orphan whisper segment silently defaulted
    # to Speaker 0. Over a long call with normal non-speech gaps
    # that's hundreds of segments leaking onto Speaker 0 and producing
    # extreme 95/5-style skews in the final distribution.
    #
    # New behaviour: if no overlap, fall back to the nearest-by-midpoint
    # VAD segment's speaker. That's a better guess than "speaker 0"
    # because it assumes the speaker didn't change during a brief
    # silence — which is what conversational audio usually looks like.
    result = []
    for ws in whisper_segments:
        ws_start = ws["start"]
        ws_end = ws["end"]
        best_overlap = 0.0
        best_speaker: int | None = None

        # Pass 1: max overlap
        for idx, (ss_start, ss_end) in enumerate(speech_segments):
            if idx >= len(full_labels):
                break
            overlap = max(0.0, min(ws_end, ss_end) - max(ws_start, ss_start))
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = full_labels[idx]

        # Pass 2: no overlap — nearest VAD segment by midpoint distance
        if best_speaker is None:
            ws_mid = (ws_start + ws_end) / 2.0
            best_dist = float("inf")
            for idx, (ss_start, ss_end) in enumerate(speech_segments):
                if idx >= len(full_labels):
                    break
                ss_mid = (ss_start + ss_end) / 2.0
                d = abs(ws_mid - ss_mid)
                if d < best_dist:
                    best_dist = d
                    best_speaker = full_labels[idx]

        # Absolute fallback (e.g. no VAD segments at all)
        result.append(best_speaker if best_speaker is not None else 0)
    return result


# ── Non-speech event anonymization (from minutes v0.11.0) ──────────────────

import re

_NON_SPEECH_PATTERN = re.compile(
    r"^\s*\[(?:laughter|laughing|cough|coughing|sneeze|applause|music|noise|"
    r"silence|inaudible|crosstalk|background noise|phone ringing|door|"
    r"breathing|sigh|clearing throat|um|uh)\]\s*$",
    re.IGNORECASE,
)


def _is_non_speech(text: str) -> bool:
    """Check if a segment is a non-speech event marker."""
    return bool(_NON_SPEECH_PATTERN.match(text.strip()))


def _anonymize_non_speech(segments: list[dict]) -> list[dict]:
    """Remove speaker attribution from non-speech event segments.

    [laughter], [cough] etc. don't belong to any speaker — they're
    ambient events. Keeping them as anonymous preserves the information
    without misattributing noise to a person.
    """
    result = []
    for seg in segments:
        if _is_non_speech(seg.get("text", "")):
            result.append({
                **seg,
                "speaker": "",
                "speaker_id": -1,  # -1 = no speaker
            })
        else:
            result.append(seg)
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

    # Step 6: Estimate speaker count (pre-pass)
    effective_n = n_speakers
    if effective_n is None and audio_duration > 60 and len(valid_indices) >= 4:
        estimated = estimate_speaker_count(audio_raw, sr=_VAD_SR)
        # Use density-based floor for long recordings — combines VAD turn-taking
        # with embedding spread. Short recordings keep a minimum of 1.
        if audio_duration > 300 and len(embeddings) >= 6:
            floor_min_k, _ = _compute_density_prior(speech_segments, audio_duration, embeddings)
            effective_n = max(estimated, floor_min_k)
        else:
            effective_n = max(estimated, _MIN_SPEAKERS_FOR_LONG_AUDIO if audio_duration > 300 else 1)
        vad_per_min_log = len(speech_segments) / max(audio_duration / 60.0, 1.0)
        print(
            f"  Speaker count estimate: {estimated} (using {effective_n}, "
            f"vad/min={vad_per_min_log:.1f}, dur={audio_duration:.0f}s)",
            file=sys.stderr,
        )

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

    # Step 10: Auto-match against voice library (from minutes v0.10.0)
    speaker_names = {}
    for spk_id in range(n_final):
        # Compute centroid for this speaker
        spk_indices = [i for i, s in enumerate(ws_speakers) if s == spk_id]
        if not spk_indices:
            speaker_names[str(spk_id)] = f"Speaker {spk_id + 1}"
            continue

        # Get embeddings for this speaker's whisper segments via speech segments
        spk_embeddings = []
        for wi in spk_indices:
            ws = whisper_segments[wi]
            ws_mid = (ws["start"] + ws["end"]) / 2
            # Find the closest speech segment with a valid embedding
            best_dist = float("inf")
            best_emb_idx = None
            for vi, emb_idx in enumerate(valid_indices):
                if emb_idx < len(speech_segments):
                    ss_start, ss_end = speech_segments[emb_idx]
                    ss_mid = (ss_start + ss_end) / 2
                    dist = abs(ws_mid - ss_mid)
                    if dist < best_dist:
                        best_dist = dist
                        best_emb_idx = vi
            if best_emb_idx is not None and best_emb_idx < len(embeddings):
                spk_embeddings.append(embeddings[best_emb_idx])

        if spk_embeddings:
            centroid = np.mean(spk_embeddings, axis=0).astype(np.float32)
            norm = np.linalg.norm(centroid)
            if norm > 1e-10:
                centroid = centroid / norm

            # Check voice library
            matched_name, confidence = identify_speaker(centroid, threshold=0.55)
            if matched_name:
                speaker_names[str(spk_id)] = matched_name
                print(f"  Auto-matched speaker {spk_id} → {matched_name} ({confidence:.0%})", file=sys.stderr)
            else:
                speaker_names[str(spk_id)] = f"Speaker {spk_id + 1}"
        else:
            speaker_names[str(spk_id)] = f"Speaker {spk_id + 1}"

    # Step 11: Build output, merge same-speaker, split long segments
    raw_segments = []
    for ws, spk in zip(whisper_segments, ws_speakers):
        raw_segments.append({
            "start": ws["start"], "end": ws["end"],
            "text": ws.get("text", "").strip(),
            "speaker": speaker_names.get(str(spk), f"Speaker {spk + 1}"),
            "speaker_id": spk,
        })

    # Anonymize non-speech events (from minutes v0.11.0)
    raw_segments = _anonymize_non_speech(raw_segments)

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
