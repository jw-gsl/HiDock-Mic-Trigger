"""Tests for shared.diarize_sortformer._stitch_windows — cross-window
speaker label remapping and overlap de-duplication.

Uses synthetic per-window turn data only; no NeMo/Sortformer required.
Window geometry mirrors production: 300 s windows, 30 s overlap, so
window 2 starts at 270 s and the overlap region is [270, 300] with
midpoint 285.
"""
from __future__ import annotations

from shared.diarize_sortformer import _stitch_windows


OVERLAP = 30.0
W2_OFFSET = 270.0  # second window start (300 - 30)
MID = W2_OFFSET + OVERLAP / 2.0  # 285.0


def _labels_by_time(stitched):
    """Map each turn's (start, end) to its global label."""
    return {(s, e): lab for s, e, lab in stitched}


# ── single / empty input ────────────────────────────────────────────────────


def test_empty_input_returns_empty():
    assert _stitch_windows([]) == []


def test_single_window_passthrough_with_consistent_relabel():
    turns = [
        (0.0, 10.0, "speaker_0"),
        (10.0, 20.0, "speaker_1"),
        (20.0, 30.0, "speaker_0"),
    ]
    out = _stitch_windows([(0.0, turns)], overlap_sec=OVERLAP)
    assert [(s, e) for s, e, _ in out] == [(0.0, 10.0), (10.0, 20.0), (20.0, 30.0)]
    # Same raw label -> same global label; different raw -> different global.
    assert out[0][2] == out[2][2]
    assert out[0][2] != out[1][2]


# ── label remapping across windows ──────────────────────────────────────────


def test_permuted_labels_are_remapped_to_previous_window():
    """Window 2's speaker IDs are permuted relative to window 1: its
    speaker_0 is window 1's speaker_1 and vice versa. The stitcher must
    map them back via the overlap region."""
    w1 = [
        (0.0, 150.0, "speaker_0"),    # Alice
        (150.0, 280.0, "speaker_1"),  # Bob — extends into overlap [270, 300]
        (280.0, 300.0, "speaker_0"),  # Alice in overlap
    ]
    # Window 2 sees the same overlap-region speech but permutes IDs:
    # Bob is now speaker_0 and Alice is speaker_1.
    w2 = [
        (270.0, 280.0, "speaker_0"),  # Bob (matches w1 150-280 speaker_1)
        (280.0, 300.0, "speaker_1"),  # Alice (matches w1 280-300 speaker_0)
        (300.0, 400.0, "speaker_0"),  # Bob keeps talking after the overlap
        (400.0, 450.0, "speaker_1"),  # Alice again
    ]
    out = _stitch_windows([(0.0, w1), (W2_OFFSET, w2)], overlap_sec=OVERLAP)
    by_time = _labels_by_time(out)

    alice = by_time[(0.0, 150.0)]
    bob = by_time[(150.0, 280.0)]  # ends before midpoint 285 — kept whole
    assert alice != bob
    # w1's Alice turn in the overlap is clipped at the midpoint.
    assert by_time[(280.0, MID)] == alice
    # w2's Alice turn keeps the post-midpoint half, remapped to Alice.
    assert by_time[(MID, 300.0)] == alice

    # Post-overlap turns from window 2 carry window 1's identities.
    assert by_time[(300.0, 400.0)] == bob
    assert by_time[(400.0, 450.0)] == alice

    # Only two global speakers in total.
    assert len({lab for _, _, lab in out}) == 2


def test_speaker_only_in_window_2_gets_fresh_label():
    """A speaker with no overlap-region evidence must NOT be collapsed
    into an existing speaker — they get a fresh global label."""
    w1 = [
        (0.0, 200.0, "speaker_0"),
        (200.0, 300.0, "speaker_1"),
    ]
    w2 = [
        (270.0, 300.0, "speaker_0"),  # continues w1's speaker_1
        (310.0, 350.0, "speaker_1"),  # brand-new voice, only after overlap
    ]
    out = _stitch_windows([(0.0, w1), (W2_OFFSET, w2)], overlap_sec=OVERLAP)
    by_time = _labels_by_time(out)

    w1_a = by_time[(0.0, 200.0)]
    w1_b = by_time[(200.0, MID)]  # clipped at midpoint
    newcomer = by_time[(310.0, 350.0)]

    # w2 speaker_0 mapped onto w1 speaker_1 via the overlap...
    assert by_time[(MID, 300.0)] == w1_b
    # ...while the newcomer is distinct from both existing speakers.
    assert newcomer not in {w1_a, w1_b}
    assert len({lab for _, _, lab in out}) == 3


# ── overlap de-duplication ──────────────────────────────────────────────────


def test_overlap_region_covered_exactly_once():
    """Both windows diarized [270, 300]; the stitcher must keep each side
    of the midpoint from exactly one window — no duplicated turns."""
    w1 = [
        (0.0, 270.0, "speaker_0"),
        (270.0, 300.0, "speaker_1"),  # whole overlap, per window 1
    ]
    w2 = [
        (270.0, 300.0, "speaker_0"),  # same speech, window 2's labelling
        (300.0, 360.0, "speaker_0"),
    ]
    out = _stitch_windows([(0.0, w1), (W2_OFFSET, w2)], overlap_sec=OVERLAP)

    # No two turns overlap in time (allow shared endpoints).
    ordered = sorted(out)
    for (s1, e1, _), (s2, _e2, _) in zip(ordered, ordered[1:]):
        assert s2 >= e1, f"turns overlap: ({s1},{e1}) and ({s2},..)"

    # The overlap region is fully covered, split at the midpoint.
    by_time = _labels_by_time(out)
    assert (270.0, MID) in by_time
    assert (MID, 300.0) in by_time
    # Both halves belong to the same (remapped) speaker.
    assert by_time[(270.0, MID)] == by_time[(MID, 300.0)]

    # Total speech duration equals the union, not the sum with overlap
    # double-counted: 0-300 (speaker A then B) + 300-360 = 360 s.
    total = sum(e - s for s, e, _ in out)
    assert abs(total - 360.0) < 1e-9


def test_turn_entirely_before_midpoint_in_window_2_is_dropped():
    """Window 2 turns that end before the midpoint belong to window 1's
    half of the overlap and must not be emitted."""
    w1 = [(0.0, 300.0, "speaker_0")]
    w2 = [
        (272.0, 280.0, "speaker_0"),  # before midpoint — window 1 owns this
        (290.0, 320.0, "speaker_0"),  # straddles nothing; after midpoint
    ]
    out = _stitch_windows([(0.0, w1), (W2_OFFSET, w2)], overlap_sec=OVERLAP)
    starts = {(s, e) for s, e, _ in out}
    assert (272.0, 280.0) not in starts
    assert (0.0, MID) in starts       # w1 clipped at midpoint
    assert (290.0, 320.0) in starts   # w2 turn after midpoint kept whole
    # Everything is one speaker.
    assert len({lab for _, _, lab in out}) == 1


def test_three_windows_chain_remapping():
    """Remapping must chain: window 3 maps onto window 2's already-
    remapped labels, which map onto window 1's."""
    w3_offset = 540.0  # 2 * (300 - 30)
    w1 = [(0.0, 300.0, "speaker_0")]                      # Alice
    w2 = [(270.0, 570.0, "speaker_1")]                    # Alice, permuted ID
    w3 = [(540.0, 700.0, "speaker_0")]                    # Alice again
    out = _stitch_windows(
        [(0.0, w1), (W2_OFFSET, w2), (w3_offset, w3)], overlap_sec=OVERLAP
    )
    labels = {lab for _, _, lab in out}
    assert len(labels) == 1, f"expected one chained speaker, got {labels}"
    # Continuous single-speaker coverage 0-700 with no gaps or overlaps.
    ordered = sorted(out)
    assert ordered[0][0] == 0.0
    assert ordered[-1][1] == 700.0
    for (s1, e1, _), (s2, _e2, _) in zip(ordered, ordered[1:]):
        assert abs(s2 - e1) < 1e-9
