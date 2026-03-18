"""Tests for pure functions in voice_library.py — cosine_similarity and embeddings I/O."""
import json

import numpy as np


class TestCosineSimilarity:
    def test_identical_vectors(self):
        from voice_library import cosine_similarity
        a = [1.0, 0.0, 0.0]
        assert cosine_similarity(a, a) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        from voice_library import cosine_similarity
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert cosine_similarity(a, b) == pytest.approx(0.0)

    def test_zero_vector(self):
        from voice_library import cosine_similarity
        assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


class TestEmbeddingsIO:
    def test_load_missing_returns_empty(self, monkeypatch):
        from voice_library import _load_embeddings, EMBEDDINGS_PATH
        # EMBEDDINGS_PATH won't exist in tmp_path → should return {}
        assert _load_embeddings() == {}

    def test_save_and_load_round_trip(self, monkeypatch, tmp_path):
        import voice_library as vl

        emb_path = tmp_path / "embeddings.json"
        monkeypatch.setattr(vl, "EMBEDDINGS_PATH", emb_path)

        data = {"Alice": {"embedding": [0.1, 0.2, 0.3], "sample_count": 1}}
        vl._save_embeddings(data)
        loaded = vl._load_embeddings()
        assert loaded["Alice"]["embedding"] == [0.1, 0.2, 0.3]

    def test_corrupt_embeddings_returns_empty(self, monkeypatch, tmp_path):
        import voice_library as vl

        emb_path = tmp_path / "embeddings.json"
        emb_path.write_text("NOT JSON!!")
        monkeypatch.setattr(vl, "EMBEDDINGS_PATH", emb_path)
        assert vl._load_embeddings() == {}


import pytest
