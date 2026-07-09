"""Tests for speaker provenance + rematch helpers."""
from unittest.mock import patch

from shared.speaker_meta import (
    ensure_speaker_meta,
    infer_source,
    is_generic_name,
    rematch_diarized,
)


def test_is_generic_name():
    assert is_generic_name("Speaker 1")
    assert is_generic_name("Speaker 12")
    assert is_generic_name("")
    assert is_generic_name(None)
    assert not is_generic_name("James")
    assert not is_generic_name("Speaker Bob")


def test_infer_source():
    assert infer_source("Speaker 3") == "generic"
    assert infer_source("Chris") == "auto"


def test_ensure_speaker_meta_backfills_only_missing():
    data = {
        "speaker_names": {"0": "James", "1": "Speaker 2"},
        "speaker_meta": {"0": {"source": "user", "confidence": None, "verified": True}},
    }
    meta = ensure_speaker_meta(data)
    # existing entry untouched
    assert meta["0"]["source"] == "user" and meta["0"]["verified"] is True
    # missing entry inferred
    assert meta["1"]["source"] == "generic" and meta["1"]["verified"] is False


def test_rematch_matches_generic_from_stored_embedding():
    data = {
        "audio_file": "/nope.wav",
        "speaker_names": {"0": "James", "1": "Speaker 2"},
        "speaker_meta": {
            "0": {"source": "user", "confidence": None, "verified": True},
            "1": {"source": "generic", "confidence": None, "verified": False},
        },
        "speaker_embeddings": {"1": [0.1, 0.2, 0.3]},
        "segments": [
            {"speaker_id": 0, "start": 0, "end": 5, "text": "hi", "speaker": "James"},
            {"speaker_id": 1, "start": 5, "end": 9, "text": "yo", "speaker": "Speaker 2"},
        ],
    }
    with patch("shared.voice_library_lite.identify_speaker", return_value=("Chris", 0.81)):
        result = rematch_diarized(data, audio_fallback=False)
    assert result["rematched"] == 1
    assert data["speaker_names"]["1"] == "Chris"
    assert data["speaker_meta"]["1"] == {"source": "auto", "confidence": 0.81, "verified": False}
    # segment text reflects the new name (so a regenerated .md is correct)
    assert data["segments"][1]["speaker"] == "Chris"


def test_rematch_never_touches_verified_or_named():
    data = {
        "audio_file": "/nope.wav",
        "speaker_names": {"0": "James", "1": "Speaker 2"},
        "speaker_meta": {
            "0": {"source": "user", "confidence": None, "verified": True},
            "1": {"source": "auto", "confidence": 0.9, "verified": True},  # verified guest
        },
        "speaker_embeddings": {"0": [1.0], "1": [1.0]},
        "segments": [],
    }
    with patch("shared.voice_library_lite.identify_speaker", return_value=("X", 0.99)) as ident:
        result = rematch_diarized(data, audio_fallback=False)
    # neither is generic+unverified, so nothing eligible and identify never called
    assert result["eligible"] == 0
    assert result["rematched"] == 0
    ident.assert_not_called()
    assert data["speaker_names"]["0"] == "James"
