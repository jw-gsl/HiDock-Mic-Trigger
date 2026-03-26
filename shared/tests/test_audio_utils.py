"""Tests for shared.audio_utils — MFCC extraction, embeddings, audio loading."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from shared.audio_utils import extract_embedding, extract_mfcc, load_audio, segment_audio


# ── extract_mfcc ────────────────────────────────────────────────────────────


def _sine_wave(freq: float = 440.0, duration: float = 1.0, sr: int = 16000) -> np.ndarray:
    """Generate a synthetic sine wave for testing."""
    t = np.arange(int(sr * duration)) / sr
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_extract_mfcc_shape():
    audio = _sine_wave(duration=1.0, sr=16000)
    n_mfcc = 40
    mfccs = extract_mfcc(audio, sr=16000, n_mfcc=n_mfcc)

    assert mfccs.shape[0] == n_mfcc, "First dimension should equal n_mfcc"
    assert mfccs.shape[1] > 0, "Should have at least one time frame"


def test_extract_mfcc_custom_params():
    audio = _sine_wave(duration=0.5, sr=16000)
    n_mfcc = 20
    mfccs = extract_mfcc(audio, sr=16000, n_mfcc=n_mfcc, n_fft=256, hop_length=128)

    assert mfccs.shape[0] == n_mfcc


def test_extract_mfcc_short_audio():
    """Very short audio (shorter than n_fft) should still work via padding."""
    audio = np.zeros(100, dtype=np.float32)
    mfccs = extract_mfcc(audio, sr=16000, n_mfcc=40, n_fft=512)
    assert mfccs.shape[0] == 40
    assert mfccs.shape[1] >= 1


# ── extract_embedding ───────────────────────────────────────────────────────


def test_extract_embedding_shape():
    audio = _sine_wave(duration=1.0)
    n_mfcc = 40
    emb = extract_embedding(audio, sr=16000, n_mfcc=n_mfcc)

    assert emb.ndim == 1, "Embedding should be 1-D"
    assert len(emb) == n_mfcc, "Length should equal n_mfcc in MFCC-fallback mode"


def test_extract_embedding_short_audio_returns_zeros():
    """Audio shorter than 400 samples should return a zero vector."""
    audio = np.zeros(100, dtype=np.float32)
    emb = extract_embedding(audio, sr=16000, n_mfcc=40)
    assert emb.ndim == 1
    assert len(emb) == 40
    np.testing.assert_array_equal(emb, np.zeros(40, dtype=np.float32))


def test_extract_embedding_with_onnx_session():
    """When an onnx_session is provided, should use neural embedding path."""
    fake_output = np.random.randn(1, 192).astype(np.float32)
    session = MagicMock()
    session.get_inputs.return_value = [MagicMock(name="audio_signal")]
    session.get_outputs.return_value = [MagicMock(name="logits")]
    session.get_inputs.return_value[0].name = "audio_signal"
    session.get_outputs.return_value[0].name = "logits"
    session.run.return_value = [fake_output]

    audio = _sine_wave(duration=1.0)
    emb = extract_embedding(audio, sr=16000, onnx_session=session)

    assert emb.ndim == 1
    assert len(emb) == 192
    session.run.assert_called_once()


# ── load_audio ──────────────────────────────────────────────────────────────


@patch("shared.audio_utils.sf")
def test_load_audio_correct_sample_rate(mock_sf):
    """load_audio should return audio at the requested sample rate."""
    sr_file = 16000
    samples = np.zeros(16000, dtype=np.float32)
    mock_sf.read.return_value = (samples, sr_file)

    result = load_audio("/fake/path.wav", sr=16000)
    assert isinstance(result, np.ndarray)
    mock_sf.read.assert_called_once()


@patch("shared.audio_utils.sf")
def test_load_audio_resamples(mock_sf):
    """load_audio should resample when file sr differs from target."""
    sr_file = 44100
    duration = 1.0
    samples = np.zeros(int(sr_file * duration), dtype=np.float32)
    mock_sf.read.return_value = (samples, sr_file)

    result = load_audio("/fake/path.wav", sr=16000)
    expected_len = int(duration * 16000)
    assert len(result) == expected_len


@patch("shared.audio_utils.sf")
def test_load_audio_stereo_to_mono(mock_sf):
    """load_audio should convert stereo to mono."""
    sr_file = 16000
    samples = np.zeros((16000, 2), dtype=np.float32)
    mock_sf.read.return_value = (samples, sr_file)

    result = load_audio("/fake/path.wav", sr=16000)
    assert result.ndim == 1


# ── segment_audio ───────────────────────────────────────────────────────────


def test_segment_audio_basic():
    sr = 16000
    audio = np.arange(sr * 3, dtype=np.float32)  # 3 seconds
    segments = [(0.0, 1.0), (1.5, 2.5)]

    result = segment_audio(audio, sr, segments)
    assert len(result) == 2
    assert len(result[0]) == sr  # 1 second
    assert len(result[1]) == sr  # 1 second


def test_segment_audio_clips_to_bounds():
    sr = 16000
    audio = np.arange(sr, dtype=np.float32)  # 1 second

    segments = [(-0.5, 0.5), (0.5, 2.0)]
    result = segment_audio(audio, sr, segments)

    assert len(result) == 2
    # First segment: max(0, -8000) to 8000 => 8000 samples
    assert len(result[0]) == int(0.5 * sr)
    # Second segment: 8000 to min(16000, 32000) => 8000 samples
    assert len(result[1]) == int(0.5 * sr)


def test_segment_audio_empty_list():
    audio = np.zeros(16000, dtype=np.float32)
    result = segment_audio(audio, 16000, [])
    assert result == []
