"""Reclaiming a confirmed speaker's misattributed speech.

`recluster_with_anchors` only reassigns *unnamed* segments, so once a reviewer has
confirmed every speaker it correctly reports "nothing to do" while a quiet
participant's speech still sits under someone else's name. Verified on Rec79
Part 2: `reassigned: 0, kept: 286`. The failure there is mislabelled, not
unlabelled, speech — which is what this module addresses.
"""

import numpy as np
import pytest

import shared.audio_utils as audio_utils
from shared.anchor_sweep import (
    MIN_MARGIN,
    MIN_SIMILARITY,
    apply_sweep,
    confirmed_segments,
    plan_sweep,
)


def _vec(*values):
    return np.asarray(values, dtype=np.float32)


JEEVAN = _vec(0.0, 1.0, 0.0)
CHRIS = _vec(1.0, 0.0, 0.0)


def _sidecar():
    """Three confirmed speakers; one segment is really Jeevan but labelled Chris."""
    return {
        "audio_file": "/nope.mp3",
        "speaker_names": {"0": "Chris Wildsmith", "1": "James Whiting", "2": "Jeevan Dulai"},
        "speaker_meta": {
            "0": {"source": "user", "verified": True},
            "1": {"source": "user", "verified": True},
            "2": {"source": "user", "verified": True},
        },
        "segments": [
            {"start": 0.0, "end": 30.0, "speaker_id": 0, "speaker": "Chris Wildsmith", "text": "chris"},
            {"start": 30.0, "end": 60.0, "speaker_id": 1, "speaker": "James Whiting", "text": "james"},
            {"start": 60.0, "end": 90.0, "speaker_id": 2, "speaker": "Jeevan Dulai", "text": "jeevan anchor"},
            # Mislabelled: sounds like Jeevan, filed under Chris.
            {"start": 90.0, "end": 120.0, "speaker_id": 0, "speaker": "Chris Wildsmith", "text": "really jeevan"},
        ],
    }


@pytest.fixture
def canned_audio(monkeypatch):
    """Embed by segment start time, so tests control who each segment sounds like."""
    def install(by_start):
        def fake(sample, sr=16000, onnx_session=None):
            return by_start[round(float(sample[0]), 3)]
        monkeypatch.setattr(audio_utils, "extract_embedding", fake)
        # Audio long enough for every span, marked with its start second.
        audio = np.zeros(16000 * 200, dtype=np.float32)
        for start in by_start:
            audio[int(start * 16000)] = start
        return audio
    return install


def test_only_confirmed_speakers_become_anchors():
    data = _sidecar()
    data["speaker_meta"]["1"]["verified"] = False
    grouped = confirmed_segments(data)
    assert set(grouped) == {"Chris Wildsmith", "Jeevan Dulai"}
    assert "James Whiting" not in grouped


def test_sweep_reclaims_a_misattributed_segment(canned_audio):
    data = _sidecar()
    audio = canned_audio({0.0: CHRIS, 30.0: _vec(0, 0, 1.0), 60.0: JEEVAN, 90.0: JEEVAN})
    plan = plan_sweep(data, audio, object(), targets=["Jeevan Dulai"])
    assert [(m["start"], m["from"], m["to"]) for m in plan["moves"]] == [
        (90.0, "Chris Wildsmith", "Jeevan Dulai"),
    ]
    assert plan["moves"][0]["score"] > MIN_SIMILARITY


def test_sweep_never_moves_a_segment_that_matches_its_current_speaker(canned_audio):
    data = _sidecar()
    # The suspect segment genuinely is Chris.
    audio = canned_audio({0.0: CHRIS, 30.0: _vec(0, 0, 1.0), 60.0: JEEVAN, 90.0: CHRIS})
    plan = plan_sweep(data, audio, object(), targets=["Jeevan Dulai"])
    assert plan["moves"] == []


def test_sweep_requires_a_margin_over_the_current_speaker(canned_audio):
    data = _sidecar()
    # Ambiguous: sits between the two anchors, so neither wins clearly. Moving on
    # a coin-flip is how confirmed work gets quietly reassigned.
    between = _vec(0.7071, 0.7071, 0.0)
    audio = canned_audio({0.0: CHRIS, 30.0: _vec(0, 0, 1.0), 60.0: JEEVAN, 90.0: between})
    plan = plan_sweep(data, audio, object(), targets=["Jeevan Dulai"])
    assert plan["moves"] == []


def test_sweep_leaves_the_anchors_themselves_alone(canned_audio):
    data = _sidecar()
    audio = canned_audio({0.0: CHRIS, 30.0: _vec(0, 0, 1.0), 60.0: JEEVAN, 90.0: JEEVAN})
    plan = plan_sweep(data, audio, object(), targets=["Jeevan Dulai"])
    # Segment 2 is Jeevan's own anchor; it must never appear as a move.
    assert all(move["index"] != 2 for move in plan["moves"])


def test_targets_limit_which_speaker_can_reclaim(canned_audio):
    data = _sidecar()
    audio = canned_audio({0.0: CHRIS, 30.0: _vec(0, 0, 1.0), 60.0: JEEVAN, 90.0: JEEVAN})
    # Sweeping for James only: the Jeevan-sounding segment must stay put.
    plan = plan_sweep(data, audio, object(), targets=["James Whiting"])
    assert plan["moves"] == []


def test_sweep_reports_anchor_evidence_per_speaker(canned_audio):
    data = _sidecar()
    audio = canned_audio({0.0: CHRIS, 30.0: _vec(0, 0, 1.0), 60.0: JEEVAN, 90.0: JEEVAN})
    plan = plan_sweep(data, audio, object())
    # How much confirmed audio each centroid rests on — a one-segment anchor
    # deserves less trust than a ten-minute one, and the caller should see that.
    assert plan["anchors"]["Jeevan Dulai"] == 30.0
    assert plan["anchors"]["Chris Wildsmith"] == 60.0


def test_sweep_without_confirmed_speakers_reports_why(canned_audio):
    data = _sidecar()
    for entry in data["speaker_meta"].values():
        entry["verified"] = False
    audio = canned_audio({0.0: CHRIS, 30.0: CHRIS, 60.0: JEEVAN, 90.0: JEEVAN})
    plan = plan_sweep(data, audio, object())
    assert plan["moves"] == []
    assert "no confirmed speakers" in plan["error"]


def test_apply_records_the_previous_owner(canned_audio):
    data = _sidecar()
    audio = canned_audio({0.0: CHRIS, 30.0: _vec(0, 0, 1.0), 60.0: JEEVAN, 90.0: JEEVAN})
    plan = plan_sweep(data, audio, object(), targets=["Jeevan Dulai"])
    assert apply_sweep(data, plan) == 1
    moved = data["segments"][3]
    assert moved["speaker"] == "Jeevan Dulai"
    assert moved["speaker_id"] == 2
    # Provenance, so a reviewer can see what it used to be.
    assert moved["source_speaker_id"] == "0"


def test_apply_refuses_to_invent_a_speaker(canned_audio):
    data = _sidecar()
    plan = {"moves": [{"index": 0, "to": "Nobody At All", "from": "Chris Wildsmith"}]}
    assert apply_sweep(data, plan) == 0
    assert data["segments"][0]["speaker"] == "Chris Wildsmith"


def test_thresholds_are_clear_of_the_measured_between_person_range():
    # Rec79 Part 2: confirmed speaker self-match 0.888, cross-speaker max 0.335.
    # The floor must sit above the latter with room to spare, or the sweep starts
    # claiming neighbouring voices.
    assert MIN_SIMILARITY > 0.335 + 0.15
    assert MIN_MARGIN >= 0.10
