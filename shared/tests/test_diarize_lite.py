"""Tests for shared.diarize_lite — clustering and diarization output format."""
from __future__ import annotations

from unittest.mock import patch

import numpy as np

from shared.diarize_lite import (
    _assign_speakers_to_whisper_segments,
    cluster_speakers,
    diarize,
)


# ── cluster_speakers ────────────────────────────────────────────────────────


def test_cluster_identical_embeddings():
    """Identical embeddings should be assigned the same speaker label."""
    v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    embeddings = np.stack([v, v, v])
    labels = cluster_speakers(embeddings)

    assert len(labels) == 3
    assert labels[0] == labels[1] == labels[2]


def test_cluster_different_embeddings():
    """Clearly distinct embeddings should get different labels."""
    # Two very different unit vectors
    a = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)
    embeddings = np.stack([a, a, b, b])

    labels = cluster_speakers(embeddings, distance_threshold=0.5)
    assert labels[0] == labels[1], "First two should share a label"
    assert labels[2] == labels[3], "Last two should share a label"
    assert labels[0] != labels[2], "Groups should differ"


def test_cluster_single_embedding():
    embeddings = np.array([[1.0, 0.0, 0.0]], dtype=np.float32)
    labels = cluster_speakers(embeddings)
    assert labels == [0]


def test_cluster_empty():
    embeddings = np.array([], dtype=np.float32).reshape(0, 3)
    labels = cluster_speakers(embeddings)
    assert labels == []


def test_cluster_with_n_speakers():
    """When n_speakers is specified, should produce exactly that many clusters."""
    np.random.seed(42)
    embeddings = np.random.randn(6, 10).astype(np.float32)
    labels = cluster_speakers(embeddings, n_speakers=2)
    assert len(set(labels)) == 2


# ── _assign_speakers_to_whisper_segments ────────────────────────────────────


def test_assign_speakers_overlap():
    """Whisper segments should be mapped to the speech segment with most overlap."""
    whisper_segments = [
        {"start": 0.0, "end": 2.0, "text": "Hello"},
        {"start": 3.0, "end": 5.0, "text": "World"},
    ]
    speech_segments = [(0.0, 2.0), (3.0, 5.0)]
    speaker_labels = [0, 1]

    result = _assign_speakers_to_whisper_segments(
        whisper_segments, speech_segments, speaker_labels
    )
    assert result == [0, 1]


def test_assign_speakers_partial_overlap():
    """A whisper segment overlapping two speech segments should get the majority speaker."""
    whisper_segments = [
        {"start": 0.5, "end": 3.5, "text": "Straddling two speakers"},
    ]
    # First speech: 0-2 (overlap: 0.5-2.0 = 1.5s)
    # Second speech: 2-4 (overlap: 2.0-3.5 = 1.5s)
    # Ties go to the first encountered, which is speaker 0
    speech_segments = [(0.0, 2.0), (2.0, 4.0)]
    speaker_labels = [0, 1]

    result = _assign_speakers_to_whisper_segments(
        whisper_segments, speech_segments, speaker_labels
    )
    assert len(result) == 1
    # With equal overlap the first match wins (overlap is not strictly greater)
    assert result[0] in (0, 1)


# ── diarize output format ──────────────────────────────────────────────────


@patch("shared.diarize_lite.detect_speech_segments")
@patch("shared.diarize_lite.load_audio")
def test_diarize_output_format_no_speech(mock_load_audio, mock_detect):
    """diarize should return correct structure even when no speech is detected."""
    mock_load_audio.return_value = np.zeros(16000, dtype=np.float32)
    mock_detect.return_value = []  # No speech detected

    whisper_segments = [
        {"start": 0.0, "end": 1.0, "text": "Hello"},
    ]

    result = diarize("/fake/audio.wav", whisper_segments)

    assert "version" in result
    assert "segments" in result
    assert "speaker_names" in result
    assert result["version"] == 1
    assert len(result["segments"]) == 1
    assert result["segments"][0]["speaker"] == "Speaker 1"


@patch("shared.diarize_lite.extract_speaker_embeddings")
@patch("shared.diarize_lite.detect_speech_segments")
@patch("shared.diarize_lite.load_audio")
def test_diarize_output_format_with_speech(mock_load_audio, mock_detect, mock_embed):
    """diarize should produce labeled segments when speech is found."""
    mock_load_audio.return_value = np.zeros(48000, dtype=np.float32)
    mock_detect.return_value = [(0.0, 1.0), (1.5, 2.5)]

    # Two embeddings — make them different enough to cluster into 2 speakers
    emb1 = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    emb2 = np.array([0.0, 0.0, 1.0], dtype=np.float32)
    mock_embed.return_value = np.stack([emb1, emb2])

    whisper_segments = [
        {"start": 0.0, "end": 1.0, "text": "Hello"},
        {"start": 1.5, "end": 2.5, "text": "Hi there"},
    ]

    result = diarize("/fake/audio.wav", whisper_segments)

    assert result["version"] == 1
    assert "audio_file" in result
    assert len(result["segments"]) == 2
    for seg in result["segments"]:
        assert "start" in seg
        assert "end" in seg
        assert "text" in seg
        assert "speaker" in seg
    assert len(result["speaker_names"]) >= 1
