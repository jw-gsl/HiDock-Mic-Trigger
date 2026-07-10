"""Tests for speaker provenance + rematch helpers."""
from unittest.mock import patch

from shared.speaker_meta import (
    ensure_speaker_meta,
    infer_source,
    is_generic_name,
    rematch_diarized,
    resolve_name_collisions,
    score_speakers,
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


def test_resolve_name_collisions_keeps_best_demotes_rest():
    names = {"0": "Natasha", "1": "James", "2": "Natasha"}
    meta = {
        "0": {"source": "auto", "confidence": 0.6, "verified": False},
        "1": {"source": "auto", "confidence": 0.9, "verified": False},
        "2": {"source": "auto", "confidence": 0.8, "verified": False},
    }
    resolve_name_collisions(names, meta)
    assert names["2"] == "Natasha"       # higher confidence keeps the name
    assert names["0"] == "Speaker 1"     # demoted to generic
    assert meta["0"]["source"] == "generic"
    assert names["1"] == "James"         # untouched


def test_resolve_name_collisions_never_demotes_verified():
    names = {"0": "Natasha", "1": "Natasha"}
    meta = {
        "0": {"source": "user", "confidence": None, "verified": True},
        "1": {"source": "auto", "confidence": 0.99, "verified": False},
    }
    resolve_name_collisions(names, meta)
    assert names["0"] == "Natasha"       # verified is protected even vs higher conf
    assert names["1"] == "Speaker 2"     # the unverified one is demoted


def test_score_speakers_margin():
    # Speaker 0 clearly matches James (1.0) far above Chris → big margin.
    # Speaker 2's centroid is closer to Chris than to its assigned "James" →
    # the assignment is suspect (best != assigned, negative margin).
    data = {
        "speaker_names": {"0": "James", "1": "Speaker 2", "2": "James"},
        "speaker_embeddings": {"0": [1.0, 0.0], "1": [0.0, 1.0], "2": [0.2, 0.98]},
    }
    lib = {"speakers": {"James": {"embedding": [1.0, 0.0]},
                        "Chris": {"embedding": [0.0, 1.0]}}}
    with patch("shared.voice_library_lite.load_library", return_value=lib):
        scores = score_speakers(data)

    # Speaker 0: assigned James is clearly best; runner-up Chris much lower.
    assert scores["0"]["best"] == "James"
    assert scores["0"]["runnerUp"] == "Chris"
    assert scores["0"]["margin"] > 0.9

    # Speaker 2: assigned James but Chris matches better → margin negative, and
    # `best` names the real closest voice so the UI can flag it.
    assert scores["2"]["assigned"] == "James"
    assert scores["2"]["best"] == "Chris"
    assert scores["2"]["margin"] < 0


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
