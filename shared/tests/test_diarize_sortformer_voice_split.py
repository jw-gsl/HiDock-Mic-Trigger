"""An explicit speaker count must be able to split labels UP, not only merge
them down. Regression cover for "Redetect at 4 on a 3-speaker result reports no
changes" — the count used to be applied as a downward cap only.
"""

import numpy as np
import pytest

import shared.audio_utils as audio_utils
from shared import diarize_sortformer
from shared.diarize_sortformer import (
    _SPLIT_MIN_SEPARATION,
    _SPLIT_MIN_TURNS_PER_VOICE,
    _split_labels_to_count,
    _two_voice_partition,
)


def _vec(*values):
    return np.asarray(values, dtype=np.float32)


A = _vec(1.0, 0.0, 0.0)
B = _vec(0.0, 1.0, 0.0)


def test_two_voice_partition_separates_two_distinct_voices():
    result = _two_voice_partition(
        [A, A + _vec(0, 0, 0.02), B, B + _vec(0, 0, 0.02)],
        min_per_group=2,
        min_separation=_SPLIT_MIN_SEPARATION,
    )
    assert result is not None
    groups, separation = result
    assert separation > _SPLIT_MIN_SEPARATION
    # Both A-ish turns land together, and both B-ish turns land together.
    assert groups[0] == groups[1]
    assert groups[2] == groups[3]
    assert groups[0] != groups[2]


def test_two_voice_partition_refuses_one_voice():
    values = [A + _vec(0, i * 0.005, 0) for i in range(6)]
    assert _two_voice_partition(
        values, min_per_group=2, min_separation=_SPLIT_MIN_SEPARATION,
    ) is None


def test_two_voice_partition_refuses_too_few_turns():
    assert _two_voice_partition(
        [A, B], min_per_group=_SPLIT_MIN_TURNS_PER_VOICE, min_separation=0.0,
    ) is None


def _audio_for(turns):
    """Fake audio whose first sample in each turn encodes that turn's index."""
    length = int(max(end for _, end, _ in turns) * 16000) + 16000
    audio = np.zeros(length, dtype=np.float32)
    for index, (start, _, _) in enumerate(turns):
        audio[int(start * 16000)] = float(index)
    return audio


@pytest.fixture
def canned_voices(monkeypatch):
    """Drive the splitter from per-turn embeddings keyed by turn index.

    The real path needs an ONNX speaker model; the split decision itself is
    pure vector maths, so stub the session and the extractor. `_audio_for`
    writes the turn index into the first sample of each slice, which is what
    the fake extractor reads back.
    """
    def install(by_index):
        monkeypatch.setattr(
            diarize_sortformer._CrossWindowLinker,
            "_session_or_none",
            lambda self: object(),
        )

        def fake_extract(sample, sr=16000, onnx_session=None):
            return by_index[int(sample[0])]

        monkeypatch.setattr(audio_utils, "extract_embedding", fake_extract)

    return install


def test_split_reaches_requested_count(canned_voices):
    turns = [
        (0.0, 5.0, "0"),
        (10.0, 15.0, "0"),
        (20.0, 25.0, "0"),
        (30.0, 35.0, "0"),
    ]
    # Turns 0/1 are one voice, turns 2/3 another — two people under one label.
    canned_voices({0: A, 1: A + _vec(0, 0, 0.02), 2: B, 3: B + _vec(0, 0, 0.02)})
    out, splits = _split_labels_to_count(_audio_for(turns), turns, 2)
    assert splits == 1
    labels = [lab for _, _, lab in out]
    assert len(set(labels)) == 2
    assert labels[0] == labels[1] == "0"      # first group keeps the old label
    assert labels[2] == labels[3] != "0"      # split-off voice is new


def test_split_declines_when_all_turns_are_one_voice(canned_voices):
    turns = [(0.0, 5.0, "0"), (10.0, 15.0, "0"), (20.0, 25.0, "0"), (30.0, 35.0, "0")]
    canned_voices({i: A + _vec(0, i * 0.004, 0) for i in range(4)})
    out, splits = _split_labels_to_count(_audio_for(turns), turns, 2)
    assert splits == 0
    assert out == turns


def test_split_is_a_noop_when_count_already_met(canned_voices):
    turns = [(0.0, 5.0, "0"), (10.0, 15.0, "1")]
    canned_voices({0: A, 1: B})
    out, splits = _split_labels_to_count(_audio_for(turns), turns, 2)
    assert splits == 0
    assert out == turns


def test_short_turns_are_never_split(canned_voices):
    # Every turn is below _SPLIT_MIN_TURN_SECONDS, so no usable embeddings.
    turns = [(0.0, 0.5, "0"), (1.0, 1.5, "0"), (2.0, 2.5, "0"), (3.0, 3.5, "0")]
    canned_voices({0: A, 1: A, 2: B, 3: B})
    out, splits = _split_labels_to_count(_audio_for(turns), turns, 2)
    assert splits == 0
    assert out == turns
