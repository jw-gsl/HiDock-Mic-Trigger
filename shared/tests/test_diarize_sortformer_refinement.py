"""Cover the two-sided partition search and the turn-reassignment loop.

Both are gated off by default and enabled only on measured evidence, but their
mechanics still need pinning: the partition search is the only automatic path in
the pipeline that can *increase* the speaker count, and the reassignment loop is
the only one that can move a turn after Sortformer has spoken. A silent failure
in either would look like "no change" rather than an error.
"""

import numpy as np
import pytest

import shared.audio_utils as audio_utils
from shared import diarize_sortformer
from shared.diarize_sortformer import (
    _reassign_turns_to_pooled_voices,
    _refine_partition_by_turn_graph,
    _turn_affinity_graph,
)


def _vec(*values):
    return np.asarray(values, dtype=np.float32)


A = _vec(1.0, 0.0, 0.0)
B = _vec(0.0, 1.0, 0.0)


def _audio_for(turns):
    """Fake audio encoding each turn's index in its first sample."""
    length = int(max(end for _, end, _ in turns) * 16000) + 16000
    audio = np.zeros(length, dtype=np.float32)
    for index, (start, _, _) in enumerate(turns):
        audio[int(start * 16000)] = float(index)
    return audio


@pytest.fixture
def canned_voices(monkeypatch):
    """Serve a per-turn embedding keyed by turn index, with a stub ONNX session."""
    def install(by_index):
        monkeypatch.setattr(
            diarize_sortformer._CrossWindowLinker,
            "_session_or_none",
            lambda self: object(),
        )
        monkeypatch.setattr(
            audio_utils,
            "extract_embedding",
            lambda sample, sr=16000, onnx_session=None: by_index[int(sample[0])],
        )
    return install


@pytest.fixture
def no_model(monkeypatch):
    monkeypatch.setattr(
        diarize_sortformer._CrossWindowLinker,
        "_session_or_none",
        lambda self: None,
    )


# --- turn affinity graph -----------------------------------------------------

def test_turn_graph_links_only_similar_turns():
    embeddings = {"0": A, "1": A + _vec(0, 0, 0.01), "2": B}
    edges = _turn_affinity_graph(embeddings)
    assert ("0", "1") in edges
    # Cross-voice cosine is ~0, far below the 0.50 floor.
    assert not any("2" in key for key in edges)


# --- two-sided partition -----------------------------------------------------

def _two_people_one_label():
    """Six turns under one label, actually two alternating voices."""
    turns = [(i * 50.0, i * 50.0 + 40.0, "Speaker 1") for i in range(6)]
    voices = {0: A, 1: B, 2: A, 3: B, 4: A, 5: B}
    return turns, voices


def test_partition_search_can_increase_the_speaker_count(canned_voices):
    """The measured failure was a -1.25 speaker bias: nothing automatic could
    ever split. This is the path that fixes it."""
    turns, voices = _two_people_one_label()
    canned_voices(voices)
    refined, summary, _voices = _refine_partition_by_turn_graph(_audio_for(turns), turns)
    assert summary is not None
    labels = [label for _, _, label in refined]
    assert len(set(labels)) == 2
    # The two voices are separated, not interleaved arbitrarily.
    assert labels[0] == labels[2] == labels[4]
    assert labels[1] == labels[3] == labels[5]
    assert labels[0] != labels[1]


def test_partition_search_merges_duplicate_window_labels(canned_voices):
    # Same voice under three labels, plus a genuine second speaker.
    turns = [
        (0.0, 60.0, "Speaker 1"),
        (60.0, 120.0, "Speaker 2"),
        (120.0, 180.0, "Speaker 3"),
        (180.0, 240.0, "Speaker 4"),
    ]
    canned_voices({0: A, 1: A + _vec(0, 0, 0.01), 2: A + _vec(0, 0.01, 0), 3: B})
    refined, summary, _voices = _refine_partition_by_turn_graph(_audio_for(turns), turns)
    assert summary is not None
    labels = [label for _, _, label in refined]
    assert len(set(labels)) == 2
    assert labels[0] == labels[1] == labels[2] != labels[3]


def test_partition_search_leaves_a_correct_partition_alone(canned_voices):
    turns = [(0.0, 60.0, "Speaker 1"), (60.0, 120.0, "Speaker 2")]
    canned_voices({0: A, 1: B})
    refined, summary, _voices = _refine_partition_by_turn_graph(_audio_for(turns), turns)
    assert summary is None
    assert refined == turns


def test_partition_search_refuses_to_split_a_thin_new_voice(canned_voices):
    """A new speaker must own real speech. Two four-second scraps of a second
    voice are not a participant, and inventing one is worse than missing one."""
    turns = [
        (0.0, 60.0, "Speaker 1"), (60.0, 120.0, "Speaker 1"),
        (120.0, 180.0, "Speaker 1"), (180.0, 240.0, "Speaker 1"),
        (240.0, 244.0, "Speaker 1"), (250.0, 254.0, "Speaker 1"),
    ]
    canned_voices({0: A, 1: A, 2: A, 3: A, 4: B, 5: B})
    refined, _summary, _voices = _refine_partition_by_turn_graph(_audio_for(turns), turns)
    assert len({label for _, _, label in refined}) == 1


def test_partition_search_declines_without_an_embedding_model(no_model):
    turns = [(0.0, 60.0, "Speaker 1"), (60.0, 120.0, "Speaker 2")]
    refined, summary, _voices = _refine_partition_by_turn_graph(_audio_for(turns), turns)
    assert summary is None
    assert refined == turns


def test_partition_search_respects_the_max_speaker_ceiling(canned_voices):
    turns = [(i * 50.0, i * 50.0 + 40.0, "Speaker 1") for i in range(8)]
    canned_voices({i: (A if i % 2 == 0 else B) for i in range(8)})
    refined, _s, _voices = _refine_partition_by_turn_graph(
        _audio_for(turns), turns, max_speakers=2,
    )
    assert len({label for _, _, label in refined}) <= 2


# --- turn reassignment -------------------------------------------------------

def test_reassignment_moves_a_misplaced_turn(canned_voices):
    # Turn 3 sounds like voice A but sits with the B cluster.
    turns = [
        (0.0, 40.0, "Speaker 1"), (40.0, 80.0, "Speaker 1"),
        (80.0, 120.0, "Speaker 2"), (120.0, 160.0, "Speaker 2"),
    ]
    canned_voices({0: A, 1: A, 2: B, 3: A})
    refined, moved = _reassign_turns_to_pooled_voices(_audio_for(turns), turns)
    assert moved >= 1
    assert refined[3][2] == "Speaker 1"


def test_reassignment_leaves_a_settled_partition_alone(canned_voices):
    turns = [
        (0.0, 40.0, "Speaker 1"), (40.0, 80.0, "Speaker 1"),
        (80.0, 120.0, "Speaker 2"), (120.0, 160.0, "Speaker 2"),
    ]
    canned_voices({0: A, 1: A, 2: B, 3: B})
    refined, moved = _reassign_turns_to_pooled_voices(_audio_for(turns), turns)
    assert moved == 0
    assert refined == turns


def test_reassignment_never_moves_a_confirmed_turn(canned_voices):
    """The Rec79 Part 2 guarantee at turn granularity: a human's confirmed
    speech must not be relabelled by any automatic refinement."""
    turns = [
        (0.0, 40.0, "Speaker 1"), (40.0, 80.0, "Speaker 1"),
        (80.0, 120.0, "Speaker 2"), (120.0, 160.0, "Speaker 2"),
    ]
    canned_voices({0: A, 1: A, 2: B, 3: A})
    refined, moved = _reassign_turns_to_pooled_voices(
        _audio_for(turns), turns, pinned_intervals=[(120.0, 160.0)],
    )
    assert moved == 0
    assert refined[3][2] == "Speaker 2"


def test_reassignment_declines_with_a_single_speaker(canned_voices):
    turns = [(0.0, 40.0, "Speaker 1"), (40.0, 80.0, "Speaker 1")]
    canned_voices({0: A, 1: B})
    refined, moved = _reassign_turns_to_pooled_voices(_audio_for(turns), turns)
    assert moved == 0
    assert refined == turns


def test_reassignment_declines_without_an_embedding_model(no_model):
    turns = [(0.0, 40.0, "Speaker 1"), (40.0, 80.0, "Speaker 2")]
    refined, moved = _reassign_turns_to_pooled_voices(_audio_for(turns), turns)
    assert moved == 0
    assert refined == turns


def test_reassignment_terminates_on_an_ambiguous_voice(canned_voices):
    """Hysteresis, not luck, is what stops the loop. A turn sitting exactly
    between two clusters must not ping-pong until the iteration cap."""
    middle = _vec(0.7071, 0.7071, 0.0)
    turns = [
        (0.0, 40.0, "Speaker 1"), (40.0, 80.0, "Speaker 1"),
        (80.0, 120.0, "Speaker 2"), (120.0, 160.0, "Speaker 2"),
        (160.0, 200.0, "Speaker 1"),
    ]
    canned_voices({0: A, 1: A, 2: B, 3: B, 4: middle})
    _refined, moved = _reassign_turns_to_pooled_voices(_audio_for(turns), turns)
    # At most one move: the margin rule blocks a return trip.
    assert moved <= 1


def test_partition_search_returns_a_pooled_voice_per_group(canned_voices):
    """A split invents a label that never went through name resolution.

    Without a pooled embedding for it the caller has no way to name or persist
    the new speaker — and `display_names` raised KeyError on it, which the
    quality harness caught as three failed meetings.
    """
    turns, voices = _two_people_one_label()
    canned_voices(voices)
    refined, summary, pooled = _refine_partition_by_turn_graph(_audio_for(turns), turns)
    assert summary is not None
    labels = {label for _, _, label in refined}
    # Every resulting group, including the invented one, has a unit-length voice.
    assert labels <= set(pooled)
    for vector in pooled.values():
        assert abs(float(np.linalg.norm(vector)) - 1.0) < 1e-5


def test_split_off_voice_does_not_inherit_the_other_half_name():
    """The name belongs to the half the reviewer confirmed, not to both."""
    from shared.diarize_sortformer import _repool_merged_speakers

    before = [(0.0, 60.0, "Speaker 1"), (60.0, 120.0, "Speaker 1")]
    after = [(0.0, 60.0, "Speaker 1"), (60.0, 120.0, "Speaker 1__graph_voice_2")]
    info = {
        "Speaker 1": {"name": "Jeevan", "source": "auto", "confidence": 0.8,
                      "embedding": [1.0, 0.0]},
        "Speaker 1__graph_voice_2": {"name": "Speaker 1__graph_voice_2",
                                     "source": "generic", "confidence": None,
                                     "embedding": [0.0, 1.0]},
    }
    out = _repool_merged_speakers(
        info, before, after,
        ["Speaker 1", "Speaker 1__graph_voice_2"],
        also_resolve={"Speaker 1__graph_voice_2"},
    )
    assert out["Speaker 1__graph_voice_2"]["name"] != "Jeevan"
    # And it keeps its own pooled voice, not the other half's.
    assert out["Speaker 1__graph_voice_2"]["embedding"] == [0.0, 1.0]
