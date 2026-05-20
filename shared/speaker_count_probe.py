"""Pre-flight speaker count estimator.

Cheap pass that estimates how many distinct speakers a recording
contains, without doing full diarization. Used by the dispatcher
when `pipeline_backends.json` has `"diarization": "auto"`:

  estimate <= 4   →  Sortformer (fast, neat on small meetings)
  estimate >= 5   →  pyannote   (no 4-speaker cap)

Pipeline:
  1. Step through the audio at fixed strides (one window every ~15s).
  2. Pick a fixed-size embedding window (~3s) at each stride.
  3. Quick energy gate to skip windows that are mostly silence —
     much cheaper than running a full VAD pass, and we don't need
     speech-vs-silence precision since we're sampling, not segmenting.
  4. Embed each window via TitaNet (the same ONNX session the lite
     diarizer + voice library use).
  5. Agglomerative clustering with a fixed cosine distance threshold.
  6. Return (estimated_count, confidence) where confidence is the
     intra-cluster vs inter-cluster cosine gap.

Wall-clock target: under 10s for a 60-min file on Apple Silicon.

Intentionally NOT a full diarization — we don't try to label segments,
we just count clusters. Looser threshold than the lite diarizer because
under-clustering (estimate too low) routes to Sortformer and that's
fine for ≤4 speakers; over-clustering (estimate too high) routes to
pyannote which handles arbitrary speaker counts so that's also fine.

Why fixed-stride windows instead of VAD?
- The shared Silero VAD detect_speech_segments aggressively merges
  across silence gaps (default 1.5s min-silence threshold), producing
  big "speech blocks" rather than speaker-turn-sized chunks. For
  diarization that's what you want; for *counting* it's the opposite —
  long blocks tend to contain a single dominant speaker, so the
  embeddings cluster too easily into one bucket.
- Fixed strides guarantee even coverage across the recording (so a
  speaker who only appears in the final 5 minutes still gets sampled)
  and gives us enough independent embeddings (~60-120 for a long
  meeting) for the clustering to be meaningful.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np


# Tuning knobs. Picked to be conservative — we'd rather slightly
# over-estimate (route to pyannote, no harm) than under-estimate
# (route to Sortformer and hit the cap).
_WINDOW_SECONDS = 3.0
_STRIDE_SECONDS = 15.0
_MIN_WINDOWS = 10        # below this we don't have enough signal to cluster
_MAX_WINDOWS = 120       # cap probe wall-clock on multi-hour files
_ENERGY_MIN_RMS = 0.005  # below this the window is essentially silent — skip
# Threshold + linkage tuned against the available labelled-ish data
# (Rec78=3 spk, Rec76=2 spk, Rec82=4 spk per Sortformer). Complete-link
# (max pairwise distance) is more conservative than average-link and
# gives the right answers on those recordings with thr=0.50. Average-link
# under-clustered (collapsed real speakers); single-link was too jumpy.
_COSINE_DISTANCE_THRESHOLD = 0.50
_LINKAGE_METHOD = "complete"


def estimate_speaker_count(
    audio_path: str | Path,
    sr: int = 16000,
) -> tuple[int, float]:
    """Estimate the number of distinct speakers in the recording.

    Returns ``(estimated_count, confidence)``:
      - ``estimated_count`` ≥ 1, capped at the number of windows we
        could embed (typically 1..MAX_WINDOWS).
      - ``confidence`` ∈ [0, 1] — the gap between intra-cluster and
        inter-cluster cosine similarity. > 0.15 is "pretty confident".

    Logs a one-line summary to stderr so the dispatcher's routing
    decision is auditable from the menubar log.

    Returns ``(1, 0.0)`` when the audio is too short or essentially
    silent — the router will fall through to its default backend.
    """
    from shared.audio_utils import extract_embedding, load_audio
    from shared.models import ensure_speaker_embed

    audio = load_audio(audio_path, sr=sr)
    total_dur = len(audio) / sr
    if total_dur < 30:
        # Sub-30s clip — likely too short to need routing at all.
        print(f"speaker-count-probe: audio too short ({total_dur:.1f}s); assuming 1 speaker", file=sys.stderr)
        return (1, 0.0)

    # Load TitaNet once for all probe embeddings.
    try:
        import onnxruntime as ort
        model_path = ensure_speaker_embed()
        session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    except Exception as e:
        # Without TitaNet we can't probe — fall through to a safe default
        # so the router uses whatever the lite/sortformer fallback is.
        print(f"speaker-count-probe: TitaNet load failed ({e}); assuming 2 speakers", file=sys.stderr)
        return (2, 0.0)

    win_samples = int(_WINDOW_SECONDS * sr)
    stride_samples = int(_STRIDE_SECONDS * sr)

    # Walk the audio at fixed strides, picking each window only if it
    # has enough energy to be speech. Bound the total embedding count
    # so probing a multi-hour file doesn't run for minutes — if we'd
    # exceed _MAX_WINDOWS, widen the stride proportionally.
    n_possible = max(1, (len(audio) - win_samples) // stride_samples + 1)
    if n_possible > _MAX_WINDOWS:
        stride_samples = (len(audio) - win_samples) // _MAX_WINDOWS

    embeddings: list[np.ndarray] = []
    pos = 0
    skipped_silent = 0
    while pos + win_samples <= len(audio) and len(embeddings) < _MAX_WINDOWS:
        chunk = audio[pos:pos + win_samples]
        # Quick energy gate — skip windows that are essentially silence.
        rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))
        if rms < _ENERGY_MIN_RMS:
            skipped_silent += 1
            pos += stride_samples
            continue
        try:
            emb = extract_embedding(chunk, sr=sr, onnx_session=session)
        except Exception:
            pos += stride_samples
            continue
        norm = float(np.linalg.norm(emb))
        if norm < 1e-10:
            pos += stride_samples
            continue
        embeddings.append((emb / norm).astype(np.float32))
        pos += stride_samples

    if len(embeddings) < _MIN_WINDOWS:
        print(
            f"speaker-count-probe: only {len(embeddings)} usable window(s) "
            f"(skipped {skipped_silent} silent); assuming 1 speaker",
            file=sys.stderr,
        )
        return (1, 0.0)

    emb_arr = np.stack(embeddings)
    estimated, confidence = _cluster_count(emb_arr)

    print(
        f"speaker-count-probe: {len(embeddings)} embedded windows "
        f"(skipped {skipped_silent} silent), "
        f"estimated {estimated} speaker(s), confidence {confidence:.2f}",
        file=sys.stderr,
    )
    return (estimated, confidence)


def _cluster_count(embeddings: np.ndarray) -> tuple[int, float]:
    """Run agglomerative clustering with cosine distance and a fixed
    threshold. Return ``(n_clusters, confidence)``.

    Confidence is the ratio of inter-cluster mean distance to
    intra-cluster mean distance, normalised — values around 0.15-0.30
    indicate a clear gap between clusters; below 0.05 suggests the
    embeddings are too similar to confidently say more than 1
    speaker exists."""
    from scipy.cluster.hierarchy import fcluster, linkage

    # Cosine distance is the right metric for L2-normalised embeddings.
    Z = linkage(embeddings, method=_LINKAGE_METHOD, metric="cosine")
    labels = fcluster(Z, t=_COSINE_DISTANCE_THRESHOLD, criterion="distance")
    n = int(labels.max())

    # Confidence: how separated are the discovered clusters?
    confidence = _separation_confidence(embeddings, labels)
    return (n, confidence)


def _separation_confidence(embeddings: np.ndarray, labels: np.ndarray) -> float:
    """Cosine-distance gap between within-cluster and between-cluster
    pairs. 0 means the clustering is meaningless; 0.3+ means the gap
    is clear."""
    if int(labels.max()) <= 1:
        return 0.0
    n = len(embeddings)
    intra_pairs: list[float] = []
    inter_pairs: list[float] = []
    for i in range(n):
        for j in range(i + 1, n):
            cos_sim = float(embeddings[i] @ embeddings[j])
            cos_dist = 1.0 - cos_sim
            if labels[i] == labels[j]:
                intra_pairs.append(cos_dist)
            else:
                inter_pairs.append(cos_dist)
    if not intra_pairs or not inter_pairs:
        return 0.0
    intra_mean = float(np.mean(intra_pairs))
    inter_mean = float(np.mean(inter_pairs))
    # Clamp into [0, 1] for readability — the actual scale is ~[0, 0.5].
    gap = max(0.0, inter_mean - intra_mean)
    return min(1.0, gap)
