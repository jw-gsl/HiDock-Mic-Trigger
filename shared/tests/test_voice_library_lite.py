"""Tests for shared.voice_library_lite — speaker enrollment, identification, management."""
from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from shared.voice_library_lite import (
    _extract_audio_embedding,
    _audio_quality_metrics,
    cosine_similarity,
    delete_sample,
    delete_speaker,
    enroll_embedding,
    enroll_speaker,
    enroll_from_diarized,
    enroll_from_transcripts,
    identify_speaker,
    library_summary,
    load_backfill_aliases,
    list_samples,
    list_speakers,
    load_library,
    rename_speaker,
    reassess_library_quality,
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


def test_native_model_adapter_is_used_for_audio_embedding(tmp_path):
    """Non-ONNX embedders such as WavLM can share the archive pipeline."""
    import soundfile as sf

    path = tmp_path / "voice.wav"
    sf.write(path, np.zeros(16000, dtype=np.float32), 16000)

    class NativeEmbedder:
        def extract_embedding(self, audio, sr):
            assert len(audio) == 16000
            assert sr == 16000
            return np.asarray([0.1, 0.2, 0.3], dtype=np.float32)

    embedding, dimension, model = _extract_audio_embedding(
        path,
        session=NativeEmbedder(),
        neural_model_version="wavlm_base_plus_sv",
    )

    np.testing.assert_allclose(embedding, [0.1, 0.2, 0.3])
    assert dimension == 3
    assert model == "wavlm_base_plus_sv"


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

    entry = loaded["speakers"]["Alice"]
    assert entry["samples"][0]["embedding"] == [0.1, 0.2, 0.3]
    assert len(entry["samples"]) == 1
    assert "embedding" not in entry


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

    assert len(result["samples"]) == 1
    assert "embedding" not in result
    assert result["model"] == "mfcc-v1"


@patch("shared.voice_library_lite._get_speaker_embed_session", return_value=None)
@patch("shared.voice_library_lite.load_audio")
def test_enroll_speaker_second_time_increases_count(mock_load_audio, mock_session, tmp_path):
    """Re-enrolling should append a second exemplar."""
    mock_load_audio.return_value = np.random.randn(16000).astype(np.float32)

    fake_file = tmp_path / "embeddings.json"
    fake_dir = tmp_path

    with patch("shared.voice_library_lite.VOICE_LIBRARY_DIR", fake_dir), \
         patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_file), \
         patch(
             "shared.voice_library_lite.extract_embedding",
             side_effect=[
                 np.array([1.0, 0.0], dtype=np.float32),
                 np.array([0.0, 1.0], dtype=np.float32),
             ],
         ):
        enroll_speaker("Alice", "/fake/audio.wav")
        result = enroll_speaker("Alice", "/fake/audio2.wav")

    assert len(result["samples"]) == 2


def test_enroll_from_diarized_falls_back_to_longest_audio_segment(tmp_path):
    """Legacy sidecars without speaker_embeddings still teach the library."""
    import json

    audio_path = tmp_path / "meeting.mp3"
    audio_path.write_bytes(b"audio placeholder")
    sidecar = tmp_path / "meeting_diarized.json"
    sidecar.write_text(json.dumps({
        "audio_file": audio_path.name,
        "segments": [
            {"speaker_id": 1, "start": 0.0, "end": 1.0},
            {"speaker_id": 1, "start": 2.0, "end": 5.5},
            {"speaker_id": 0, "start": 6.0, "end": 9.0},
        ],
    }))

    with patch(
        "shared.voice_library_lite.enroll_speaker",
        return_value={"name": "Emma Thorn"},
    ) as enroll:
        result = enroll_from_diarized("Emma Thorn", sidecar, 1)

    assert result["name"] == "Emma Thorn"
    enroll.assert_called_once_with(
        "Emma Thorn", audio_path, segment_start=2.0, segment_end=5.5,
        provenance={
            "source_file": str(sidecar.resolve()),
                "speaker_id": "1",
                "audio_file": str(audio_path.resolve()),
                "turn_count": 2,
                "total_talk_seconds": 4.5,
                "segment_start": 2.0,
            "segment_end": 5.5,
        },
    )


def test_diarized_samples_are_one_per_meeting_and_keep_provenance(tmp_path):
    """Repeated confirmation replaces one meeting; other meetings add evidence."""
    fake_file = tmp_path / "embeddings.json"
    fake_dir = tmp_path
    with patch("shared.voice_library_lite.VOICE_LIBRARY_DIR", fake_dir), \
         patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_file):
        enroll_embedding(
            "Alice", [1.0, 0.0], embed_dim=2,
            provenance={"source_file": str(tmp_path / "one.json"), "speaker_id": "0"},
        )
        enroll_embedding(
            "Alice", [0.99, 0.01], embed_dim=2,
            provenance={"source_file": str(tmp_path / "one.json"), "speaker_id": "0"},
        )
        enroll_embedding(
            "Alice", [0.98, 0.02], embed_dim=2,
            provenance={"source_file": str(tmp_path / "two.json"), "speaker_id": "0"},
        )
        enroll_embedding(
            "Alice", [0.97, 0.03], embed_dim=2,
            provenance={"source_file": str(tmp_path / "two.json"), "speaker_id": "9"},
        )
        entry = load_library()["speakers"]["Alice"]
        listed = list_speakers()[0]

    assert len(entry["samples"]) == 2
    assert {s["source_file"] for s in entry["samples"]} == {
        str(tmp_path / "one.json"), str(tmp_path / "two.json")
    }
    assert listed["sample_count"] == 2
    assert listed["meeting_count"] == 2
    assert listed["profile_status"] == "thin"


@patch("shared.voice_library_lite._get_speaker_embed_session", return_value=None)
@patch("shared.voice_library_lite.load_audio")
@patch("shared.voice_library_lite.load_audio_segment")
@patch("shared.voice_library_lite.extract_embedding", return_value=np.array([1.0, 0.0], dtype=np.float32))
def test_historical_backfill_uses_bounded_audio_for_legacy_sidecars(
    mock_extract, mock_load_segment, mock_load_audio, mock_session, tmp_path
):
    """Legacy named meetings without stored centroids still become samples."""
    import json

    transcript_dir = tmp_path / "transcripts"
    transcript_dir.mkdir()
    audio = tmp_path / "meeting.mp3"
    audio.write_bytes(b"placeholder")
    sidecar = transcript_dir / "meeting_diarized.json"
    sidecar.write_text(json.dumps({
        "audio_file": str(audio),
        "speaker_names": {"0": "Alice"},
        "segments": [{"speaker_id": 0, "start": 10.0, "end": 100.0}],
    }))
    mock_load_audio.return_value = np.ones(16000, dtype=np.float32)
    mock_load_segment.return_value = np.ones(16000, dtype=np.float32)

    fake_file = tmp_path / "embeddings.json"
    with patch("shared.voice_library_lite.VOICE_LIBRARY_DIR", tmp_path), \
         patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_file):
        result = enroll_from_transcripts(
            transcript_dir,
            names=["Alice"],
            audio_fallback=True,
            include_legacy=True,
        )
        listed = list_samples("Alice")

    assert result["audio_enrolled"] == 1
    assert result["stored_embedding_enrolled"] == 0
    assert listed[0]["source_file"] == str(sidecar.resolve())
    assert listed[0]["segment_start"] == 10.0
    assert listed[0]["segment_end"] == 40.0


def test_historical_backfill_creates_missing_named_profile(tmp_path):
    """Backfill must discover named people absent from the current library."""
    import json

    transcript_dir = tmp_path / "transcripts"
    transcript_dir.mkdir()
    sidecar = transcript_dir / "meeting_diarized.json"
    sidecar.write_text(json.dumps({
        "speaker_names": {"0": "Andy Wheeler"},
        "speaker_embeddings": {"0": [1.0, 0.0]},
        "segments": [{"speaker_id": 0, "start": 0.0, "end": 10.0}],
    }))

    fake_file = tmp_path / "embeddings.json"
    with patch("shared.voice_library_lite.VOICE_LIBRARY_DIR", tmp_path), \
         patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_file):
        result = enroll_from_transcripts(transcript_dir, audio_fallback=False, include_legacy=True)
        listed = list_speakers(transcript_dir)

    assert result["enrolled"] == 1
    assert result["speakers"] == {"Andy Wheeler": 1}
    assert listed[0]["name"] == "Andy Wheeler"


def test_historical_backfill_enforces_trust_policy_and_keeps_label_source(tmp_path):
    """Only explicit user/legacy evidence and metadata-free legacy labels train."""
    import json

    transcript_dir = tmp_path / "transcripts"
    transcript_dir.mkdir()
    sidecar = transcript_dir / "meeting_diarized.json"
    sidecar.write_text(json.dumps({
        "speaker_names": {
            "0": "Alice", "1": "Unknown", "2": "Auto Confirmed",
            "3": "Incorrect Generic", "4": "Legacy Person", "5": "Imported Person",
        },
        "speaker_meta": {
            "0": {"source": "user", "verified": True},
            "1": {"source": "unknown", "verified": False},
            "2": {"source": "auto", "verified": True},
            "3": {"source": "generic", "verified": False},
            "4": {"source": "legacy", "verified": False},
        },
        "speaker_embeddings": {str(i): [1.0, float(i + 1)] for i in range(6)},
        "segments": [{"speaker_id": i, "start": i * 10, "end": i * 10 + 5} for i in range(6)],
    }))

    fake_file = tmp_path / "embeddings.json"
    with patch("shared.voice_library_lite.VOICE_LIBRARY_DIR", tmp_path), \
         patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_file):
        result = enroll_from_transcripts(transcript_dir, include_legacy=True)
        names = {item["name"] for item in list_speakers(transcript_dir)}
        legacy_sample = list_samples("Legacy Person")[0]

    assert result["enrolled"] == 3
    assert names == {"Alice", "Legacy Person", "Imported Person"}
    assert legacy_sample["label_source"] == "legacy"


def test_historical_backfill_dry_run_does_not_write_library(tmp_path):
    """Stage 0 reports candidates without creating or changing the library."""
    import json

    transcript_dir = tmp_path / "transcripts"
    transcript_dir.mkdir()
    (transcript_dir / "meeting_diarized.json").write_text(json.dumps({
        "speaker_names": {"0": "Alice"},
        "speaker_embeddings": {"0": [1.0, 0.0]},
        "segments": [{"speaker_id": 0, "start": 0, "end": 10}],
    }))
    fake_file = tmp_path / "embeddings.json"
    with patch("shared.voice_library_lite.VOICE_LIBRARY_DIR", tmp_path), \
         patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_file):
        result = enroll_from_transcripts(transcript_dir, dry_run=True, include_legacy=True)

    assert result["dry_run"] is True
    assert result["enrolled"] == 1
    assert result["candidates"][0]["label_source"] == "legacy_import"
    assert not fake_file.exists()


def test_historical_backfill_applies_explicit_aliases_without_losing_observed_name(tmp_path):
    """A pilot can target a canonical person while retaining legacy evidence."""
    import json

    transcript_dir = tmp_path / "transcripts"
    transcript_dir.mkdir()
    (transcript_dir / "meeting_diarized.json").write_text(json.dumps({
        "speaker_names": {"0": "James"},
        "speaker_embeddings": {"0": [1.0, 0.0]},
        "segments": [{"speaker_id": 0, "start": 0, "end": 10}],
    }))
    fake_file = tmp_path / "embeddings.json"
    with patch("shared.voice_library_lite.VOICE_LIBRARY_DIR", tmp_path), \
         patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_file):
        result = enroll_from_transcripts(
            transcript_dir,
            names=["James Whiting"],
            aliases={"James": "James Whiting"},
            dry_run=True,
            include_legacy=True,
        )

    assert result["speakers"] == {"James Whiting": 1}
    assert result["candidates"][0]["observed_name"] == "James"
    assert result["candidates"][0]["canonical_name"] == "James Whiting"
    assert result["candidates"][0]["alias_applied"] is True


def test_stored_embeddings_only_excludes_audio_only_candidates_before_selection(tmp_path):
    """A safe pilot must not let missing embeddings consume its sample cap."""
    import json

    transcript_dir = tmp_path / "transcripts"
    transcript_dir.mkdir()
    for index in range(3):
        (transcript_dir / f"audio-only-{index}_diarized.json").write_text(json.dumps({
            "speaker_names": {"0": "Alice"},
            "segments": [{"speaker_id": 0, "start": 0, "end": 10}],
        }))
    (transcript_dir / "stored_diarized.json").write_text(json.dumps({
        "speaker_names": {"0": "Alice"},
        "speaker_embeddings": {"0": [1.0, 0.0]},
        "segments": [{"speaker_id": 0, "start": 0, "end": 10}],
    }))
    fake_file = tmp_path / "embeddings.json"
    with patch("shared.voice_library_lite.VOICE_LIBRARY_DIR", tmp_path), \
         patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_file):
        result = enroll_from_transcripts(
            transcript_dir, dry_run=True, stored_embeddings_only=True, max_samples=1, include_legacy=True,
        )

    assert result["enrolled"] == 1
    assert result["skipped"] == 0


def test_load_backfill_aliases_rejects_invalid_json_shape(tmp_path):
    alias_file = tmp_path / "aliases.json"
    alias_file.write_text("[]")
    with pytest.raises(ValueError, match="alias file"):
        load_backfill_aliases(alias_file)


def test_dry_run_reports_but_excludes_derived_merged_sidecars(tmp_path):
    """Stage 0 makes derived evidence visible without admitting it."""
    import json

    transcript_dir = tmp_path / "transcripts"
    transcript_dir.mkdir()
    (transcript_dir / "Merged-meeting_diarized.json").write_text(json.dumps({
        "speaker_names": {"0": "Alice"},
        "speaker_embeddings": {"0": [1.0, 0.0]},
        "segments": [{"speaker_id": 0, "start": 0, "end": 10}],
    }))
    with patch("shared.voice_library_lite.EMBEDDINGS_FILE", tmp_path / "embeddings.json"):
        result = enroll_from_transcripts(transcript_dir, dry_run=True)

    assert result["enrolled"] == 0
    assert result["candidates"][0]["derived_merged"] is True
    assert result["candidates"][0]["reason"] == "derived_merged_excluded"


def test_historical_backfill_max_samples_caps_active_profile_not_archive(tmp_path):
    """The active cap never deletes provenance-backed archived evidence."""
    import json

    transcript_dir = tmp_path / "transcripts"
    transcript_dir.mkdir()
    sidecar = transcript_dir / "meeting_diarized.json"
    sidecar.write_text(json.dumps({
        "speaker_names": {"0": "Alice"},
        "speaker_embeddings": {"0": [1.0, 0.0]},
        "segments": [{"speaker_id": 0, "start": 0, "end": 10}],
    }))
    fake_file = tmp_path / "embeddings.json"
    with patch("shared.voice_library_lite.VOICE_LIBRARY_DIR", tmp_path), \
         patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_file):
        enroll_embedding("Alice", [0.0, 1.0], embed_dim=2,
                         provenance={"source_file": str(tmp_path / "old-1.json")})
        enroll_embedding("Alice", [0.0, -1.0], embed_dim=2,
                         provenance={"source_file": str(tmp_path / "old-2.json")})
        enroll_from_transcripts(transcript_dir, max_samples=1, include_legacy=True)
        samples = list_samples("Alice")

    assert len(samples) == 3
    assert sum(sample["active"] for sample in samples) == 1
    assert any(sample["source_file"] == str(sidecar.resolve()) for sample in samples)


def test_quality_gate_archives_short_confirmed_sample_and_excludes_it_from_matching(tmp_path):
    """Confirmation is retained, while structurally poor evidence stays inactive."""
    fake_file = tmp_path / "embeddings.json"
    with patch("shared.voice_library_lite.VOICE_LIBRARY_DIR", tmp_path), \
         patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_file):
        enroll_embedding(
            "Alice", [1.0, 0.0], embed_dim=2,
            provenance={
                "source_file": str(tmp_path / "good.json"),
                "segment_start": 0.0, "segment_end": 10.0,
                "total_talk_seconds": 30.0,
            },
        )
        enroll_embedding(
            "Alice", [0.0, 1.0], embed_dim=2,
            provenance={
                "source_file": str(tmp_path / "short.json"),
                "segment_start": 0.0, "segment_end": 1.0,
                "total_talk_seconds": 1.0,
            },
        )
        samples = list_samples("Alice")
        match, _ = identify_speaker([0.0, 1.0], threshold=0.7)

    short = next(sample for sample in samples if sample["source_file"].endswith("short.json"))
    assert short["quality_state"] == "archive"
    assert short["active"] is False
    assert match is None


def test_reassess_quality_preserves_archive_and_rebuilds_active_set(tmp_path):
    """Existing profiles can adopt the quality gate without data loss."""
    fake_file = tmp_path / "embeddings.json"
    with patch("shared.voice_library_lite.VOICE_LIBRARY_DIR", tmp_path), \
         patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_file):
        save_library({"speakers": {"Alice": {"samples": [
            {
                "embedding": [1.0, 0.0], "embedding_dim": 2,
                "source": "confirm", "source_file": "good.json",
                "segment_start": 0.0, "segment_end": 10.0,
                "total_talk_seconds": 20.0,
            },
            {
                "embedding": [0.0, 1.0], "embedding_dim": 2,
                "source": "confirm", "source_file": "short.json",
                "segment_start": 0.0, "segment_end": 1.0,
                "total_talk_seconds": 1.0,
            },
        ]}}})
        result = reassess_library_quality()
        samples = list_samples("Alice")

    assert result["sample_count"] == 2
    assert result["active_sample_count"] == 1
    assert result["archived_sample_count"] == 1
    assert len(samples) == 2


def test_acoustic_quality_distinguishes_clean_speech_like_audio_from_clipping():
    """The acoustic gate is explainable and rejects clearly clipped audio."""
    sr = 16000
    silence = np.zeros(int(0.25 * sr), dtype=np.float32)
    time = np.arange(int(0.75 * sr), dtype=np.float32) / sr
    speech_like = 0.12 * np.sin(2 * np.pi * 180 * time)

    clean = _audio_quality_metrics(np.concatenate([silence, speech_like]), sr)
    clipped = _audio_quality_metrics(np.ones(sr, dtype=np.float32), sr)

    assert clean["acoustic_quality"] >= 0.7
    assert clean["audio_speech_ratio"] > 0.6
    assert clipped["acoustic_quality"] < 0.2
    assert "clipping" in clipped["audio_reason"]


def test_reassess_quality_dry_run_reports_changes_without_saving(tmp_path):
    """The pre-rollout check must leave the on-disk library untouched."""
    import json

    fake_file = tmp_path / "embeddings.json"
    report_file = tmp_path / "reassessment.json"
    original = {"speakers": {"Alice": {"samples": [{
        "embedding": [1.0, 0.0], "embedding_dim": 2,
        "source": "confirm", "source_file": "good.json",
        "segment_start": 0.0, "segment_end": 10.0,
        "total_talk_seconds": 20.0,
    }]}}}
    with patch("shared.voice_library_lite.VOICE_LIBRARY_DIR", tmp_path), \
         patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_file):
        save_library(original)
        result = reassess_library_quality(dry_run=True, report_path=report_file)
        stored = json.loads(fake_file.read_text())

    report = json.loads(report_file.read_text())
    assert stored == original
    assert result["dry_run"] is True
    assert result["changes"]
    assert report["sample_count"] == 1
    assert report["dry_run"] is True


def test_historical_meeting_count_excludes_unverified_auto_and_merged(tmp_path):
    """The UI coverage count reflects trustworthy child meetings, not merges."""
    import json

    transcript_dir = tmp_path / "transcripts"
    transcript_dir.mkdir()
    for filename, metadata in [
        ("one_diarized.json", {}),
        ("two_diarized.json", {"0": {"source": "user", "verified": True}}),
        ("three_diarized.json", {"0": {"source": "auto", "verified": False}}),
        ("Merged-one-to-two_diarized.json", {}),
    ]:
        (transcript_dir / filename).write_text(json.dumps({
            "speaker_names": {"0": "Alice"},
            "speaker_meta": metadata,
        }))

    fake_file = tmp_path / "embeddings.json"
    with patch("shared.voice_library_lite.VOICE_LIBRARY_DIR", tmp_path), \
         patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_file):
        enroll_embedding("Alice", [1.0, 0.0], embed_dim=2)
        listed = list_speakers(transcript_dir)

    assert listed[0]["historical_meeting_count"] == 1
    assert listed[0]["meeting_count"] == 1


def test_library_summary_deduplicates_meetings_across_speakers(tmp_path):
    """The aggregate header counts a shared meeting once, not once per person."""
    import json

    transcript_dir = tmp_path / "transcripts"
    transcript_dir.mkdir()
    for filename, names in [
        ("one_diarized.json", {"0": "Alice", "1": "Bob"}),
        ("two_diarized.json", {"0": "Alice"}),
        ("Merged-one_diarized.json", {"0": "Alice", "1": "Bob"}),
    ]:
        (transcript_dir / filename).write_text(json.dumps({
            "speaker_names": names,
            "speaker_meta": {
                speaker_id: {"source": "user", "verified": True}
                for speaker_id in names
            },
        }))

    fake_file = tmp_path / "embeddings.json"
    with patch("shared.voice_library_lite.VOICE_LIBRARY_DIR", tmp_path), \
         patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_file):
        enroll_embedding("Alice", [1.0, 0.0], embed_dim=2)
        enroll_embedding("Bob", [0.0, 1.0], embed_dim=2)
        summary = library_summary(transcript_dir)

    assert summary == {
        "speaker_count": 2, "sample_count": 2,
        "active_sample_count": 2, "meeting_count": 2,
    }


def test_profile_status_reaches_usable_and_healthy_depth(tmp_path):
    fake_file = tmp_path / "embeddings.json"
    fake_dir = tmp_path
    with patch("shared.voice_library_lite.VOICE_LIBRARY_DIR", fake_dir), \
         patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_file):
        for index in range(12):
            enroll_embedding(
                "Alice", [1.0, float(index + 1)], embed_dim=2,
                provenance={
                    "source_file": str(tmp_path / f"meeting-{index}.json"),
                    "speaker_id": "0",
                },
            )
        listed = list_speakers()[0]

    assert listed["sample_count"] == 12
    assert listed["meeting_count"] == 12
    assert listed["profile_status"] == "healthy"


def test_sample_metadata_can_be_listed_and_deleted(tmp_path):
    fake_file = tmp_path / "embeddings.json"
    fake_dir = tmp_path
    source = tmp_path / "meeting.json"
    with patch("shared.voice_library_lite.VOICE_LIBRARY_DIR", fake_dir), \
         patch("shared.voice_library_lite.EMBEDDINGS_FILE", fake_file):
        enroll_embedding(
            "Alice", [1.0, 0.0], embed_dim=2,
            provenance={"source_file": str(source), "speaker_id": "0"},
        )
        samples = list_samples("Alice")
        assert samples[0]["source_file"] == str(source)
        assert "embedding" not in samples[0]
        assert delete_sample("Alice", samples[0]["id"]) is True
        assert list_samples("Alice") == []
        assert "Alice" not in load_library()["speakers"]


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
