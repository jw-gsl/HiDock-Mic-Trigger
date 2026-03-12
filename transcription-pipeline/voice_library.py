"""Voice library for speaker identification using SpeechBrain ECAPA-TDNN.

Storage layout:
    ~/HiDock/Voice Library/
        embeddings.json     # {"James": {"embedding": [...], "sample_count": 5, "last_updated": "..."}}
        samples/            # Short WAV clips per speaker segment

Requires: speechbrain >= 1.0 (uncomment in requirements.txt)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import config

VOICE_LIB_DIR = config.VOICE_LIBRARY_DIR
EMBEDDINGS_PATH = VOICE_LIB_DIR / "embeddings.json"
SAMPLES_DIR = VOICE_LIB_DIR / "samples"

COSINE_THRESHOLD = 0.75

_classifier = None


def _get_classifier():
    """Lazy-load SpeechBrain ECAPA-TDNN classifier."""
    global _classifier
    if _classifier is None:
        import torch
        from speechbrain.inference.speaker import EncoderClassifier

        device = config.WHISPER_DEVICE
        if device == "mps" and not torch.backends.mps.is_available():
            device = "cpu"

        _classifier = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir=str(VOICE_LIB_DIR / ".speechbrain_cache"),
            run_opts={"device": device},
        )
    return _classifier


def _load_embeddings() -> dict:
    """Load stored voice embeddings."""
    if not EMBEDDINGS_PATH.exists():
        return {}
    try:
        return json.loads(EMBEDDINGS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_embeddings(data: dict) -> None:
    """Save voice embeddings atomically."""
    EMBEDDINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = EMBEDDINGS_PATH.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(EMBEDDINGS_PATH)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def extract_embedding(audio_path: str) -> list[float]:
    """Extract a 192-dim speaker embedding from an audio segment."""
    classifier = _get_classifier()
    signal = classifier.load_audio(audio_path)
    embedding = classifier.encode_batch(signal.unsqueeze(0))
    return embedding.squeeze().cpu().tolist()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two embedding vectors."""
    a_arr = np.array(a)
    b_arr = np.array(b)
    dot = np.dot(a_arr, b_arr)
    norm = np.linalg.norm(a_arr) * np.linalg.norm(b_arr)
    if norm == 0:
        return 0.0
    return float(dot / norm)


def enroll(name: str, audio_path: str) -> dict:
    """Enroll or update a speaker in the voice library.

    Extracts embedding from audio_path and averages with existing
    embeddings for the speaker (if any).
    """
    embedding = extract_embedding(audio_path)
    data = _load_embeddings()

    if name in data:
        existing = data[name]
        old_emb = existing["embedding"]
        old_count = existing["sample_count"]
        # Running average
        new_emb = [
            (old_emb[i] * old_count + embedding[i]) / (old_count + 1)
            for i in range(len(embedding))
        ]
        data[name] = {
            "embedding": new_emb,
            "sample_count": old_count + 1,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }
    else:
        data[name] = {
            "embedding": embedding,
            "sample_count": 1,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    # Save audio sample
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    sample_dest = SAMPLES_DIR / f"{name}_{int(time.time())}.wav"
    src = Path(audio_path)
    if src.exists():
        import shutil
        shutil.copy2(str(src), str(sample_dest))

    _save_embeddings(data)
    return {
        "name": name,
        "sample_count": data[name]["sample_count"],
        "status": "enrolled",
    }


def identify(audio_path: str) -> dict:
    """Identify a speaker from audio against the voice library.

    Returns the best match if above COSINE_THRESHOLD, else unknown.
    """
    embedding = extract_embedding(audio_path)
    data = _load_embeddings()

    if not data:
        return {"speaker": None, "confidence": 0.0, "status": "no_enrolled_speakers"}

    best_name = None
    best_score = -1.0
    for name, info in data.items():
        score = cosine_similarity(embedding, info["embedding"])
        if score > best_score:
            best_score = score
            best_name = name

    if best_score >= COSINE_THRESHOLD:
        return {"speaker": best_name, "confidence": round(best_score, 4), "status": "identified"}
    return {"speaker": None, "confidence": round(best_score, 4), "status": "unknown"}


def list_speakers() -> list[dict]:
    """List all enrolled speakers."""
    data = _load_embeddings()
    speakers = []
    for name, info in data.items():
        speakers.append({
            "name": name,
            "sample_count": info["sample_count"],
            "last_updated": info.get("last_updated"),
        })
    return speakers


def identify_speakers(audio_path: str, diarization) -> dict[str, str]:
    """Match diarization speaker IDs to enrolled voice library names.

    Args:
        audio_path: Path to the full audio file.
        diarization: pyannote Annotation object.

    Returns:
        Dict mapping speaker IDs (e.g. "SPEAKER_00") to names (e.g. "James").
    """
    import tempfile
    import torchaudio

    data = _load_embeddings()
    if not data:
        return {}

    waveform, sample_rate = torchaudio.load(audio_path)
    speaker_map = {}

    for speaker_id in diarization.labels():
        # Extract longest segment for this speaker
        segments = [
            (turn.start, turn.end)
            for turn, _, spk in diarization.itertracks(yield_label=True)
            if spk == speaker_id
        ]
        if not segments:
            continue

        # Use the longest segment
        longest = max(segments, key=lambda s: s[1] - s[0])
        start_sample = int(longest[0] * sample_rate)
        end_sample = int(longest[1] * sample_rate)
        segment_waveform = waveform[:, start_sample:end_sample]

        # Save to temp file for embedding extraction
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            torchaudio.save(tmp.name, segment_waveform, sample_rate)
            result = identify(tmp.name)
            Path(tmp.name).unlink(missing_ok=True)

        if result["status"] == "identified" and result["speaker"]:
            speaker_map[speaker_id] = result["speaker"]

    return speaker_map


def main():
    parser = argparse.ArgumentParser(description="Voice Library Management")
    sub = parser.add_subparsers(dest="command")

    p_enroll = sub.add_parser("enroll", help="Enroll a speaker")
    p_enroll.add_argument("name", help="Speaker name")
    p_enroll.add_argument("audio", help="Path to audio segment")

    p_identify = sub.add_parser("identify", help="Identify a speaker")
    p_identify.add_argument("audio", help="Path to audio segment")

    p_list = sub.add_parser("list", help="List enrolled speakers")

    args = parser.parse_args()

    if args.command == "enroll":
        result = enroll(args.name, args.audio)
        print(json.dumps(result))
    elif args.command == "identify":
        result = identify(args.audio)
        print(json.dumps(result))
    elif args.command == "list":
        speakers = list_speakers()
        print(json.dumps(speakers, indent=2))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
