"""Tests for shared.models — model registry, status, download, delete."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from shared.models import (
    MODEL_REGISTRY,
    MODELS_DIR,
    delete_model,
    ensure_silero_vad,
    ensure_speaker_embed,
    ensure_speaker_embedding_model,
    get_model_status,
)


# ── MODEL_REGISTRY ──────────────────────────────────────────────────────────


def test_registry_has_all_keys():
    assert "whisper" in MODEL_REGISTRY
    assert "silero_vad" in MODEL_REGISTRY
    assert "speaker_embed" in MODEL_REGISTRY


def test_registry_entries_have_required_fields():
    required_fields = {"name", "filename", "url", "size_mb", "required", "description"}
    for key, info in MODEL_REGISTRY.items():
        missing = required_fields - set(info.keys())
        assert not missing, f"Model '{key}' missing fields: {missing}"


# ── get_model_status ────────────────────────────────────────────────────────


def test_get_model_status_returns_all_keys():
    """get_model_status should return an entry for every registered model."""
    with patch("shared.models.MODELS_DIR", Path("/nonexistent/path")):
        statuses = get_model_status()

    assert set(statuses.keys()) == set(MODEL_REGISTRY.keys())


def test_get_model_status_correct_fields():
    expected_fields = {
        "name", "description", "size_mb", "filename", "url",
        "required", "installed", "file_size_bytes",
    }
    with patch("shared.models.MODELS_DIR", Path("/nonexistent/path")):
        statuses = get_model_status()

    for key, status in statuses.items():
        missing = expected_fields - set(status.keys())
        assert not missing, f"Status for '{key}' missing fields: {missing}"


def test_get_model_status_not_installed():
    """Models should show as not installed when MODELS_DIR doesn't exist."""
    with patch("shared.models.MODELS_DIR", Path("/nonexistent/path")):
        statuses = get_model_status()

    for status in statuses.values():
        assert status["installed"] is False
        assert status["file_size_bytes"] == 0


# ── Model paths ─────────────────────────────────────────────────────────────


def test_model_paths_resolve_to_models_dir():
    for key, info in MODEL_REGISTRY.items():
        expected = MODELS_DIR / info["filename"]
        assert expected.parent == MODELS_DIR


# ── delete_model ────────────────────────────────────────────────────────────


def test_delete_model_removes_file(tmp_path):
    fake_file = tmp_path / "silero_vad.onnx"
    fake_file.write_bytes(b"fake model content")

    with patch("shared.models.MODELS_DIR", tmp_path):
        result = delete_model("silero_vad")

    assert result is True
    assert not fake_file.exists()


def test_delete_model_missing_file(tmp_path):
    with patch("shared.models.MODELS_DIR", tmp_path):
        result = delete_model("silero_vad")
    assert result is False


def test_delete_model_unknown_key():
    result = delete_model("nonexistent_model")
    assert result is False


# ── ensure_* functions ──────────────────────────────────────────────────────


def test_ensure_silero_vad_calls_download():
    with patch("shared.models.download_model_if_needed") as mock_dl:
        mock_dl.return_value = Path("/fake/silero_vad.onnx")
        result = ensure_silero_vad()

    mock_dl.assert_called_once()
    assert result == Path("/fake/silero_vad.onnx")


def test_ensure_speaker_embed_calls_download():
    with patch("shared.models.download_model_if_needed") as mock_dl:
        mock_dl.return_value = Path("/fake/speaker_embedding.onnx")
        result = ensure_speaker_embed()

    mock_dl.assert_called_once()
    assert result == Path("/fake/speaker_embedding.onnx")


def test_ensure_speaker_embedding_model_is_alias():
    """The old name should still work as a backward-compatible alias."""
    assert ensure_speaker_embedding_model is ensure_speaker_embed
