"""Tests for shared.voice_library_lite — speaker enrollment, identification, management."""
from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from shared.voice_library_lite import (
    cosine_similarity,
    delete_speaker,
    enroll_speaker,
    identify_speaker,
    list_speakers,
    load_library,
    rename_speaker,
    save_library,
)


# ── cosine_similarity ───────────────────────────────────────────────────────


def test_cosine_similarity_identical():
    v = [1.0, 2.0, 3.0]
    assert cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal():
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert cosine_similarity(a, b) == pytest.approx(0.0)


def test_cosine_similarity_zero_vector():
    a = [0.0, 0.0, 0.0]
    b = [1.0, 2.0, 3.0]
    assert cosine_similarity(a, b) == 0.0
    assert cosine_similarity(b, a) == 0.0


def test_cosine_similarity_opposite():
    a = [1.0, 0.0]
    b = [-1.0, 0.0]
    assert cosine_similarity(a, b) == pytest.approx(-1.0)


# ── load_library / save_library ─────────────────────────────────────────────


def test_load_library_missing_file(tmp_path):
    """Loading from a non-existent file should return default structure."""
    fake_path = tmp_path / "nonexistent.json"
    with patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_path):
        lib = load_library()
    assert lib == {"speakers": {}}


def test_load_library_empty_file(tmp_path):
    """Loading from an empty/corrupt file should return default structure."""
    fake_path = tmp_path / "bad.json"
    fake_path.write_text("not valid json")
    with patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_path):
        lib = load_library()
    assert lib == {"speakers": {}}


def test_save_load_roundtrip(tmp_path):
    """save_library -> load_library should preserve data."""
    fake_file = tmp_path / "embeddings.json"
    fake_dir = tmp_path

    lib = {
        "speakers": {
            "Alice": {
                "embedding": [0.1, 0.2, 0.3],
                "embedding_dim": 3,
                "model": "mfcc-v1",
                "sample_count": 1,
                "enrolled_at": "2025-01-01T00:00:00+00:00",
                "last_updated": "2025-01-01T00:00:00+00:00",
            }
        }
    }

    with patch("shared.voice_library_lite.VOICE_LIBRARY_DIR", fake_dir), \
         patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_file):
        save_library(lib)
        loaded = load_library()

    assert loaded["speakers"]["Alice"]["embedding"] == [0.1, 0.2, 0.3]
    assert loaded["speakers"]["Alice"]["sample_count"] == 1


# ── enroll_speaker ──────────────────────────────────────────────────────────


@patch("shared.voice_library_lite._get_speaker_embed_session", return_value=None)
@patch("shared.voice_library_lite.load_audio")
def test_enroll_speaker_creates_entry(mock_load_audio, mock_session, tmp_path):
    """First enrollment should create a new speaker entry."""
    mock_load_audio.return_value = np.random.randn(16000).astype(np.float32)

    fake_file = tmp_path / "embeddings.json"
    fake_dir = tmp_path

    with patch("shared.voice_library_lite.VOICE_LIBRARY_DIR", fake_dir), \
         patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_file):
        result = enroll_speaker("Alice", "/fake/audio.wav")

    assert result["sample_count"] == 1
    assert "embedding" in result
    assert result["model"] == "mfcc-v1"


@patch("shared.voice_library_lite._get_speaker_embed_session", return_value=None)
@patch("shared.voice_library_lite.load_audio")
def test_enroll_speaker_second_time_increases_count(mock_load_audio, mock_session, tmp_path):
    """Re-enrolling should increment sample_count via running average."""
    mock_load_audio.return_value = np.random.randn(16000).astype(np.float32)

    fake_file = tmp_path / "embeddings.json"
    fake_dir = tmp_path

    with patch("shared.voice_library_lite.VOICE_LIBRARY_DIR", fake_dir), \
         patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_file):
        enroll_speaker("Alice", "/fake/audio.wav")
        result = enroll_speaker("Alice", "/fake/audio2.wav")

    assert result["sample_count"] == 2


# ── identify_speaker ────────────────────────────────────────────────────────


def test_identify_speaker_match(tmp_path):
    """Should return matching speaker name and high confidence."""
    emb = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    lib = {
        "speakers": {
            "Alice": {
                "embedding": [1.0, 0.0, 0.0],
                "embedding_dim": 3,
                "model": "mfcc-v1",
                "sample_count": 1,
            }
        }
    }

    fake_file = tmp_path / "embeddings.json"
    fake_dir = tmp_path
    with patch("shared.voice_library_lite.VOICE_LIBRARY_DIR", fake_dir), \
         patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_file):
        save_library(lib)
        name, confidence = identify_speaker(emb, threshold=0.7)

    assert name == "Alice"
    assert confidence == pytest.approx(1.0)


def test_identify_speaker_no_match(tmp_path):
    """Should return None when no speaker exceeds threshold."""
    emb = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    lib = {
        "speakers": {
            "Bob": {
                "embedding": [0.0, 1.0, 0.0],
                "embedding_dim": 3,
                "model": "mfcc-v1",
                "sample_count": 1,
            }
        }
    }

    fake_file = tmp_path / "embeddings.json"
    fake_dir = tmp_path
    with patch("shared.voice_library_lite.VOICE_LIBRARY_DIR", fake_dir), \
         patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_file):
        save_library(lib)
        name, confidence = identify_speaker(emb, threshold=0.7)

    assert name is None
    assert confidence == 0.0


def test_identify_speaker_empty_library(tmp_path):
    """Should return None from empty library."""
    fake_file = tmp_path / "nonexistent.json"
    with patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_file):
        name, confidence = identify_speaker(np.array([1.0, 0.0]))

    assert name is None
    assert confidence == 0.0


# ── delete_speaker ──────────────────────────────────────────────────────────


def test_delete_speaker_removes_entry(tmp_path):
    lib = {
        "speakers": {
            "Alice": {
                "embedding": [1.0, 0.0],
                "embedding_dim": 2,
                "model": "mfcc-v1",
                "sample_count": 1,
            }
        }
    }

    fake_file = tmp_path / "embeddings.json"
    fake_dir = tmp_path
    with patch("shared.voice_library_lite.VOICE_LIBRARY_DIR", fake_dir), \
         patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_file):
        save_library(lib)
        result = delete_speaker("Alice")
        assert result is True
        remaining = load_library()

    assert "Alice" not in remaining["speakers"]


def test_delete_speaker_not_found(tmp_path):
    fake_file = tmp_path / "nonexistent.json"
    with patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_file):
        result = delete_speaker("Ghost")
    assert result is False


# ── rename_speaker ──────────────────────────────────────────────────────────


def test_rename_speaker_changes_key(tmp_path):
    lib = {
        "speakers": {
            "Alice": {
                "embedding": [1.0, 0.0],
                "embedding_dim": 2,
                "model": "mfcc-v1",
                "sample_count": 1,
                "last_updated": "2025-01-01T00:00:00+00:00",
            }
        }
    }

    fake_file = tmp_path / "embeddings.json"
    fake_dir = tmp_path
    with patch("shared.voice_library_lite.VOICE_LIBRARY_DIR", fake_dir), \
         patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_file):
        save_library(lib)
        result = rename_speaker("Alice", "Alicia")
        assert result is True
        updated = load_library()

    assert "Alicia" in updated["speakers"]
    assert "Alice" not in updated["speakers"]


def test_rename_speaker_not_found(tmp_path):
    fake_file = tmp_path / "nonexistent.json"
    with patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_file):
        result = rename_speaker("Ghost", "Phantom")
    assert result is False


# ── list_speakers ───────────────────────────────────────────────────────────


def test_list_speakers_format(tmp_path):
    lib = {
        "speakers": {
            "Alice": {
                "embedding": [1.0],
                "embedding_dim": 1,
                "model": "mfcc-v1",
                "sample_count": 3,
                "last_updated": "2025-06-01T00:00:00+00:00",
            },
            "Bob": {
                "embedding": [0.5],
                "embedding_dim": 1,
                "model": "mfcc-v1",
                "sample_count": 1,
                "last_updated": "2025-07-01T00:00:00+00:00",
            },
        }
    }

    fake_file = tmp_path / "embeddings.json"
    fake_dir = tmp_path
    with patch("shared.voice_library_lite.VOICE_LIBRARY_DIR", fake_dir), \
         patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_file):
        save_library(lib)
        speakers = list_speakers()

    assert len(speakers) == 2
    names = {s["name"] for s in speakers}
    assert names == {"Alice", "Bob"}
    for s in speakers:
        assert "name" in s
        assert "sample_count" in s
        assert "last_updated" in s
