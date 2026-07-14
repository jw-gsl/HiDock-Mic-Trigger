"""Tests for the multi-exemplar voice library."""
import json

import pytest

import shared.voice_library_lite as vl


@pytest.fixture(autouse=True)
def temp_library(tmp_path, monkeypatch):
    monkeypatch.setattr(vl, "VOICE_LIBRARY_DIR", tmp_path)
    monkeypatch.setattr(vl, "EMBEDDINGS_FILE", tmp_path / "embeddings.json")
    yield


def test_multi_exemplar_best_of_match():
    vl.enroll_embedding("James", [1.0, 0.0], embed_dim=2)
    vl.enroll_embedding("James", [0.9, 0.44], embed_dim=2)   # a different-sounding day
    vl.enroll_embedding("Chris", [0.0, 1.0], embed_dim=2)
    # Probe near James's SECOND exemplar → best-of still matches James.
    name, score = vl.identify_speaker([0.88, 0.47], threshold=0.7)
    assert name == "James"
    assert score > 0.9
    assert vl.list_speakers()[0]["sample_count"] == 2


def test_calendar_candidate_filter_limits_voice_matching():
    vl.enroll_embedding("James", [1.0, 0.0], embed_dim=2)
    vl.enroll_embedding("Chris", [0.0, 1.0], embed_dim=2)

    name, score = vl.identify_speaker(
        [0.0, 1.0], threshold=0.7, allowed_names={"James"}
    )
    assert name is None
    assert score == 0.0

    name, score = vl.identify_speaker(
        [0.0, 1.0], threshold=0.7, allowed_names={"Chris"}
    )
    assert name == "Chris"
    assert score == pytest.approx(1.0)


def test_calendar_emails_are_saved_as_normalized_identity_aliases():
    vl.enroll_embedding("James", [1.0, 0.0], embed_dim=2)
    assert vl.set_calendar_emails("James", [" JAMES@EXAMPLE.COM ", "james@example.com"])
    assert vl.load_library()["speakers"]["James"]["calendar_emails"] == ["james@example.com"]


def test_legacy_single_embedding_migrates_on_load():
    vl.EMBEDDINGS_FILE.write_text(json.dumps({
        "speakers": {"Old": {"embedding": [0.5, 0.5], "embedding_dim": 2, "model": "x"}}
    }))
    lib = vl.load_library()
    entry = lib["speakers"]["Old"]
    assert len(entry["samples"]) == 1
    assert "embedding" not in entry        # single source of truth = samples


def test_dedup_skips_near_identical():
    vl.enroll_embedding("James", [1.0, 0.0], embed_dim=2)
    vl.enroll_embedding("James", [1.0, 0.001], embed_dim=2)   # ~identical
    assert len(vl.load_library()["speakers"]["James"]["samples"]) == 1


def test_enroll_from_transcripts_skips_unverified_auto(tmp_path):
    # One verified James, one unverified auto "Chris" → only James is enrolled.
    sidecar = {
        "speaker_names": {"0": "James", "1": "Chris", "2": "Speaker 3"},
        "speaker_meta": {
            "0": {"source": "user", "verified": True},
            "1": {"source": "auto", "verified": False},
            "2": {"source": "generic", "verified": False},
        },
        "speaker_embeddings": {"0": [1.0, 0.0], "1": [0.0, 1.0], "2": [0.7, 0.7]},
    }
    (tmp_path / "m1_diarized.json").write_text(json.dumps(sidecar))
    result = vl.enroll_from_transcripts(tmp_path)
    assert result["enrolled"] == 1
    names = {s["name"] for s in vl.list_speakers()}
    assert names == {"James"}
