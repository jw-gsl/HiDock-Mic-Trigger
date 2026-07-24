"""Tests for shared.diarize_sortformer._stitch_windows — cross-window
speaker label remapping and overlap de-duplication.

Uses synthetic per-window turn data only; no NeMo/Sortformer required.
Window geometry mirrors production: 300 s windows, 30 s overlap, so
window 2 starts at 270 s and the overlap region is [270, 300] with
midpoint 285.
"""
from __future__ import annotations

import numpy as np

from shared.diarize_sortformer import _prune_empty_speakers, _stitch_windows


OVERLAP = 30.0
W2_OFFSET = 270.0  # second window start (300 - 30)
MID = W2_OFFSET + OVERLAP / 2.0  # 285.0


def _labels_by_time(stitched):
    """Map each turn's (start, end) to its global label."""
    return {(s, e): lab for s, e, lab in stitched}


class _FakeLinker:
    """Canned per-(window, raw-label) embeddings for stitch tests."""

    def __init__(self, vectors):
        self.vectors = vectors

    def embed(self, turns, label, window_index=None):
        return self.vectors.get((window_index, label))


_ALICE = np.array([1.0, 0.0, 0.0])
_BOB = np.array([0.0, 1.0, 0.0])
_ALICE_LIKE = np.array([0.95, 0.05, 0.0])   # cosine ≈ 0.999 vs _ALICE
_BOB_LIKE = np.array([0.05, 0.95, 0.0])     # cosine ≈ 0.999 vs _BOB
_CAROL = np.array([0.0, 0.0, 1.0])          # orthogonal: cosine 0 vs both


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


# ── voice-embedding cross-window linking ──────────────────────────────────────


def _two_person_call_windows():
    """Rec07 geometry: Alice and Bob; Bob does all the talking through the
    handover region, Alice is silent there and resumes afterwards."""
    w1 = [
        (0.0, 200.0, "speaker_0"),    # Alice
        (200.0, 290.0, "speaker_1"),  # Bob — through most of the overlap
    ]
    w2 = [
        (270.0, 290.0, "speaker_0"),  # Bob again (overlap evidence vs w1 Bob)
        (290.0, 330.0, "speaker_0"),  # Bob keeps talking
        (340.0, 400.0, "speaker_1"),  # Alice resumes — silent through overlap
    ]
    return w1, w2


def test_no_linker_keeps_legacy_behaviour():
    """Without a linker the silent-through-overlap speaker is re-minted —
    the exact Rec07 failure this feature fixes."""
    w1, w2 = _two_person_call_windows()
    out = _stitch_windows([(0.0, w1), (W2_OFFSET, w2)], overlap_sec=OVERLAP)
    assert len({lab for _, _, lab in out}) == 3


def test_embedding_relinks_speaker_silent_through_overlap():
    """Alice has no overlap evidence in window 2; her voice embedding must
    map window 2's speaker_1 back onto her instead of minting a new person."""
    w1, w2 = _two_person_call_windows()
    linker = _FakeLinker({
        (0, "speaker_0"): _ALICE,
        (0, "speaker_1"): _BOB,
        (1, "speaker_0"): _BOB_LIKE,
        (1, "speaker_1"): _ALICE_LIKE,
    })
    out = _stitch_windows(
        [(0.0, w1), (W2_OFFSET, w2)], overlap_sec=OVERLAP, linker=linker
    )
    by_time = _labels_by_time(out)
    assert len({lab for _, _, lab in out}) == 2
    alice = by_time[(0.0, 200.0)]
    assert by_time[(340.0, 400.0)] == alice


def test_embedding_link_rejects_genuinely_new_voice():
    """A real newcomer must still get a fresh label — linking is
    conservative, never a licence to collapse distinct voices."""
    w1, w2 = _two_person_call_windows()
    linker = _FakeLinker({
        (0, "speaker_0"): _ALICE,
        (0, "speaker_1"): _BOB,
        (1, "speaker_0"): _BOB_LIKE,
        (1, "speaker_1"): _CAROL,   # orthogonal to both existing voices
    })
    out = _stitch_windows(
        [(0.0, w1), (W2_OFFSET, w2)], overlap_sec=OVERLAP, linker=linker
    )
    by_time = _labels_by_time(out)
    assert len({lab for _, _, lab in out}) == 3
    newcomer = by_time[(340.0, 400.0)]
    assert newcomer != by_time[(0.0, 200.0)]


def test_embedding_link_rejects_ambiguous_match():
    """Above the similarity threshold is not enough when the runner-up is
    just as close — an ambiguous voice stays a fresh label."""
    w1 = [(0.0, 100.0, "speaker_0"), (100.0, 200.0, "speaker_1")]  # Alice, Bob
    w2 = [(320.0, 360.0, "speaker_0")]  # ambiguous voice after a silent overlap
    halfway = np.array([0.71, 0.71, 0.0])  # ≈0.707 cosine vs BOTH Alice and Bob
    linker = _FakeLinker({
        (0, "speaker_0"): _ALICE,
        (0, "speaker_1"): _BOB,
        (1, "speaker_0"): halfway,
    })
    out = _stitch_windows(
        [(0.0, w1), (W2_OFFSET, w2)], overlap_sec=OVERLAP, linker=linker
    )
    by_time = _labels_by_time(out)
    # Neither existing voice is a defensible match: the ambiguous turn must
    # become a third label rather than collapsing into Alice or Bob.
    assert len({lab for _, _, lab in out}) == 3
    assert by_time[(320.0, 360.0)] not in {by_time[(0.0, 100.0)], by_time[(100.0, 200.0)]}


def test_embedding_link_single_candidate_needs_no_margin():
    """With only one registered voice there is no runner-up, so the
    threshold alone decides — a solo speaker re-links after a quiet window."""
    w1 = [(0.0, 250.0, "speaker_0")]
    w2 = [(320.0, 360.0, "speaker_1")]  # Alice again; overlap region is silent
    linker = _FakeLinker({
        (0, "speaker_0"): _ALICE,
        (1, "speaker_1"): np.array([0.8, 0.2, 0.0]),  # cosine ≈ 0.97 vs Alice
    })
    out = _stitch_windows(
        [(0.0, w1), (W2_OFFSET, w2)], overlap_sec=OVERLAP, linker=linker
    )
    labels = {lab for _, _, lab in out}
    assert len(labels) == 1


def test_overlap_evidence_wins_over_embedding():
    """Temporal overlap is direct evidence: a raw label matched in the
    handover region keeps that mapping even if its embedding points elsewhere."""
    w1, w2 = _two_person_call_windows()
    linker = _FakeLinker({
        (0, "speaker_0"): _ALICE,
        (0, "speaker_1"): _BOB,
        # Window 2's Bob-turn embedding is corrupted (looks like Alice) —
        # the overlap match must still win for that raw label.
        (1, "speaker_0"): _ALICE_LIKE,
        (1, "speaker_1"): _ALICE_LIKE,
    })
    out = _stitch_windows(
        [(0.0, w1), (W2_OFFSET, w2)], overlap_sec=OVERLAP, linker=linker
    )
    by_time = _labels_by_time(out)
    bob = by_time[(200.0, MID)]
    assert by_time[(290.0, 330.0)] == bob  # overlap mapping, not the embedding


# ── speaker-audio collection ──────────────────────────────────────────────────


def test_collect_speaker_audio_caps_long_turns():
    """A single long turn must not blow past max_seconds — oversized chunks
    break TitaNet ONNX inference (Rec07: 128–218 s chunks failed)."""
    from shared.diarize_sortformer import _collect_speaker_audio

    audio = np.ones(300 * 16000, dtype=np.float32)  # 5 minutes
    turns = [(0.0, 250.0, "x"), (260.0, 300.0, "x")]
    chunk = _collect_speaker_audio(audio, turns, "x", max_seconds=10.0)
    assert 0 < len(chunk) <= 10 * 16000


def test_collect_speaker_audio_skips_short_turns():
    from shared.diarize_sortformer import _collect_speaker_audio

    audio = np.ones(16000, dtype=np.float32)
    assert _collect_speaker_audio(audio, [(0.0, 0.5, "x")], "x").size == 0


# ── micro-label absorption ────────────────────────────────────────────────────


def test_absorb_micro_labels_reassigns_fragment_to_closest_voice():
    from shared.diarize_sortformer import _absorb_micro_labels

    turns = [(0.0, 100.0, "A"), (100.0, 200.0, "B"), (300.0, 304.0, "C")]
    talk = {"A": 100.0, "B": 100.0, "C": 4.0}
    embs = {"A": _ALICE, "B": _BOB, "C": _BOB_LIKE}  # fragment sounds like B

    out = _absorb_micro_labels(turns, embs, talk)

    assert [t[2] for t in out] == ["A", "B", "B"]


def test_absorb_micro_labels_keeps_distinct_fragment():
    from shared.diarize_sortformer import _absorb_micro_labels

    turns = [(0.0, 100.0, "A"), (100.0, 200.0, "B"), (300.0, 304.0, "C")]
    talk = {"A": 100.0, "B": 100.0, "C": 4.0}
    embs = {"A": _ALICE, "B": _BOB, "C": _CAROL}  # orthogonal: no match

    out = _absorb_micro_labels(turns, embs, talk)

    assert [t[2] for t in out] == ["A", "B", "C"]


def test_absorb_micro_labels_needs_a_full_size_candidate():
    from shared.diarize_sortformer import _absorb_micro_labels

    turns = [(0.0, 2.0, "A"), (2.0, 4.0, "B")]
    talk = {"A": 2.0, "B": 2.0}
    embs = {"A": _ALICE, "B": _ALICE_LIKE}

    out = _absorb_micro_labels(turns, embs, talk)

    assert [t[2] for t in out] == ["A", "B"]


def test_absorb_micro_labels_ignores_unembedded_fragment():
    from shared.diarize_sortformer import _absorb_micro_labels

    turns = [(0.0, 100.0, "A"), (300.0, 303.0, "C")]
    talk = {"A": 100.0, "C": 3.0}
    embs = {"A": _ALICE, "C": None}

    out = _absorb_micro_labels(turns, embs, talk)

    assert [t[2] for t in out] == ["A", "C"]


# ── empty-speaker pruning ─────────────────────────────────────────────────────


def test_prune_empty_speakers_drops_segmentless_entries():
    names = {"0": "Speaker 1", "1": "Speaker 2", "2": "Speaker 9"}
    meta = {k: {"source": "generic", "verified": False} for k in names}
    embs = {"1": [0.1, 0.2], "2": [0.3, 0.4]}
    segments = [{"speaker_id": 0}, {"speaker_id": "1"}]

    names2, meta2, embs2 = _prune_empty_speakers(names, meta, embs, segments)

    assert set(names2) == {"0", "1"}
    assert set(meta2) == {"0", "1"}
    assert set(embs2) == {"1"}
    # Unrelated entries are never renumbered or renamed.
    assert names2["1"] == "Speaker 2"
