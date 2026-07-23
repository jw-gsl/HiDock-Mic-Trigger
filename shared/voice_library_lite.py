"""Voice library management using speaker embeddings.

Stores enrolled speakers as JSON. Each speaker keeps MULTIPLE embedding samples
(exemplars) rather than a single running-average centroid — matching is done
best-of-samples (max cosine over a speaker's exemplars), which is far more robust
to the same person sounding different across meetings/mics than one average.

Legacy single-embedding entries are migrated to a one-element sample list on
load, so old libraries keep working.
"""
from __future__ import annotations

import json
import hashlib
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np

from shared.audio_utils import extract_embedding, load_audio, load_audio_segment

VOICE_LIBRARY_DIR = Path.home() / "HiDock" / "Voice Library"
EMBEDDINGS_FILE = VOICE_LIBRARY_DIR / "embeddings.json"
RAW_TRANSCRIPTS_DIR = Path.home() / "HiDock" / "Raw Transcripts"

_EMBEDDING_DIM = 40
_MODEL_VERSION = "mfcc-v1"

# Neural embedding constants
_NEURAL_EMBEDDING_DIM = 192
_NEURAL_MODEL_VERSION = "titanet-small"

# Multi-exemplar tuning
# Keep the active matching set bounded, but preserve every admitted sample as
# auditable evidence.  The active set is quality-and-diversity selected; it is
# not a destructive FIFO cap on the profile archive.
_MAX_SAMPLES = 60       # active exemplars per speaker, not archive size
_DEDUP_THRESHOLD = 0.98  # skip a duplicate from the same source/segment
_MIN_ACTIVE_QUALITY = 0.70
_QUALITY_VERSION = "quality-v2"
_AUDIO_QUALITY_VERSION = "acoustic-v1"
_QUALITY_INSPECTION_MAX_SECONDS = 30.0
# A high absolute cosine is not enough when two enrolled people are nearly
# equally close. Require a small lead over the runner-up before auto-tagging;
# the user can still choose a name manually in the transcript viewer.
_MIN_MATCH_MARGIN = 0.04

# Profile depth guidance. These are deliberately targets, not hard gates: a
# one-meeting profile can still identify someone, but it should be labelled as
# thin so the UI/workflow can keep collecting evidence.
_MIN_USABLE_SAMPLES = 5
_MIN_USABLE_MEETINGS = 3
_HEALTHY_SAMPLE_TARGET = 12
_HEALTHY_MEETING_TARGET = 5


class _WavLMSpeakerEmbedder:
    """Small adapter exposing the official Transformers X-vector model."""

    def __init__(self, model_path: str | Path):
        import torch
        from transformers import Wav2Vec2FeatureExtractor, WavLMForXVector

        self._torch = torch
        self._feature_extractor = Wav2Vec2FeatureExtractor.from_pretrained(
            str(model_path), local_files_only=True,
        )
        self._model = WavLMForXVector.from_pretrained(
            str(model_path), local_files_only=True,
        )
        self._model.eval()

    def extract_embedding(self, audio: np.ndarray, sr: int) -> np.ndarray:
        inputs = self._feature_extractor(
            np.asarray(audio, dtype=np.float32),
            sampling_rate=sr,
            return_tensors="pt",
        )
        with self._torch.inference_mode():
            embedding = self._model(**inputs).embeddings
            embedding = self._torch.nn.functional.normalize(embedding, dim=-1)
        return embedding[0].detach().cpu().numpy().astype(np.float32)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_speaker_embed_session(model_key: str | None = None, model_path: str | Path | None = None):
    """Try to load a configured speaker embedding ONNX model.

    Returns:
        onnxruntime.InferenceSession or None if not available.
    """
    try:
        from shared.models import MODELS_DIR, SPEAKER_EMBED_FILENAME, SPEAKER_EMBED_MODELS

        filename = SPEAKER_EMBED_FILENAME
        if model_path is not None:
            resolved_path = Path(model_path).expanduser().resolve()
        elif model_key is not None:
            model = SPEAKER_EMBED_MODELS.get(model_key)
            if model is None:
                return None
            filename = model["filename"]
            resolved_path = MODELS_DIR / filename
        else:
            resolved_path = MODELS_DIR / filename
        if not resolved_path.exists():
            return None

        if model_key == "wavlm_base_plus_sv":
            return _WavLMSpeakerEmbedder(resolved_path)

        import onnxruntime as ort

        options = None
        if model_key == "wespeaker_resnet293":
            # Match WeSpeaker's official ONNX inference setup and prevent
            # parallel archive workers from each consuming every CPU core.
            options = ort.SessionOptions()
            options.inter_op_num_threads = 1
            options.intra_op_num_threads = 1
        return ort.InferenceSession(
            str(resolved_path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
    except ImportError:
        return None
    except Exception:
        return None


def cosine_similarity(a: np.ndarray | list, b: np.ndarray | list) -> float:
    """Compute cosine similarity between two vectors.

    Returns:
        Cosine similarity in range [-1, 1].
    """
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a < 1e-10 or norm_b < 1e-10:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def _samples_of(entry: dict) -> list[dict]:
    """Return a speaker entry's exemplar list, migrating a legacy single
    `embedding` into a one-element `samples` list."""
    samples = entry.get("samples")
    if isinstance(samples, list) and samples:
        return samples
    if "embedding" in entry:
        return [{
            "embedding": entry["embedding"],
            "embedding_dim": entry.get("embedding_dim", len(entry["embedding"])),
            "model": entry.get("model", _MODEL_VERSION),
            "source": "legacy",
            "added_at": entry.get("last_updated") or entry.get("enrolled_at", ""),
        }]
    return []


def _provenance_key(record: dict) -> tuple | None:
    """Return the identity of the recording/segment that produced a sample.

    A diarized confirmation is keyed by sidecar, so confirming the same
    meeting again replaces the old exemplar even if diarization assigned a
    different cluster ID. Manual audio enrollment
    is keyed by audio file + segment range, allowing several useful samples
    from one recording. Samples without provenance retain the old global
    near-duplicate behaviour.
    """
    source_file = record.get("source_file")
    if source_file:
        return ("source", str(source_file))
    audio_file = record.get("audio_file")
    if audio_file:
        return (
            "audio",
            str(audio_file),
            str(record.get("segment_start", "")),
            str(record.get("segment_end", "")),
        )
    return None


def _sample_id(sample: dict) -> str:
    """Return a stable, non-sensitive id for a sample.

    Provenanced samples use their recording identity, so replacing a
    re-diarised confirmation does not change the id. Legacy samples fall back
    to the embedding itself and remain addressable for cleanup.
    """
    identity = _provenance_key(sample)
    if identity is None:
        identity = ("embedding", sample.get("embedding", []))
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha1(encoded.encode("utf-8")).hexdigest()[:16]


def _meeting_key(sample: dict) -> str | None:
    """Return a stable recording key for profile coverage counts."""
    source_file = sample.get("source_file")
    if source_file:
        return f"source:{source_file}"
    audio_file = sample.get("audio_file")
    if audio_file:
        return f"audio:{audio_file}"
    return None


def _deduplicate_diarized_samples(samples: list[dict]) -> list[dict]:
    """Keep at most one diarized exemplar per meeting source.

    Older backfills could add one sample for every diarizer cluster carrying
    the same name. That inflates sample depth without adding meeting diversity
    (and can make a single malformed sidecar look like fifteen meetings).
    Prefer the longest attributable turn when compacting those old entries.
    """
    result: list[dict] = []
    positions: dict[str, int] = {}
    for sample in samples:
        key = _meeting_key(sample)
        if key is None:
            result.append(sample)
            continue
        index = positions.get(key)
        if index is None:
            positions[key] = len(result)
            result.append(sample)
            continue
        current = result[index]
        current_duration = float(current.get("segment_end", 0.0) or 0.0) - float(current.get("segment_start", 0.0) or 0.0)
        candidate_duration = float(sample.get("segment_end", 0.0) or 0.0) - float(sample.get("segment_start", 0.0) or 0.0)
        if candidate_duration > current_duration:
            result[index] = sample
    for sample in result:
        sample["id"] = _sample_id(sample)
    return result


def _profile_status(sample_count: int, meeting_count: int) -> str:
    """Classify profile depth without pretending quantity equals correctness."""
    if (sample_count >= _HEALTHY_SAMPLE_TARGET
            and meeting_count >= _HEALTHY_MEETING_TARGET):
        return "healthy"
    if (sample_count >= _MIN_USABLE_SAMPLES
            and meeting_count >= _MIN_USABLE_MEETINGS):
        return "usable"
    return "thin"


def _speaker_segment(data: dict, speaker_id) -> tuple[float, float] | None:
    """Find the longest valid segment for a speaker in a diarized sidecar."""
    candidates = []
    for segment in data.get("segments", []):
        try:
            if int(segment.get("speaker_id")) != int(speaker_id):
                continue
            start = float(segment.get("start", 0.0))
            end = float(segment.get("end", 0.0))
        except (TypeError, ValueError):
            continue
        if end > start:
            candidates.append((end - start, start, end))
    if not candidates:
        return None
    _, start, end = max(candidates)
    return start, end


def _diarized_provenance(
    sidecar_path: Path,
    data: dict,
    speaker_id,
) -> dict:
    """Build auditable provenance for a confirmed/backfilled exemplar."""
    provenance = {
        "source_file": str(sidecar_path.resolve()),
        "speaker_id": str(speaker_id),
    }
    audio_ref = data.get("audio_file")
    if audio_ref:
        audio_path = Path(str(audio_ref))
        if not audio_path.is_absolute():
            audio_path = sidecar_path.resolve().parent / audio_path
        provenance["audio_file"] = str(audio_path.resolve())
    turns = []
    for item in data.get("segments", []):
        try:
            if int(item.get("speaker_id")) == int(speaker_id):
                start = float(item.get("start", 0.0))
                end = float(item.get("end", 0.0))
                if end > start:
                    turns.append((start, end))
        except (TypeError, ValueError):
            continue
    provenance["turn_count"] = len(turns)
    provenance["total_talk_seconds"] = round(sum(end - start for start, end in turns), 3)
    segment = _speaker_segment(data, speaker_id)
    if segment is not None:
        provenance["segment_start"], provenance["segment_end"] = segment
    return provenance


def load_backfill_aliases(path: str | Path) -> dict[str, str]:
    """Load explicit observed-label → canonical-name mappings for a backfill."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    aliases = data.get("aliases", data) if isinstance(data, dict) else None
    if not isinstance(aliases, dict):
        raise ValueError("alias file must be a JSON object or contain an 'aliases' object")
    result: dict[str, str] = {}
    for observed, canonical in aliases.items():
        key = " ".join(str(observed).split()).casefold()
        value = " ".join(str(canonical).split())
        if not key or not value:
            raise ValueError("alias file cannot contain empty labels")
        existing = result.get(key)
        if existing is not None and existing != value:
            raise ValueError(f"conflicting canonical names for alias '{observed}'")
        result[key] = value
    return result


def _canonical_backfill_name(name: str, aliases: dict[str, str]) -> str:
    cleaned = " ".join(str(name or "").split())
    return aliases.get(cleaned.casefold(), cleaned)


def _backfill_candidate_record(
    sidecar_path: Path,
    data: dict,
    speaker_id: str,
    speaker_name: str,
    canonical_name: str,
    label_source: str | None,
    *,
    include_merged: bool,
) -> dict:
    """Describe one potential historical exemplar without changing data."""
    meta = (data.get("speaker_meta") or {}).get(speaker_id) or {}
    segment = _speaker_segment(data, speaker_id)
    turns = [
        item for item in data.get("segments", [])
        if str(item.get("speaker_id", "")) == str(speaker_id)
    ]
    embedding = (data.get("speaker_embeddings") or {}).get(speaker_id)
    audio_ref = data.get("audio_file")
    audio_path = None
    if audio_ref:
        audio_path = Path(str(audio_ref))
        if not audio_path.is_absolute():
            audio_path = sidecar_path.resolve().parent / audio_path
    start, end = segment if segment else (None, None)
    return {
        "source_file": str(sidecar_path.resolve()),
        "derived_merged": sidecar_path.stem.startswith("Merged-"),
        "included_merged": include_merged,
        "speaker_id": str(speaker_id),
        "observed_name": speaker_name,
        "canonical_name": canonical_name,
        "alias_applied": canonical_name != speaker_name,
        "label_source": label_source,
        "label_verified": meta.get("verified") is True,
        "eligible": label_source is not None,
        "reason": "eligible" if label_source else "untrusted_or_placeholder_label",
        "turn_count": len(turns),
        "longest_turn_start": start,
        "longest_turn_end": end,
        "longest_turn_seconds": round(end - start, 3) if segment else 0.0,
        "stored_embedding": embedding is not None,
        "embedding_dim": len(embedding) if isinstance(embedding, list) else None,
        "audio_file": str(audio_path.resolve()) if audio_path else None,
        "audio_exists": audio_path.exists() if audio_path else False,
    }


def load_library() -> dict:
    """Load the voice library from disk, migrating legacy entries to the
    multi-sample schema so callers can rely on `samples`.

    Returns:
        Library dict with "speakers" key mapping names to speaker data.
    """
    if not EMBEDDINGS_FILE.exists():
        return {"speakers": {}}
    try:
        data = json.loads(EMBEDDINGS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"speakers": {}}
    if "speakers" not in data:
        data["speakers"] = {}
    for entry in data["speakers"].values():
        samples = _deduplicate_diarized_samples(_samples_of(entry))
        entry["samples"] = samples
        entry.pop("embedding", None)   # single source of truth = samples
        if samples:
            entry.setdefault("embedding_dim", samples[0].get("embedding_dim"))
            entry.setdefault("model", samples[0].get("model"))
    return data


def save_library(lib: dict) -> None:
    """Save the voice library to disk atomically (temp file + rename)."""
    VOICE_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    content = json.dumps(lib, indent=2, ensure_ascii=False) + "\n"
    fd, tmp_path = tempfile.mkstemp(
        dir=VOICE_LIBRARY_DIR, prefix=EMBEDDINGS_FILE.name, suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, EMBEDDINGS_FILE)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _canonical_speaker_name(lib: dict, name: str) -> str:
    """Strip/collapse whitespace and, if a case-insensitive match already exists
    in the library, reuse that key so we don't create 'Leslie Mcneely' next to
    'Leslie McNeely'. Typos (Wildmsith vs Wildsmith) still need an explicit merge."""
    cleaned = " ".join((name or "").strip().split())
    if not cleaned:
        return cleaned
    folded = cleaned.casefold()
    for existing in lib.get("speakers", {}):
        if existing.casefold() == folded:
            return existing
    return cleaned


def _audio_quality_metrics(audio: np.ndarray, sr: int = 16000) -> dict:
    """Return conservative, explainable audio-cleanliness indicators.

    This is an admission signal, not a perceptual-quality claim. It uses short
    RMS frames to flag silence, poor speech density, high noise floors, and
    clipping without requiring a second ML model or network access.
    """
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if len(audio) < max(1, int(0.5 * sr)):
        return {
            "acoustic_quality": 0.0,
            "audio_metrics_version": _AUDIO_QUALITY_VERSION,
            "audio_reason": "less than 0.5 seconds of decodable audio",
        }
    frame = max(1, int(0.025 * sr))
    hop = max(1, int(0.010 * sr))
    rms = np.array([
        float(np.sqrt(np.mean(audio[start:start + frame] ** 2)))
        for start in range(0, max(1, len(audio) - frame + 1), hop)
    ])
    noise_floor = float(np.percentile(rms, 20))
    speech_threshold = max(0.004, noise_floor * 2.5)
    speech_ratio = float(np.mean(rms >= speech_threshold))
    signal = max(float(np.percentile(rms, 80)), 1e-7)
    snr_db = float(20 * np.log10(signal / max(noise_floor, 1e-7)))
    clipping_ratio = float(np.mean(np.abs(audio) >= 0.98))

    snr_score = float(np.clip((snr_db - 3.0) / 17.0, 0.0, 1.0))
    density_score = float(np.clip((speech_ratio - 0.20) / 0.55, 0.0, 1.0))
    if clipping_ratio >= 0.02:
        clipping_score = 0.0
    elif clipping_ratio >= 0.005:
        clipping_score = 0.5
    else:
        clipping_score = 1.0
    quality = round(0.45 * snr_score + 0.40 * density_score + 0.15 * clipping_score, 3)
    reasons = []
    if speech_ratio < 0.35:
        reasons.append("low speech density")
    if snr_db < 8.0:
        reasons.append("low signal-to-noise estimate")
    if clipping_ratio >= 0.005:
        reasons.append("audible clipping risk")
    if not reasons:
        reasons.append("adequate acoustic signal")
    return {
        "acoustic_quality": quality,
        "audio_snr_db": round(snr_db, 2),
        "audio_speech_ratio": round(speech_ratio, 3),
        "audio_clipping_ratio": round(clipping_ratio, 5),
        "audio_metrics_version": _AUDIO_QUALITY_VERSION,
        "audio_reason": "; ".join(reasons),
    }


def _audio_quality_from_path(
    audio_path: str | Path,
    segment_start: float | None = None,
    segment_end: float | None = None,
) -> dict:
    """Load a bounded clip and return acoustic metrics for an enrollment."""
    start = max(0.0, float(segment_start or 0.0))
    end = segment_end
    if end is None:
        end = start + _QUALITY_INSPECTION_MAX_SECONDS
    else:
        end = min(float(end), start + _QUALITY_INSPECTION_MAX_SECONDS)
    audio = load_audio_segment(
        audio_path, start_seconds=start, end_seconds=end, sr=16000,
    )
    return _audio_quality_metrics(audio)


def _assess_sample_quality(source: str, provenance: dict) -> dict:
    """Score structural evidence and, where available, acoustic cleanliness.

    This is a conservative admission signal, not a perceptual-quality (MOS)
    prediction. Stored diarizer centroids may have no decodable source audio,
    so their score remains structural until an explicit audio inspection.
    """
    start, end = provenance.get("segment_start"), provenance.get("segment_end")
    try:
        segment_seconds = max(0.0, float(end) - float(start))
    except (TypeError, ValueError):
        segment_seconds = None
    try:
        talk_seconds = max(0.0, float(provenance.get("total_talk_seconds")))
    except (TypeError, ValueError):
        talk_seconds = None

    if segment_seconds is None:
        duration_score, duration_reason = 0.75, "timing unavailable"
    elif segment_seconds < 1.5:
        duration_score, duration_reason = 0.15, "less than 1.5 seconds"
    elif segment_seconds < 3.0:
        duration_score, duration_reason = 0.50, "short segment"
    elif segment_seconds <= 30.0:
        duration_score, duration_reason = 1.0, "useful segment duration"
    elif segment_seconds <= 90.0:
        duration_score, duration_reason = 0.85, "long segment"
    else:
        duration_score, duration_reason = 0.65, "very long segment may be mixed"

    if talk_seconds is None:
        talk_score, talk_reason = 0.75, "total talk time unavailable"
    elif talk_seconds < 5.0:
        talk_score, talk_reason = 0.25, "less than 5 seconds attributable speech"
    elif talk_seconds < 10.0:
        talk_score, talk_reason = 0.60, "limited attributable speech"
    elif talk_seconds < 30.0:
        talk_score, talk_reason = 0.85, "adequate attributable speech"
    else:
        talk_score, talk_reason = 1.0, "strong attributable speech coverage"

    label_source = provenance.get("label_source")
    if source == "human_archive_verified":
        trust_score = 1.0
    elif source == "backfill" and label_source == "user":
        trust_score = 1.0
    elif source == "backfill" and label_source in {"legacy", "legacy_import"}:
        trust_score = 0.85
    elif source in {"confirm", "audio"}:
        trust_score = 0.90
    else:
        trust_score = 0.75

    structural_score = round(0.40 * duration_score + 0.35 * talk_score + 0.25 * trust_score, 3)
    acoustic_score = provenance.get("acoustic_quality")
    try:
        acoustic_score = float(acoustic_score)
    except (TypeError, ValueError):
        acoustic_score = None
    if acoustic_score is None:
        score = structural_score
        acoustic_reason = "acoustic quality not inspected"
    else:
        score = round(0.65 * structural_score + 0.35 * acoustic_score, 3)
        acoustic_reason = str(provenance.get("audio_reason") or "acoustic quality inspected")
    reasons = [duration_reason, talk_reason, acoustic_reason]
    state = "active_candidate" if score >= _MIN_ACTIVE_QUALITY else "archive"
    return {
        "quality_score": score,
        "structural_quality": structural_score,
        "acoustic_quality": acoustic_score,
        "quality_state": state,
        "quality_version": _QUALITY_VERSION,
        "quality_reasons": reasons,
    }


def _refresh_active_samples(entry: dict, max_samples: int = _MAX_SAMPLES) -> None:
    """Choose a bounded, diverse active set without deleting archived evidence."""
    samples = entry.setdefault("samples", [])
    eligible = [
        sample for sample in samples
        if sample.get("quality_state", "active_candidate") != "archive"
        and float(sample.get("quality_score", 0.75) or 0.0) >= _MIN_ACTIVE_QUALITY
    ]
    selected: list[dict] = []
    remaining = list(eligible)
    limit = max(1, max_samples)
    while remaining and len(selected) < limit:
        def rank(sample: dict) -> tuple[float, str]:
            quality = float(sample.get("quality_score", 0.75) or 0.0)
            comparable = [
                other for other in selected
                if other.get("embedding_dim", len(other.get("embedding", [])))
                == sample.get("embedding_dim", len(sample.get("embedding", [])))
            ]
            novelty = 1.0 if not comparable else 1.0 - max(
                cosine_similarity(sample["embedding"], other["embedding"])
                for other in comparable
            )
            return (0.75 * quality + 0.25 * max(0.0, novelty), str(sample.get("added_at", "")))
        chosen = max(remaining, key=rank)
        selected.append(chosen)
        remaining.remove(chosen)
    selected_ids = {id(sample) for sample in selected}
    for sample in samples:
        sample["active"] = id(sample) in selected_ids


def _enroll_into(
    lib: dict,
    name: str,
    embedding: np.ndarray | list,
    embed_dim: int,
    model: str,
    source: str,
    provenance: dict | None = None,
    max_samples: int = _MAX_SAMPLES,
) -> dict:
    """Archive an exemplar and refresh the bounded active matching set."""
    emb = np.asarray(embedding, dtype=np.float32)
    norm = float(np.linalg.norm(emb))
    if norm > 1e-10:
        emb = emb / norm
    now = _now()

    name = _canonical_speaker_name(lib, name)
    if not name:
        raise ValueError("speaker name is empty")

    entry = lib["speakers"].get(name)
    if entry is None:
        entry = {"enrolled_at": now, "last_updated": now,
                 "embedding_dim": embed_dim, "model": model, "samples": []}
        lib["speakers"][name] = entry
    samples = entry.setdefault("samples", [])
    provenance = dict(provenance or {})
    quality = _assess_sample_quality(source, provenance)
    source_key = _provenance_key(provenance)

    # A repeated confirmation from the same meeting should refresh that
    # meeting's exemplar, not create a second copy. This is also what lets a
    # later, improved diarisation replace an earlier centroid cleanly.
    if source_key is not None:
        for s in samples:
            if _provenance_key(s) != source_key:
                continue
            s.update({
                "embedding": emb.tolist(),
                "embedding_dim": embed_dim,
                "model": model,
                "source": source,
                **quality,
                **provenance,
            })
            s.setdefault("added_at", now)
            s["updated_at"] = now
            s["id"] = _sample_id(s)
            _refresh_active_samples(entry, max_samples)
            entry["last_updated"] = now
            return entry

    # Skip a near-identical exemplar only when it came from the same source.
    # Similar centroids from different meetings are valuable evidence and must
    # be retained for meeting coverage and environmental variation.
    for s in samples:
        same_source = source_key is None and _provenance_key(s) is None
        if source_key is not None and _provenance_key(s) == source_key:
            same_source = True
        if s.get("embedding_dim", len(s["embedding"])) == embed_dim \
                and same_source \
                and cosine_similarity(emb, s["embedding"]) > _DEDUP_THRESHOLD:
            s["updated_at"] = now
            s["id"] = _sample_id(s)
            _refresh_active_samples(entry, max_samples)
            entry["last_updated"] = now
            return entry

    sample = {
        "embedding": emb.tolist(),
        "embedding_dim": embed_dim,
        "model": model,
        "source": source,
        "added_at": now,
        **quality,
        **provenance,
    }
    sample["id"] = _sample_id(sample)
    samples.append(sample)
    _refresh_active_samples(entry, max_samples)
    entry["last_updated"] = now
    entry["embedding_dim"] = embed_dim
    entry["model"] = model
    return entry


def _trim_profile_samples(entry: dict, max_samples: int) -> None:
    """Compatibility wrapper: refresh the active set; never delete evidence."""
    _refresh_active_samples(entry, max_samples)


def enroll_embedding(name: str, embedding: np.ndarray | list, *,
                     embed_dim: int | None = None, model: str | None = None,
                     source: str = "confirm",
                     provenance: dict | None = None) -> dict:
    """Enroll a precomputed embedding as an exemplar of `name`."""
    embed_dim = embed_dim if embed_dim is not None else len(embedding)
    model = model or _NEURAL_MODEL_VERSION
    lib = load_library()
    entry = _enroll_into(
        lib, name, embedding, embed_dim, model, source, provenance=provenance
    )
    save_library(lib)
    return entry


def enroll_speaker(
    name: str,
    audio_path: str | Path,
    segment_start: float | None = None,
    segment_end: float | None = None,
    provenance: dict | None = None,
) -> dict:
    """Enroll a speaker from an audio segment (computes the embedding, then adds
    it as an exemplar). Neural TitaNet when available, else MFCC."""
    embedding, embed_dim, model_version = _extract_audio_embedding(
        audio_path,
        segment_start=segment_start,
        segment_end=segment_end,
    )
    sample_provenance = dict(provenance or {})
    sample_provenance.setdefault("audio_file", str(Path(audio_path).resolve()))
    if segment_start is not None:
        sample_provenance.setdefault("segment_start", float(segment_start))
    if segment_end is not None:
        sample_provenance.setdefault("segment_end", float(segment_end))
    try:
        sample_provenance.update(_audio_quality_from_path(
            audio_path, segment_start=segment_start, segment_end=segment_end,
        ))
    except Exception as exc:  # noqa: BLE001 - quality metadata must not block a valid enrollment
        sample_provenance.update({
            "audio_metrics_version": _AUDIO_QUALITY_VERSION,
            "audio_reason": f"inspection failed: {exc}",
        })
    return enroll_embedding(
        name,
        embedding,
        embed_dim=embed_dim,
        model=model_version,
        source="audio",
        provenance=sample_provenance,
    )


def _extract_audio_embedding(
    audio_path: str | Path,
    *,
    segment_start: float | None = None,
    segment_end: float | None = None,
    session=None,
    neural_model_version: str | None = None,
) -> tuple[np.ndarray, int, str]:
    """Extract one voice embedding, optionally from a bounded audio segment."""
    if segment_start is not None or segment_end is not None:
        # Long recordings can occupy hundreds of MB when decoded in full.
        # Enrolment and archive recovery already supply bounded evidence, so
        # decode only that window rather than slicing after a whole-file load.
        audio = load_audio_segment(
            audio_path,
            start_seconds=max(0.0, float(segment_start or 0.0)),
            end_seconds=segment_end,
            sr=16000,
        )
    else:
        audio = load_audio(audio_path, sr=16000)

    session = session if session is not None else _get_speaker_embed_session()
    if session is not None:
        if hasattr(session, "extract_embedding"):
            embedding = session.extract_embedding(audio, 16000)
        else:
            embedding = extract_embedding(audio, sr=16000, onnx_session=session)
        embed_dim = len(embedding)
        model_version = neural_model_version or _NEURAL_MODEL_VERSION
    else:
        embedding = extract_embedding(audio, sr=16000, n_mfcc=_EMBEDDING_DIM)
        embed_dim = _EMBEDDING_DIM
        model_version = _MODEL_VERSION
    return embedding, embed_dim, model_version


def enroll_from_diarized(name: str, diarized_path: str | Path, speaker_id) -> dict:
    """Enroll the stored per-speaker centroid (`speaker_embeddings[id]`) from a
    diarized sidecar. This is the robust, multi-segment voiceprint the diarizer
    already computed — no audio re-decode (works even for Opus/Plaud).

    Legacy sidecars may not contain stored speaker embeddings. In that case,
    fall back to enrolling the longest available audio segment for the speaker
    so editing or confirming a name still teaches the Voice Library."""
    sidecar_path = Path(diarized_path)
    data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    embs = data.get("speaker_embeddings") or {}
    emb = embs.get(str(speaker_id))
    if emb is not None:
        return enroll_embedding(
            name,
            emb,
            embed_dim=len(emb),
            model=_NEURAL_MODEL_VERSION,
            source="confirm",
            provenance=_diarized_provenance(sidecar_path, data, speaker_id),
        )

    audio_ref = data.get("audio_file")
    if not audio_ref:
        raise ValueError(
            f"no stored embedding or audio file for speaker {speaker_id} in {diarized_path}"
        )
    audio_path = Path(str(audio_ref))
    if not audio_path.is_absolute():
        audio_path = sidecar_path.resolve().parent / audio_path
    if not audio_path.exists():
        raise ValueError(f"audio file not found for speaker {speaker_id}: {audio_path}")

    segment = _speaker_segment(data, speaker_id)
    if segment is None:
        raise ValueError(
            f"no audio segments found for speaker {speaker_id} in {diarized_path}"
        )

    start, end = segment
    return enroll_speaker(
        name,
        audio_path,
        segment_start=start,
        segment_end=end,
        provenance=_diarized_provenance(sidecar_path, data, speaker_id),
    )


def _bounded_representative_segment(
    data: dict,
    speaker_id,
    max_seconds: float = 30.0,
) -> tuple[float, float] | None:
    """Choose a useful, bounded clip for historical audio backfill."""
    segment = _speaker_segment(data, speaker_id)
    if segment is None:
        return None
    start, end = segment
    return start, min(end, start + max_seconds)


def _historical_meeting_sets(
    names: Iterable[str],
    directory: str | Path = RAW_TRANSCRIPTS_DIR,
) -> dict[str, set[str]]:
    """Find trustworthy historical meetings for each requested library name.

    Derived ``Merged-*`` transcripts are excluded: their child meetings are
    the real evidence and counting both would inflate coverage.
    """
    from shared.speaker_meta import backfill_label_source

    d = Path(directory)
    wanted = set(names)
    meetings = {name: set() for name in wanted}
    for f in sorted(d.glob("*_diarized.json")):
        if f.stem.startswith("Merged-"):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        names = data.get("speaker_names", {}) or {}
        meta = data.get("speaker_meta", {}) or {}
        for sid, speaker_name in names.items():
            if speaker_name not in wanted:
                continue
            m = meta.get(sid, {}) or {}
            if backfill_label_source(speaker_name, m) is None:
                continue
            meetings[speaker_name].add(str(f.resolve()))
    return meetings


def list_historical_meetings(
    name: str,
    directory: str | Path = RAW_TRANSCRIPTS_DIR,
) -> list[str]:
    """Return trustworthy historical sidecars containing ``name``."""
    return sorted(_historical_meeting_sets([name], directory).get(name, set()))


def _select_backfill_candidates(
    candidates: list[tuple[Path, str, dict]],
    existing_sources: set[str],
    limit: int,
) -> list[tuple[Path, str, dict]]:
    """Keep existing provenance and spread new candidates across history."""
    if len(candidates) <= limit:
        return candidates
    preferred = [candidate for candidate in candidates if str(candidate[0].resolve()) in existing_sources]
    remaining = [candidate for candidate in candidates if candidate not in preferred]
    selected = preferred[:limit]
    slots = max(0, limit - len(selected))
    if slots == 0:
        return selected
    if len(remaining) <= slots:
        return selected + remaining
    if slots == 1:
        return selected + [remaining[-1]]
    indexes = {
        round(index * (len(remaining) - 1) / (slots - 1))
        for index in range(slots)
    }
    return selected + [remaining[index] for index in sorted(indexes)]


def _deduplicate_backfill_candidates(
    candidates: list[tuple[Path, str, dict]],
) -> list[tuple[Path, str, dict]]:
    """Select one representative cluster per named person per sidecar."""
    grouped: dict[str, list[tuple[Path, str, dict]]] = {}
    for candidate in candidates:
        grouped.setdefault(str(candidate[0].resolve()), []).append(candidate)

    selected = []
    for group in grouped.values():
        selected.append(max(
            group,
            key=lambda item: (_speaker_segment(item[2], item[1]) or (0.0, 0.0))[1]
                              - (_speaker_segment(item[2], item[1]) or (0.0, 0.0))[0],
        ))
    return sorted(selected, key=lambda item: str(item[0]))


def enroll_from_transcripts(
    directory: str | Path,
    *,
    names: Iterable[str] | None = None,
    audio_fallback: bool = False,
    include_legacy: bool = False,
    include_merged: bool = False,
    max_samples: int = _MAX_SAMPLES,
    dry_run: bool = False,
    report_path: str | Path | None = None,
    aliases: dict[str, str] | None = None,
    stored_embeddings_only: bool = False,
) -> dict:
    """Backfill trustworthy historical meeting exemplars.

    Stored diarizer embeddings are used when present. With ``audio_fallback``
    enabled, legacy sidecars without a stored embedding use a bounded 30-second
    clip from the longest speaker turn. Unverified auto-matches are always
    excluded. When names are supplied, only those profiles are backfilled;
    otherwise all named speakers found in the directory are eligible.
    """
    from shared.speaker_meta import backfill_label_source
    import sys as _sys

    d = Path(directory)
    lib = load_library()
    alias_map = {
        " ".join(str(observed).split()).casefold(): " ".join(str(canonical).split())
        for observed, canonical in (aliases or {}).items()
        if str(observed).strip() and str(canonical).strip()
    }
    requested = {
        _canonical_backfill_name(str(name), alias_map)
        for name in names or [] if str(name).strip()
    }
    # Inventory derived artifacts as well, but never use them as evidence
    # unless the caller explicitly asks. This lets Stage 0 prove that merged
    # meetings were excluded rather than hiding them from the report.
    all_files = sorted(d.glob("*_diarized.json"))
    total = len(all_files)
    candidates_by_name: dict[str, list[tuple[Path, str, dict, str]]] = {}
    candidate_records: list[dict] = []
    files = 0
    eligible = 0
    print(f"PROGRESS:0/{total}", file=_sys.stderr, flush=True)

    for idx, f in enumerate(all_files, start=1):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            print(f"PROGRESS:{idx}/{total}", file=_sys.stderr, flush=True)
            continue
        files += 1
        speaker_names = data.get("speaker_names", {}) or {}
        meta = data.get("speaker_meta", {}) or {}
        for sid, speaker_name in speaker_names.items():
            canonical_name = _canonical_backfill_name(speaker_name, alias_map)
            label_source = backfill_label_source(
                speaker_name, meta.get(sid), include_legacy=include_legacy,
            )
            candidate_records.append(_backfill_candidate_record(
                f, data, str(sid), speaker_name, canonical_name, label_source,
                include_merged=include_merged,
            ))
            if f.stem.startswith("Merged-") and not include_merged:
                candidate_records[-1].update({
                    "eligible": False,
                    "reason": "derived_merged_excluded",
                })
                continue
            if requested and canonical_name not in requested:
                continue
            if label_source is None:
                continue
            if stored_embeddings_only and (data.get("speaker_embeddings") or {}).get(str(sid)) is None:
                continue
            candidates_by_name.setdefault(canonical_name, []).append(
                (f, str(sid), data, label_source, speaker_name)
            )
            eligible += 1
        print(f"PROGRESS:{idx}/{total}", file=_sys.stderr, flush=True)

    enrolled = 0
    stored_enrolled = 0
    audio_enrolled = 0
    skipped = 0
    per_name: dict[str, int] = {}
    per_name_meetings: dict[str, int] = {}

    for speaker_name, candidates in candidates_by_name.items():
        speaker_name = _canonical_speaker_name(lib, speaker_name)
        candidates = _deduplicate_backfill_candidates(candidates)
        entry = lib["speakers"].get(speaker_name, {})
        existing_sources = {
            str(sample.get("source_file"))
            for sample in _samples_of(entry)
            if sample.get("source_file")
        }
        chosen = _select_backfill_candidates(candidates, existing_sources, max(1, max_samples))
        per_name_meetings[speaker_name] = len(candidates)
        if dry_run:
            for sidecar_path, speaker_id, data, label_source, observed_name in chosen:
                embedding = (data.get("speaker_embeddings") or {}).get(speaker_id)
                if embedding is not None:
                    stored_enrolled += 1
                    enrolled += 1
                elif audio_fallback:
                    audio_enrolled += 1
                    enrolled += 1
                else:
                    skipped += 1
            per_name[speaker_name] = enrolled - sum(per_name.values())
            continue
        session = _get_speaker_embed_session() if audio_fallback else None
        _trim_profile_samples(entry, max(1, max_samples))
        for sidecar_path, speaker_id, data, label_source, observed_name in chosen:
            embs = data.get("speaker_embeddings") or {}
            embedding = embs.get(speaker_id)
            source = "backfill"
            provenance = _diarized_provenance(sidecar_path, data, speaker_id)
            provenance["label_source"] = label_source
            provenance["observed_name"] = observed_name
            if embedding is not None:
                _enroll_into(
                    lib, speaker_name, embedding, len(embedding),
                    _NEURAL_MODEL_VERSION, source, provenance=provenance,
                    max_samples=max(1, max_samples),
                )
                stored_enrolled += 1
                enrolled += 1
                continue

            if not audio_fallback:
                skipped += 1
                continue
            audio_ref = data.get("audio_file")
            if not audio_ref:
                skipped += 1
                continue
            audio_path = Path(str(audio_ref))
            if not audio_path.is_absolute():
                audio_path = sidecar_path.resolve().parent / audio_path
            segment = _bounded_representative_segment(data, speaker_id)
            if not audio_path.exists() or segment is None:
                skipped += 1
                continue
            start, end = segment
            try:
                embedding, embed_dim, model = _extract_audio_embedding(
                    audio_path,
                    segment_start=start,
                    segment_end=end,
                    session=session,
                )
                provenance["segment_start"] = start
                provenance["segment_end"] = end
                try:
                    provenance.update(_audio_quality_from_path(
                        audio_path, segment_start=start, segment_end=end,
                    ))
                except Exception as exc:  # noqa: BLE001 - preserve a valid legacy embedding
                    provenance.update({
                        "audio_metrics_version": _AUDIO_QUALITY_VERSION,
                        "audio_reason": f"inspection failed: {exc}",
                    })
                _enroll_into(
                    lib, speaker_name, embedding, embed_dim, model,
                    source, provenance=provenance, max_samples=max(1, max_samples),
                )
                audio_enrolled += 1
                enrolled += 1
            except Exception as exc:  # noqa: BLE001 - one bad legacy clip must not abort the batch
                print(f"WARN: could not backfill {speaker_name} from {sidecar_path.name}: {exc}", file=_sys.stderr)
                skipped += 1
        per_name[speaker_name] = enrolled - sum(per_name.values())

    result = {
        "dry_run": dry_run,
        "files": files,
        "eligible": eligible,
        "enrolled": enrolled,
        "stored_embedding_enrolled": stored_enrolled,
        "audio_enrolled": audio_enrolled,
        "skipped": skipped,
        "speakers": per_name,
        "historical_meetings": per_name_meetings,
    }
    # The detailed candidate list is intentionally opt-in for regular writes:
    # it can contain thousands of records and is primarily the Stage 0 report.
    if dry_run or report_path:
        result["candidates"] = candidate_records
    if report_path:
        report = Path(report_path)
        report.parent.mkdir(parents=True, exist_ok=True)
        result["report_path"] = str(report.resolve())
        report.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if not dry_run:
        save_library(lib)
    return result


def _best_similarity(embedding: np.ndarray, entry: dict, dim: int) -> float:
    """Max cosine over a speaker's active same-dimension exemplars."""
    best = -1.0
    for s in _samples_of(entry):
        if s.get("active") is False:
            continue
        if s.get("embedding_dim", len(s["embedding"])) != dim:
            continue
        sc = cosine_similarity(embedding, s["embedding"])
        if sc > best:
            best = sc
    return best


def library_scores(
    embedding: np.ndarray | list,
    allowed_names: Iterable[str] | None = None,
) -> list[tuple[str, float]]:
    """Best-of-samples similarity of `embedding` against every enrolled speaker
    (same embedding dim only).

    ``allowed_names`` is an optional candidate set supplied by contextual
    matching (for example a calendar attendee list). An empty set is
    intentional: it means context was available but did not identify any
    enrolled candidate, so no voice-library label should be emitted.
    Returns [(name, score)] — used by both identify_speaker and
    speaker_meta.score_speakers."""
    lib = load_library()
    embedding = np.asarray(embedding)
    dim = len(embedding)
    allowed = None if allowed_names is None else set(allowed_names)
    out: list[tuple[str, float]] = []
    for name, entry in lib["speakers"].items():
        if allowed is not None and name not in allowed:
            continue
        s = _best_similarity(embedding, entry, dim)
        if s > -1.0:
            out.append((name, round(float(s), 4)))
    return out


def identify_speaker(
    embedding: np.ndarray | list,
    threshold: float = 0.7,
    allowed_names: Iterable[str] | None = None,
    min_margin: float = _MIN_MATCH_MARGIN,
) -> tuple[str | None, float]:
    """Identify a speaker by best-of-exemplars similarity.

    Returns:
        (name, confidence) if the best match clears `threshold` and leads the
        runner-up by `min_margin`, else (None, 0.0). The margin guard prevents
        an ambiguous voice from becoming a confident-looking auto-tag.
    """
    scores = library_scores(embedding, allowed_names=allowed_names)
    if not scores:
        return (None, 0.0)
    scores.sort(key=lambda x: x[1], reverse=True)
    best_name, best_score = scores[0]
    runner_up_score = scores[1][1] if len(scores) > 1 else -1.0
    if best_score >= threshold and best_score - runner_up_score >= min_margin:
        return (best_name, best_score)
    return (None, 0.0)


def identify_speakers(
    embeddings: np.ndarray | list,
    threshold: float = 0.7,
    allowed_names: Iterable[str] | None = None,
    min_margin: float = _MIN_MATCH_MARGIN,
) -> dict[int, tuple[str | None, float]]:
    """Identify speakers for multiple embeddings."""
    return {
        i: identify_speaker(
            emb,
            threshold=threshold,
            allowed_names=allowed_names,
            min_margin=min_margin,
        )
        for i, emb in enumerate(embeddings)
    }


def set_calendar_emails(name: str, emails: Iterable[str]) -> bool:
    """Associate Microsoft 365 attendee email addresses with a voice entry."""
    lib = load_library()
    if name not in lib["speakers"]:
        return False
    cleaned = sorted({str(email).strip().casefold() for email in emails if str(email).strip()})
    if cleaned:
        lib["speakers"][name]["calendar_emails"] = cleaned
    else:
        lib["speakers"][name].pop("calendar_emails", None)
    lib["speakers"][name]["last_updated"] = _now()
    save_library(lib)
    return True


def list_speakers(
    transcripts_dir: str | Path = RAW_TRANSCRIPTS_DIR,
) -> list[dict]:
    """List enrolled speakers with sample depth and historical coverage.

    Meeting coverage comes from trustworthy diarized meeting labels when the
    transcript archive is available, not only from the bounded exemplar list.
    This prevents a profile from showing four meetings merely because older
    exemplars were legacy/unprovenanced or trimmed by the sample cap.
    """
    lib = load_library()
    names = list(lib["speakers"])
    historical = _historical_meeting_sets(names, transcripts_dir)
    speakers = []
    for name, data in lib["speakers"].items():
        samples = _samples_of(data)
        active_sample_count = sum(1 for sample in samples if sample.get("active") is not False)
        sample_meeting_count = len({key for key in (_meeting_key(s) for s in samples) if key})
        historical_meeting_count = len(historical.get(name, set()))
        meeting_count = max(sample_meeting_count, historical_meeting_count)
        speakers.append({
            "name": name,
            "sample_count": len(samples),
            "active_sample_count": active_sample_count,
            "archived_sample_count": len(samples) - active_sample_count,
            "meeting_count": meeting_count,
            "sample_meeting_count": sample_meeting_count,
            "historical_meeting_count": historical_meeting_count,
            "profile_status": _profile_status(active_sample_count, meeting_count),
            "last_updated": data.get("last_updated", ""),
        })
    return speakers


def library_summary(
    transcripts_dir: str | Path = RAW_TRANSCRIPTS_DIR,
) -> dict:
    """Return aggregate library totals for the Voice Library header.

    Meeting totals are a union of trustworthy historical sidecars, so a
    meeting with several enrolled speakers is counted once. The sample total
    is the number of retained exemplars currently stored on disk.
    """
    lib = load_library()
    names = list(lib["speakers"])
    historical = _historical_meeting_sets(names, transcripts_dir)
    meeting_keys = set().union(*(set(values) for values in historical.values())) if historical else set()
    sample_count = sum(len(_samples_of(entry)) for entry in lib["speakers"].values())
    active_sample_count = sum(
        1 for entry in lib["speakers"].values() for sample in _samples_of(entry)
        if sample.get("active") is not False
    )
    return {
        "speaker_count": len(names),
        "sample_count": sample_count,
        "active_sample_count": active_sample_count,
        "meeting_count": len(meeting_keys),
    }


def reassess_library_quality(
    max_samples: int = _MAX_SAMPLES,
    *,
    dry_run: bool = False,
    report_path: str | Path | None = None,
    inspect_audio: bool = False,
) -> dict:
    """Re-score evidence and rebuild active sets, optionally without saving.

    Audio inspection is opt-in: old samples often point at files that have
    moved or were never locally decodable, and a missing clip must not make a
    historical, provenance-backed embedding disappear. Failures are reported
    against that sample and the structural assessment remains usable.
    """
    lib = load_library()
    total = active = archived = 0
    changes = []
    inspected = inspection_failures = 0
    audio_cache: dict[tuple[str, object, object], dict] = {}
    for name, entry in lib["speakers"].items():
        samples = _samples_of(entry)
        before = {}
        for sample in samples:
            sample_id = sample.get("id") or _sample_id(sample)
            before[sample_id] = {
                "quality_score": sample.get("quality_score"),
                "active": sample.get("active"),
            }
            if inspect_audio and sample.get("audio_file"):
                audio_key = (
                    str(sample["audio_file"]),
                    sample.get("segment_start"),
                    sample.get("segment_end"),
                )
                metrics = audio_cache.get(audio_key)
                if metrics is None:
                    try:
                        metrics = _audio_quality_from_path(
                            sample["audio_file"],
                            segment_start=sample.get("segment_start"),
                            segment_end=sample.get("segment_end"),
                        )
                    except Exception as exc:  # noqa: BLE001 - report and retain structural score
                        metrics = {
                            "audio_metrics_version": _AUDIO_QUALITY_VERSION,
                            "audio_reason": f"inspection failed: {exc}",
                        }
                    audio_cache[audio_key] = metrics
                sample.update(metrics)
                inspected += 1
                if "acoustic_quality" not in metrics:
                    inspection_failures += 1
            sample.update(_assess_sample_quality(sample.get("source", "legacy"), sample))
        entry["samples"] = samples
        _refresh_active_samples(entry, max(1, max_samples))
        for sample in samples:
            sample_id = sample.get("id") or _sample_id(sample)
            previous = before[sample_id]
            if (previous["quality_score"] != sample.get("quality_score")
                    or previous["active"] != sample.get("active")):
                changes.append({
                    "speaker": name,
                    "sample_id": sample_id,
                    "before_quality_score": previous["quality_score"],
                    "after_quality_score": sample.get("quality_score"),
                    "before_active": previous["active"],
                    "after_active": sample.get("active"),
                    "structural_quality": sample.get("structural_quality"),
                    "acoustic_quality": sample.get("acoustic_quality"),
                    "audio_reason": sample.get("audio_reason"),
                    "quality_reasons": sample.get("quality_reasons"),
                })
        total += len(samples)
        active += sum(1 for sample in samples if sample.get("active") is True)
        archived += sum(1 for sample in samples if sample.get("active") is False)
    result = {
        "dry_run": dry_run,
        "inspect_audio": inspect_audio,
        "speaker_count": len(lib["speakers"]),
        "sample_count": total,
        "active_sample_count": active,
        "archived_sample_count": archived,
        "max_active_samples": max(1, max_samples),
        "quality_version": _QUALITY_VERSION,
        "audio_samples_inspected": inspected,
        "audio_inspection_failures": inspection_failures,
        "changes": changes,
    }
    if report_path:
        path = Path(report_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2) + "\n")
        result["report_path"] = str(path.resolve())
    if not dry_run:
        save_library(lib)
    return result


def list_samples(name: str) -> list[dict]:
    """List sample metadata for one speaker without exposing embeddings."""
    lib = load_library()
    entry = lib["speakers"].get(name)
    if entry is None:
        return []
    fields = (
        "source", "added_at", "updated_at", "source_file", "audio_file",
        "speaker_id", "segment_start", "segment_end", "embedding_dim", "model",
        "label_source", "observed_name", "quality_score", "quality_state",
        "quality_version", "quality_reasons", "structural_quality", "acoustic_quality",
        "audio_snr_db", "audio_speech_ratio", "audio_clipping_ratio",
        "audio_metrics_version", "audio_reason", "active", "total_talk_seconds", "turn_count",
    )
    result = []
    for sample in _samples_of(entry):
        item = {key: sample[key] for key in fields if key in sample}
        item["id"] = sample.get("id") or _sample_id(sample)
        result.append(item)
    return result


def delete_sample(name: str, sample_id: str) -> bool:
    """Remove one exemplar by id, deleting an empty speaker profile."""
    lib = load_library()
    entry = lib["speakers"].get(name)
    if entry is None:
        return False
    samples = _samples_of(entry)
    for index, sample in enumerate(samples):
        if (sample.get("id") or _sample_id(sample)) != sample_id:
            continue
        samples.pop(index)
        if samples:
            entry["samples"] = samples
            entry["last_updated"] = _now()
        else:
            del lib["speakers"][name]
        save_library(lib)
        return True
    return False


def delete_speaker(name: str) -> bool:
    """Delete a speaker from the voice library."""
    lib = load_library()
    if name not in lib["speakers"]:
        return False
    del lib["speakers"][name]
    save_library(lib)
    return True


def rename_speaker(old_name: str, new_name: str) -> bool:
    """Rename a speaker (merges exemplars if the new name already exists).

    This is also the merge primitive: rename A → B when B exists folds A's
    exemplars into B and deletes A.
    """
    lib = load_library()
    if old_name not in lib["speakers"]:
        return False
    new_name = " ".join((new_name or "").strip().split())
    if not new_name:
        return False
    # Prefer an existing case-insensitive target so rename "leslie" → "Leslie"
    # lands on the canonical key when present.
    new_name = _canonical_speaker_name(lib, new_name)
    if new_name == old_name:
        return True
    if new_name in lib["speakers"] and new_name != old_name:
        # Merge exemplars into the existing target rather than erroring.
        target = lib["speakers"][new_name]
        target_samples = target.setdefault("samples", _samples_of(target))
        target_samples.extend(_samples_of(lib["speakers"][old_name]))
        _refresh_active_samples(target, _MAX_SAMPLES)
        target["last_updated"] = _now()
        del lib["speakers"][old_name]
    else:
        lib["speakers"][new_name] = lib["speakers"].pop(old_name)
        lib["speakers"][new_name]["last_updated"] = _now()
    save_library(lib)
    return True


def merge_speakers(source_name: str, target_name: str) -> bool:
    """Merge `source_name` into `target_name` (exemplars + delete source).

    Requires both names to exist. Prefer this over rename when the UI intent is
    explicitly "merge these two people".
    """
    lib = load_library()
    if source_name not in lib["speakers"] or target_name not in lib["speakers"]:
        return False
    if source_name == target_name:
        return True
    return rename_speaker(source_name, target_name)


# ── CLI ─────────────────────────────────────────────────────────────────────

def _cli():
    """Command-line interface for voice library management."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Voice library management CLI")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="List all enrolled speakers (JSON)")
    sub.add_parser("summary", help="Show aggregate speaker, sample, and meeting totals (JSON)")

    samples_p = sub.add_parser("samples", help="List sample provenance for a speaker")
    samples_p.add_argument("--name", required=True)

    enroll_p = sub.add_parser("enroll", help="Enroll a speaker from audio")
    enroll_p.add_argument("--name", required=True)
    enroll_p.add_argument("--audio", required=True)
    enroll_p.add_argument("--start", type=float, default=None)
    enroll_p.add_argument("--end", type=float, default=None)

    diar_p = sub.add_parser("enroll-diarized", help="Enroll a speaker's stored embedding from a diarized sidecar")
    diar_p.add_argument("--name", required=True)
    diar_p.add_argument("--json", required=True, help="Path to _diarized.json")
    diar_p.add_argument("--id", required=True, help="Speaker id in the sidecar")

    bulk_p = sub.add_parser("enroll-from-transcripts", help="Build the library from all tagged transcripts in a directory")
    bulk_p.add_argument("--dir", required=True, help="Directory of *_diarized.json files")
    bulk_p.add_argument(
        "--name",
        action="append",
        default=None,
        help="Backfill only this speaker; repeat for multiple speakers",
    )
    bulk_p.add_argument(
        "--audio-fallback",
        action="store_true",
        help="Extract a bounded audio clip when a legacy sidecar has no stored embedding",
    )
    bulk_p.add_argument(
        "--include-legacy",
        action="store_true",
        help="Explicitly include metadata-free legacy labels (unsafe unless independently audited)",
    )
    bulk_p.add_argument(
        "--no-legacy",
        action="store_true",
        help="Deprecated compatibility flag; metadata-free legacy labels are excluded by default",
    )
    bulk_p.add_argument(
        "--include-merged",
        action="store_true",
        help="Include derived Merged-* sidecars; normally child meetings are the evidence",
    )
    bulk_p.add_argument(
        "--max-samples",
        type=int,
        default=_MAX_SAMPLES,
        help=f"Maximum active matching exemplars per speaker (default: {_MAX_SAMPLES}); archive is preserved",
    )
    bulk_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect candidates and print the report without changing the voice library",
    )
    bulk_p.add_argument(
        "--report",
        help="Optional JSON path for the inventory/backfill report",
    )
    bulk_p.add_argument(
        "--alias-file",
        help="JSON file mapping observed legacy labels to canonical speaker names",
    )
    bulk_p.add_argument(
        "--stored-embeddings-only",
        action="store_true",
        help="Use only sidecar speaker embeddings; do not select audio-fallback candidates",
    )

    reassess_p = sub.add_parser(
        "reassess-quality",
        help="Score existing samples and rebuild active matching sets without deleting evidence",
    )
    reassess_p.add_argument(
        "--max-samples",
        type=int,
        default=_MAX_SAMPLES,
        help=f"Maximum active matching exemplars per speaker (default: {_MAX_SAMPLES})",
    )
    reassess_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Calculate and report the proposed quality changes without saving the library",
    )
    reassess_p.add_argument(
        "--report",
        help="Optional JSON path for the reassessment report",
    )
    reassess_p.add_argument(
        "--audio",
        action="store_true",
        help="Inspect decodable source clips for acoustic cleanliness (read-only unless dry-run is omitted)",
    )

    delete_p = sub.add_parser("delete", help="Delete a speaker")
    delete_p.add_argument("--name", required=True)

    delete_sample_p = sub.add_parser("delete-sample", help="Delete one speaker sample")
    delete_sample_p.add_argument("--name", required=True)
    delete_sample_p.add_argument("--id", required=True, help="Sample id from 'samples'")

    rename_p = sub.add_parser("rename", help="Rename a speaker (merges if --new already exists)")
    rename_p.add_argument("--old", required=True)
    rename_p.add_argument("--new", required=True)

    merge_p = sub.add_parser("merge", help="Merge one speaker into another (keep target name)")
    merge_p.add_argument("--from", dest="source", required=True, help="Speaker to absorb and delete")
    merge_p.add_argument("--into", dest="target", required=True, help="Speaker that keeps the name")

    calendar_p = sub.add_parser(
        "set-calendar-emails",
        help="Associate Microsoft 365 attendee email addresses with a speaker",
    )
    calendar_p.add_argument("--name", required=True)
    calendar_p.add_argument("--email", action="append", required=True)

    args = parser.parse_args()

    if args.command == "list":
        print(json.dumps(list_speakers(), indent=2, ensure_ascii=False))

    elif args.command == "summary":
        print(json.dumps(library_summary(), indent=2, ensure_ascii=False))

    elif args.command == "samples":
        print(json.dumps(list_samples(args.name), indent=2, ensure_ascii=False))

    elif args.command == "enroll":
        try:
            result = enroll_speaker(name=args.name, audio_path=args.audio,
                                    segment_start=args.start, segment_end=args.end)
            print(json.dumps({"ok": True, "sample_count": len(result.get("samples", []))}))
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
            sys.exit(1)

    elif args.command == "enroll-diarized":
        try:
            result = enroll_from_diarized(args.name, args.json, args.id)
            print(json.dumps({"ok": True, "sample_count": len(result.get("samples", []))}))
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
            sys.exit(1)

    elif args.command == "enroll-from-transcripts":
        try:
            print(json.dumps({
                "ok": True,
                **enroll_from_transcripts(
                    args.dir,
                    names=args.name,
                    audio_fallback=args.audio_fallback,
                    include_legacy=args.include_legacy and not args.no_legacy,
                    include_merged=args.include_merged,
                    max_samples=max(1, args.max_samples),
                    dry_run=args.dry_run,
                    report_path=args.report,
                    aliases=load_backfill_aliases(args.alias_file) if args.alias_file else None,
                    stored_embeddings_only=args.stored_embeddings_only,
                ),
            }))
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
            sys.exit(1)

    elif args.command == "reassess-quality":
        try:
            result = reassess_library_quality(
                max(1, args.max_samples),
                dry_run=args.dry_run,
                report_path=args.report,
                inspect_audio=args.audio,
            )
            # Detailed sample movements belong in --report; keeping stdout
            # compact lets the app and shell users reliably consume a large
            # library reassessment.
            change_count = len(result.pop("changes", []))
            print(json.dumps({
                "ok": True,
                **result,
                "change_count": change_count,
            }))
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
            sys.exit(1)

    elif args.command == "delete":
        ok = delete_speaker(args.name)
        print(json.dumps({"ok": ok}) if ok else json.dumps({"ok": False, "error": f"Speaker '{args.name}' not found"}))
        if not ok:
            sys.exit(1)

    elif args.command == "delete-sample":
        ok = delete_sample(args.name, args.id)
        print(json.dumps({"ok": ok}) if ok else json.dumps({
            "ok": False,
            "error": f"Sample '{args.id}' not found for '{args.name}'",
        }))
        if not ok:
            sys.exit(1)

    elif args.command == "rename":
        try:
            ok = rename_speaker(args.old, args.new)
            if ok:
                print(json.dumps({"ok": True}))
            else:
                print(json.dumps({"ok": False, "error": f"Speaker '{args.old}' not found"}), file=sys.stderr)
                sys.exit(1)
        except ValueError as e:
            print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
            sys.exit(1)

    elif args.command == "merge":
        try:
            ok = merge_speakers(args.source, args.target)
            if ok:
                print(json.dumps({"ok": True}))
            else:
                print(
                    json.dumps({
                        "ok": False,
                        "error": f"Cannot merge '{args.source}' into '{args.target}' (missing name?)",
                    }),
                    file=sys.stderr,
                )
                sys.exit(1)
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)}), file=sys.stderr)
            sys.exit(1)

    elif args.command == "set-calendar-emails":
        ok = set_calendar_emails(args.name, args.email)
        print(json.dumps({"ok": ok, "name": args.name, "calendar_emails": args.email}))
        if not ok:
            sys.exit(1)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    _cli()
