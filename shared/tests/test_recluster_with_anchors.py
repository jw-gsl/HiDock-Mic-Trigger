import json

import numpy as np

import shared.recluster_with_anchors as recluster


def test_named_anchor_policy_trusts_legacy_but_not_unverified_auto():
    names = {"0": "Alice", "1": "Bob", "2": "Speaker 3"}

    assert recluster._is_named_anchor(
        0, names, {"0": {"source": "user", "verified": True}}
    )
    assert recluster._is_named_anchor(
        1, names, {"1": {"source": "legacy_import", "verified": False}}
    )
    assert not recluster._is_named_anchor(
        1, names, {"1": {"source": "auto", "verified": False}}
    )
    assert recluster._is_named_anchor(0, {"0": "Alice"}, {})
    assert not recluster._is_named_anchor(2, names, {})


def test_reassignment_keeps_named_anchor_fixed_and_updates_generic_turn(tmp_path, monkeypatch):
    sidecar = tmp_path / "meeting_diarized.json"
    sidecar.write_text(json.dumps({
        "audio_file": str(tmp_path / "meeting.mp3"),
        "segments": [
            {"start": 0.0, "end": 3.0, "speaker_id": 0, "speaker": "Alice", "text": "anchor"},
            {"start": 3.0, "end": 6.0, "speaker_id": 1, "speaker": "Speaker 2", "text": "generic"},
        ],
        "speaker_names": {"0": "Alice", "1": "Speaker 2"},
        "speaker_meta": {
            "0": {"source": "user", "verified": True},
            "1": {"source": "generic", "verified": False},
        },
    }), encoding="utf-8")
    (tmp_path / "meeting.mp3").write_bytes(b"audio")

    monkeypatch.setattr(recluster, "load_audio", lambda *_args, **_kwargs: np.zeros(16_000))
    monkeypatch.setattr(
        "shared.diarize_lite._load_speaker_embed_model",
        lambda: object(),
    )
    monkeypatch.setattr(
        recluster,
        "_embed_segment",
        lambda *_args, **_kwargs: np.array([1.0, 0.0]),
    )

    result = recluster.recluster_with_anchors(sidecar)
    updated = json.loads(sidecar.read_text(encoding="utf-8"))

    assert result["reassigned"] == 1
    assert [segment["speaker_id"] for segment in updated["segments"]] == [0]
    assert "anchor" in updated["segments"][0]["text"]
    assert "generic" in updated["segments"][0]["text"]
    assert updated["speaker_names"] == {"0": "Alice"}


def test_reassignment_merge_preserves_words_and_caps_blocks():
    words = [
        {"word": "one", "start": 0.0, "end": 1.0},
        {"word": "two", "start": 1.0, "end": 2.0},
    ]
    merged = recluster._merge_consecutive_same_speaker([
        {"start": 0.0, "end": 2.0, "speaker_id": 0, "text": "one two", "words": words},
        {"start": 2.1, "end": 4.0, "speaker_id": 0, "text": "three"},
    ])
    assert len(merged) == 1
    assert merged[0]["words"] == words
    assert merged[0]["text"] == "one two three"

    capped = recluster._merge_consecutive_same_speaker([
        {"start": 0.0, "end": 20.0, "speaker_id": 0, "text": "first"},
        {"start": 20.1, "end": 40.0, "speaker_id": 0, "text": "second"},
    ])
    assert len(capped) == 2
