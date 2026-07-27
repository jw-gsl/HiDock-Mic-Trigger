import numpy as np

from shared.diarize_sortformer import _mixed_turn_graph_partition


def test_mixed_turn_graph_splits_two_alternating_voices():
    a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    # Small within-person variation, in alternating temporal order.
    values = [a, b, a + [0, 0, .03], b + [0, 0, .03], a, b, a, b]
    result = _mixed_turn_graph_partition([np.asarray(value, dtype=np.float32) for value in values])
    assert result is not None
    assert sum(result[i] != result[i - 1] for i in range(1, len(result))) >= 2


def test_mixed_turn_graph_refuses_single_voice_variation():
    base = np.array([1.0, 0.2, 0.0], dtype=np.float32)
    values = [base + np.array([0, (i % 3) * .01, 0]) for i in range(8)]
    assert _mixed_turn_graph_partition(values) is None
