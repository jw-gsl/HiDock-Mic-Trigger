import json
from pathlib import Path
from unittest.mock import patch

import numpy as np

from shared.voice_candidate_review import (
    activate_candidate,
    load_candidate_config,
    record_suggestion_outcome,
    suggest_for_transcript,
)


def _sample(vector, source):
    return {
        "embedding": vector,
        "embedding_dim": len(vector),
        "source_file": source,
        "active": True,
    }


def _fixture(tmp_path: Path, *, meetings: int = 3):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"candidate-model")
    library = {
        "speakers": {
            "James Whiting": {
                "samples": [_sample([1.0, 0.0], f"james-{i}") for i in range(meetings)],
            },
            "Chris Wildsmith": {
                "samples": [_sample([0.0, 1.0], f"chris-{i}") for i in range(3)],
            },
        }
    }
    library_path = tmp_path / "library.json"
    library_path.write_text(json.dumps(library))
    config = {
        "enabled": True,
        "model_key": "wespeaker_resnet293",
        "model_path": str(model),
        "library_path": str(library_path),
        "scorer": "top3_median",
        "threshold": 0.71,
        "min_margin": 0.21,
    }
    config_path = tmp_path / "active.json"
    config_path.write_text(json.dumps(config))
    audio = tmp_path / "meeting.wav"
    audio.touch()
    sidecar = tmp_path / "meeting_diarized.json"
    sidecar.write_text(json.dumps({
        "audio_file": str(audio),
        "speaker_names": {"0": "Speaker 1", "1": "Lucy McKay"},
        "speaker_meta": {
            "0": {"source": "generic", "verified": False},
            "1": {"source": "user", "verified": True},
        },
        "segments": [
            {"speaker_id": 0, "start": 0.0, "end": 10.0},
            {"speaker_id": 1, "start": 10.0, "end": 20.0},
        ],
    }))
    return config_path, sidecar


def test_missing_candidate_config_is_safe_and_review_only(tmp_path):
    result = load_candidate_config(tmp_path / "missing.json")
    assert result["available"] is False
    assert result["review_only"] is True
    assert result["reason"] == "candidate_not_configured"


@patch(
    "shared.voice_candidate_review._sha256",
    return_value="dbb1ccc7754caff552ebc46347a51aaee2669bb24efc740e665d1a1133d20e98",
)
def test_activate_candidate_writes_explicit_review_only_config(_mock_hash, tmp_path):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "voice-library.json").write_text(json.dumps({"speakers": {"James": {}}}))
    model = candidate / "voxceleb_resnet293_LM.onnx"
    model.write_bytes(b"model")
    config_path = tmp_path / "active.json"

    result = activate_candidate(candidate, config_path=config_path)

    saved = json.loads(config_path.read_text())
    assert result["speaker_count"] == 1
    assert saved["review_only"] is True
    assert saved["scorer"] == "top3_median"
    assert saved["threshold"] == 0.71
    assert saved["min_margin"] == 0.21


@patch("shared.voice_candidate_review._audio_quality_from_path")
@patch("shared.voice_candidate_review._extract_audio_embedding")
def test_suggest_uses_robust_meeting_score_and_skips_verified(mock_embed, mock_quality, tmp_path):
    config, sidecar = _fixture(tmp_path)
    mock_embed.return_value = (np.asarray([1.0, 0.0], dtype=np.float32), 2, "wespeaker_resnet293")
    mock_quality.return_value = {"acoustic_quality": 0.9, "audio_reason": "adequate acoustic signal"}

    result = suggest_for_transcript(sidecar, config_path=config, session=object())

    suggestion = result["suggestions"]["0"]
    assert result["review_only"] is True
    assert result["skipped_verified"] == ["1"]
    assert suggestion["proposed_name"] == "James Whiting"
    assert suggestion["scorer"] == "top3_median"
    assert suggestion["supporting_meetings"] == 3
    assert suggestion["decision"] == "strong_review"
    assert suggestion["reasons"] == []


@patch("shared.voice_candidate_review._audio_quality_from_path")
@patch("shared.voice_candidate_review._extract_audio_embedding")
def test_thin_profile_is_visible_but_never_strong(mock_embed, mock_quality, tmp_path):
    config, sidecar = _fixture(tmp_path, meetings=1)
    mock_embed.return_value = (np.asarray([1.0, 0.0], dtype=np.float32), 2, "wespeaker_resnet293")
    mock_quality.return_value = {"acoustic_quality": 0.9, "audio_reason": "adequate acoustic signal"}

    suggestion = suggest_for_transcript(sidecar, config_path=config, session=object())["suggestions"]["0"]

    assert suggestion["proposed_name"] == "James Whiting"
    assert suggestion["scorer"] == "max_thin_profile"
    assert suggestion["decision"] == "review"
    assert "thin_profile_manual_review_only" in suggestion["reasons"]


@patch("shared.voice_candidate_review._audio_quality_from_path")
@patch("shared.voice_candidate_review._extract_audio_embedding")
def test_single_ranked_identity_is_never_strong(mock_embed, mock_quality, tmp_path):
    config_path, sidecar = _fixture(tmp_path)
    config = json.loads(config_path.read_text())
    library_path = Path(config["library_path"])
    library = json.loads(library_path.read_text())
    del library["speakers"]["Chris Wildsmith"]
    library_path.write_text(json.dumps(library))
    mock_embed.return_value = (np.asarray([1.0, 0.0], dtype=np.float32), 2, "wespeaker_resnet293")
    mock_quality.return_value = {"acoustic_quality": 0.9, "audio_reason": "adequate acoustic signal"}

    suggestion = suggest_for_transcript(sidecar, config_path=config_path, session=object())["suggestions"]["0"]

    assert suggestion["proposed_name"] == "James Whiting"
    assert suggestion["runner_up"] is None
    assert suggestion["decision"] == "review"
    assert "single_ranked_identity" in suggestion["reasons"]


@patch("shared.voice_candidate_review._audio_quality_from_path")
@patch("shared.voice_candidate_review._extract_audio_embedding")
def test_confirmed_review_teaches_isolated_library_only_after_saved_verification(
    mock_embed, mock_quality, tmp_path
):
    config, sidecar = _fixture(tmp_path)
    data = json.loads(sidecar.read_text())
    data["speaker_names"]["0"] = "James Whiting"
    data["speaker_meta"]["0"] = {"source": "user", "verified": True}
    sidecar.write_text(json.dumps(data))
    mock_embed.return_value = (
        np.asarray([1.0, 0.0], dtype=np.float32), 2, "wespeaker_resnet293"
    )
    mock_quality.return_value = {
        "acoustic_quality": 0.9,
        "audio_reason": "adequate acoustic signal",
    }

    event = record_suggestion_outcome(
        sidecar,
        speaker_id=0,
        action="confirmed",
        proposed_name="James Whiting",
        final_name="James Whiting",
        config_path=config,
        session=object(),
    )

    library = json.loads((tmp_path / "library.json").read_text())
    samples = library["speakers"]["James Whiting"]["samples"]
    assert event["enrolled"] is True
    assert len(samples) == 4
    assert samples[-1]["source_file"] == str(sidecar.resolve())
    assert samples[-1]["label_source"] == "user"
    assert (tmp_path / "review-events.jsonl").exists()


def test_unknown_review_is_logged_without_enrollment(tmp_path):
    config, sidecar = _fixture(tmp_path)
    data = json.loads(sidecar.read_text())
    data["speaker_meta"]["0"] = {"source": "unknown", "verified": True}
    sidecar.write_text(json.dumps(data))
    before = (tmp_path / "library.json").read_text()

    event = record_suggestion_outcome(
        sidecar,
        speaker_id=0,
        action="unknown",
        proposed_name="James Whiting",
        config_path=config,
    )

    assert event["enrolled"] is False
    assert (tmp_path / "library.json").read_text() == before
    logged = json.loads((tmp_path / "review-events.jsonl").read_text())
    assert logged["action"] == "unknown"
    assert logged["final_name"] is None


def test_candidate_learning_rejects_unverified_sidecar(tmp_path):
    config, sidecar = _fixture(tmp_path)

    try:
        record_suggestion_outcome(
            sidecar,
            speaker_id=0,
            action="confirmed",
            proposed_name="James Whiting",
            final_name="James Whiting",
            config_path=config,
            session=object(),
        )
    except ValueError as exc:
        assert "verified saved speaker" in str(exc)
    else:
        raise AssertionError("unverified candidate learning should fail")
