"""Tests for shared.models — model registry, status, download, delete."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


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
    # All models carry name/description/stage/backend_key. Install
    # flavours:
    #   - built-in: no file, no pip package
    #   - pip-installable: pip_package + pip_import_name
    #     (NeMo Sortformer adds nemo_model_name for HF cache lookup)
    #   - file-downloadable: filename + url + size_mb
    common = {"name", "description", "stage", "backend_key"}
    for key, info in MODEL_REGISTRY.items():
        missing = common - set(info.keys())
        assert not missing, f"Model '{key}' missing fields: {missing}"
        if info.get("built_in"):
            continue
        if info.get("pip_package"):
            assert info.get("pip_import_name"), (
                f"{key}: pip_package set but pip_import_name missing (needed for install detection)"
            )
            if info.get("nemo_model"):
                assert "nemo_model_name" in info, f"{key}: nemo_model set but nemo_model_name missing"
            continue
        # Downloadable file — needs filename, url, size_mb
        for f in ("filename", "url", "size_mb"):
            assert f in info, f"Downloadable model '{key}' missing '{f}'"


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
    """File-backed models should show as not installed when MODELS_DIR
    doesn't exist. Built-in code models stay installed=True (no file
    needed); NeMo-managed status depends on `nemo-toolkit` being
    importable at test time and isn't covered by the MODELS_DIR mock."""
    with patch("shared.models.MODELS_DIR", Path("/nonexistent/path")):
        statuses = get_model_status()

    from shared.models import MODEL_REGISTRY
    for key, status in statuses.items():
        info = MODEL_REGISTRY[key]
        if info.get("built_in"):
            assert status["installed"] is True, f"{key}: built-in should always be installed"
            assert status["file_size_bytes"] == 0
        elif info.get("pip_package") or info.get("managed_externally"):
            # Pip-installable entries (NeMo Sortformer, TEN VAD) and
            # managed-externally entries (Parakeet via parakeet-mlx): skip —
            # installed state depends on whether the managing module is
            # importable in the test venv, which is orthogonal to
            # MODELS_DIR (the test is mocking MODELS_DIR only).
            continue
        else:
            assert status["installed"] is False, f"{key}: should be uninstalled"
            assert status["file_size_bytes"] == 0


# ── Model paths ─────────────────────────────────────────────────────────────


def test_model_paths_resolve_to_models_dir():
    for key, info in MODEL_REGISTRY.items():
        # Skip anything that doesn't land in MODELS_DIR:
        #   - managed_externally: parakeet-mlx via HF hub cache
        #   - built_in: code-only, no file
        #   - pip_package: installed by pip into the venv
        if (info.get("managed_externally")
                or info.get("built_in")
                or info.get("pip_package")):
            continue
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


# ── download_model_if_needed integrity ──────────────────────────────────────


class _FakeResponse:
    """Minimal urlopen response: declares Content-Length but delivers less."""

    def __init__(self, body: bytes, content_length: int):
        self._body = body
        self.headers = {"Content-Length": str(content_length)}
        self._served = False

    def read(self, _size):
        if self._served:
            return b""
        self._served = True
        return self._body


def test_incomplete_download_raises_and_cleans_up(tmp_path):
    """A short body (dropped connection) must raise — not rename a partial
    file into place where it would pass the size>1000 'installed' check."""
    import pytest
    from shared.models import download_model_if_needed

    body = b"x" * 50
    with patch("shared.models.MODELS_DIR", tmp_path), \
         patch("urllib.request.urlopen", return_value=_FakeResponse(body, 100)):
        with pytest.raises(OSError, match="Incomplete download"):
            download_model_if_needed("https://example.com/model.onnx", "model.onnx")

    assert not (tmp_path / "model.onnx").exists()
    assert not (tmp_path / "model.downloading").exists()


def test_complete_download_succeeds(tmp_path):
    from shared.models import download_model_if_needed

    body = b"x" * 100
    with patch("shared.models.MODELS_DIR", tmp_path), \
         patch("urllib.request.urlopen", return_value=_FakeResponse(body, 100)):
        dest = download_model_if_needed("https://example.com/model.onnx", "model.onnx")

    assert dest == tmp_path / "model.onnx"
    assert dest.read_bytes() == body


# ── managed_externally entries (Parakeet) ───────────────────────────────────


def test_managed_externally_status_uses_module_check():
    """Parakeet's 'installed' state must come from the parakeet_mlx module,
    not from a file in MODELS_DIR (its url is an HTML page, not a model)."""
    with patch("shared.models.MODELS_DIR", Path("/nonexistent/path")), \
         patch("shared.models._python_module_available", return_value=True) as mock_avail:
        statuses = get_model_status()

    assert statuses["parakeet"]["installed"] is True
    assert statuses["parakeet"]["url"] is None
    mock_avail.assert_any_call("parakeet_mlx")


def test_managed_externally_download_refused(capsys):
    """`models.py download parakeet` must refuse instead of writing the
    HuggingFace HTML page into MODELS_DIR."""
    import json as _json
    import sys as _sys

    import pytest
    from shared.models import _cli

    with patch.object(_sys, "argv", ["models.py", "download", "parakeet"]):
        with pytest.raises(SystemExit) as exc:
            _cli()

    assert exc.value.code == 1
    out = _json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["ok"] is False
    assert out.get("managed_externally") is True
    assert "managed externally" in out["error"]
