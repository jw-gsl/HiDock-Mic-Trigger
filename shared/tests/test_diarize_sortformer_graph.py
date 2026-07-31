"""Direct cover for the voice-affinity graph trio.

`_speaker_affinity_graph`, `_graph_partition_score` and
`_auto_merge_labels_by_graph` decide how many speakers a meeting has whenever
the user gives no count and no calendar event is linked — the most common case.
They were previously exercised only indirectly through `diarize()`, so the
modularity objective, the size penalty and the near-tie rule had no direct
tests. These pin the contracts the caller in `diarize()` relies on.
"""

import math

import numpy as np

from shared.diarize_sortformer import (
    _GRAPH_AUTOMERGE_MIN_SCORE,
    _auto_merge_labels_by_graph,
    _graph_partition_score,
    _speaker_affinity_graph,
)


def _vec(*values):
    return np.asarray(values, dtype=np.float32)


def _two_communities():
    """Two tight voice pairs, mutually distant: the unambiguous case."""
    return {
        "A1": _vec(1.0, 0.0, 0.0),
        "A2": _vec(0.98, 0.02, 0.0),
        "B1": _vec(0.0, 1.0, 0.0),
        "B2": _vec(0.02, 0.98, 0.0),
    }


def _turns_for(labels, seconds=50):
    return [
        (index * seconds, (index + 1) * seconds, label)
        for index, label in enumerate(labels)
    ]


# --- _speaker_affinity_graph -------------------------------------------------

def test_affinity_graph_keeps_only_strong_edges():
    embeddings = _two_communities()
    edges = _speaker_affinity_graph(list(embeddings), embeddings)
    # Cross-community cosine is ~0.02, well under the 0.50 floor, so the graph
    # contains each pair once and nothing linking the two communities.
    assert set(edges) == {("A1", "A2"), ("B1", "B2")}
    assert all(weight > 0.9 for weight in edges.values())


def test_affinity_graph_is_empty_when_every_voice_is_distinct():
    embeddings = {"A": _vec(1, 0, 0), "B": _vec(0, 1, 0), "C": _vec(0, 0, 1)}
    assert _speaker_affinity_graph(list(embeddings), embeddings) == {}


def test_affinity_graph_skips_labels_without_embeddings():
    embeddings = {"A": _vec(1, 0), "B": _vec(0.99, 0.01), "C": None}
    edges = _speaker_affinity_graph(list(embeddings), embeddings)
    assert set(edges) == {("A", "B")}
    assert not any("C" in key for key in edges)


def test_affinity_graph_keys_are_order_independent():
    embeddings = {"B": _vec(1, 0), "A": _vec(0.99, 0.01)}
    edges = _speaker_affinity_graph(list(embeddings), embeddings)
    # Keys are sorted, so the same pair is never stored twice under two orders.
    assert list(edges) == [("A", "B")]


def test_affinity_graph_caps_neighbours_per_label():
    # Five near-identical voices: without the cap this would be a full clique
    # (10 edges). With neighbours=1 each label contributes at most one edge.
    embeddings = {
        f"L{index}": _vec(1.0, index * 0.001, 0.0) for index in range(5)
    }
    edges = _speaker_affinity_graph(list(embeddings), embeddings, neighbours=1)
    assert 0 < len(edges) <= 5


# --- _graph_partition_score --------------------------------------------------

def test_modularity_is_highest_for_the_true_partition():
    embeddings = _two_communities()
    labels = list(embeddings)
    edges = _speaker_affinity_graph(labels, embeddings)

    correct = {"A1": "A1", "A2": "A1", "B1": "B1", "B2": "B1"}
    everything_merged = {label: "A1" for label in labels}
    all_singletons = {label: label for label in labels}

    assert _graph_partition_score(labels, correct, edges) == 0.5
    assert _graph_partition_score(labels, everything_merged, edges) == 0.0
    assert _graph_partition_score(labels, all_singletons, edges) == -0.25


def test_modularity_signals_failure_without_edges():
    # -1.0 is the documented "no usable graph" sentinel, distinct from a merely
    # poor partition (which scores around or below zero).
    assert _graph_partition_score(["A", "B"], {"A": "A", "B": "B"}, {}) == -1.0


# --- _auto_merge_labels_by_graph --------------------------------------------

def test_automerge_recovers_the_true_speaker_count():
    embeddings = _two_communities()
    turns = _turns_for(list(embeddings))
    merged, count, score = _auto_merge_labels_by_graph(turns, embeddings)
    assert count == 2
    assert score >= _GRAPH_AUTOMERGE_MIN_SCORE
    assert len({label for _, _, label in merged}) == 2


def test_automerge_collapses_window_label_fragmentation():
    # One person split across three window labels, plus a genuine second
    # speaker — the failure mode cross-window stitching is meant to repair.
    embeddings = {
        "A": _vec(1.0, 0.0, 0.0),
        "B": _vec(0.99, 0.01, 0.0),
        "C": _vec(0.985, 0.02, 0.0),
        "D": _vec(0.0, 1.0, 0.0),
    }
    turns = _turns_for(list(embeddings))
    merged, count, _ = _auto_merge_labels_by_graph(turns, embeddings)
    assert count == 2
    surviving = {label for _, _, label in merged}
    assert len(surviving) == 2
    # The lone distinct voice must not be absorbed into the fragmented person.
    assert "D" in surviving


def test_automerge_declines_below_three_usable_labels():
    turns = _turns_for(["A", "B"])
    embeddings = {"A": _vec(1, 0), "B": _vec(0, 1)}
    assert _auto_merge_labels_by_graph(turns, embeddings) == (turns, None, None)


def test_automerge_declines_without_embeddings():
    turns = _turns_for(["A", "B", "C"])
    embeddings = {"A": None, "B": None, "C": None}
    assert _auto_merge_labels_by_graph(turns, embeddings) == (turns, None, None)


def test_automerge_declines_when_no_edge_clears_the_floor():
    turns = _turns_for(["A", "B", "C"])
    embeddings = {"A": _vec(1, 0, 0), "B": _vec(0, 1, 0), "C": _vec(0, 0, 1)}
    assert _auto_merge_labels_by_graph(turns, embeddings) == (turns, None, None)


def test_automerge_reports_a_sub_threshold_score_for_a_structureless_graph():
    """A chain of gradually drifting voices has no community structure.

    `diarize()` gates on `_GRAPH_AUTOMERGE_MIN_SCORE`, so the contract that
    matters here is that such a graph scores *below* the floor and therefore
    cannot silently collapse a meeting. Without this the caller's gate would be
    load-bearing but untested.
    """
    embeddings = {
        f"L{index}": _vec(math.cos(angle), math.sin(angle), 0.0)
        for index, angle in enumerate([0.0, 0.35, 0.70, 1.05])
    }
    turns = _turns_for(list(embeddings))
    _, count, score = _auto_merge_labels_by_graph(turns, embeddings)
    assert count is not None
    assert score < _GRAPH_AUTOMERGE_MIN_SCORE


def test_automerge_never_exceeds_the_usable_label_count():
    embeddings = _two_communities()
    turns = _turns_for(list(embeddings))
    _, count, _ = _auto_merge_labels_by_graph(turns, embeddings)
    assert 2 <= count <= len(embeddings)
