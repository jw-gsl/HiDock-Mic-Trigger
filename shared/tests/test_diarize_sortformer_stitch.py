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


def test_absorb_micro_labels_absorbs_unembedded_fragment_by_proximity():
    # A fragment too short to embed has no voice evidence, so it can never clear
    # the similarity threshold — leaving it alone made it a permanent phantom
    # participant. Fall back to the nearest speaker who actually spoke.
    from shared.diarize_sortformer import _absorb_micro_labels

    turns = [(0.0, 100.0, "A"), (300.0, 303.0, "C")]
    talk = {"A": 100.0, "C": 3.0}
    embs = {"A": _ALICE, "C": None}

    out = _absorb_micro_labels(turns, embs, talk)

    assert [t[2] for t in out] == ["A", "A"]


def test_absorb_micro_labels_picks_the_temporally_closest_full_voice():
    from shared.diarize_sortformer import _absorb_micro_labels

    turns = [(0.0, 100.0, "A"), (500.0, 600.0, "B"), (598.0, 598.4, "C")]
    talk = {"A": 100.0, "B": 100.0, "C": 0.4}
    embs = {"A": _ALICE, "B": _BOB, "C": None}

    out = _absorb_micro_labels(turns, embs, talk)

    # Inside B's turn, and nowhere near A's.
    assert [t[2] for t in out] == ["A", "B", "B"]


def test_absorb_micro_labels_leaves_unembedded_fragment_when_nothing_is_full_size():
    from shared.diarize_sortformer import _absorb_micro_labels

    turns = [(0.0, 3.0, "A"), (10.0, 10.4, "C")]
    talk = {"A": 3.0, "C": 0.4}
    embs = {"A": None, "C": None}

    out = _absorb_micro_labels(turns, embs, talk)

    assert [t[2] for t in out] == ["A", "C"]


# ── merge-to-count (explicit user-requested speaker count) ────────────────────


def test_merge_labels_to_count_merges_most_similar_first():
    from shared.diarize_sortformer import _merge_labels_to_count

    turns = [
        (0.0, 10.0, "A"), (10.0, 20.0, "B"), (20.0, 30.0, "C"), (30.0, 40.0, "D"),
    ]
    embs = {
        "A": _ALICE, "B": _BOB,
        "C": _ALICE_LIKE,   # closest to A
        "D": _BOB_LIKE,     # closest to B
    }
    out = _merge_labels_to_count(turns, embs, 2)
    assert [t[2] for t in out] == ["A", "B", "A", "B"]


def test_merge_labels_to_count_noop_when_at_or_below_count():
    from shared.diarize_sortformer import _merge_labels_to_count

    turns = [(0.0, 5.0, "A"), (5.0, 10.0, "B")]
    embs = {"A": _ALICE, "B": _BOB}
    assert _merge_labels_to_count(turns, embs, 2) == turns
    assert _merge_labels_to_count(turns, embs, 5) == turns


def test_merge_labels_to_count_chains_by_best_pair():
    from shared.diarize_sortformer import _merge_labels_to_count

    turns = [(0.0, 5.0, "A"), (5.0, 10.0, "B"), (10.0, 15.0, "C")]
    embs = {"A": _ALICE, "B": _ALICE_LIKE, "C": _BOB}  # A~B (0.999) before B~C
    out = _merge_labels_to_count(turns, embs, 2)
    assert [t[2] for t in out] == ["A", "A", "C"]


def test_merge_labels_to_count_never_merges_unembedded():
    from shared.diarize_sortformer import _merge_labels_to_count

    turns = [(0.0, 5.0, "A"), (5.0, 10.0, "B"), (10.0, 15.0, "C")]
    embs = {"A": _ALICE, "B": _BOB}  # C has no embedding
    out = _merge_labels_to_count(turns, embs, 1)
    assert [t[2] for t in out] == ["A", "A", "C"]


def test_merge_labels_to_count_ignores_unembedded_labels_in_the_budget():
    """The Rec82 defect: unmergeable scraps must not spend a speaker slot.

    Six labels and a requested count of 2, where two labels are sub-second
    scraps with no embedding. They can never be merged, so counting them
    against the budget drove the four embeddable labels into one cluster — the
    final merge joining the two real, orthogonal voices. James (2920 s) and
    Jenny (666 s) became a single speaker holding the whole hour.
    """
    from shared.diarize_sortformer import _merge_labels_to_count

    turns = [
        (0.0, 2920.0, "Speaker 1"),      # James
        (2920.0, 3586.0, "Speaker 2"),   # Jenny — orthogonal to James
        (3586.0, 3586.4, "Speaker 3"),   # 0.4s scrap, no embedding
        (3586.4, 3589.8, "Speaker 4"),   # 3.4s scrap, no embedding
        (3589.8, 3594.6, "Speaker 5"),   # 4.8s, sounds like James
        (3594.6, 3597.8, "Speaker 6"),   # 3.2s, sounds like James
    ]
    embs = {
        "Speaker 1": _ALICE,
        "Speaker 2": _BOB,
        "Speaker 3": None,
        "Speaker 4": None,
        "Speaker 5": _ALICE_LIKE,
        "Speaker 6": _ALICE_LIKE,
    }

    out = _merge_labels_to_count(turns, embs, 2)

    labels = [t[2] for t in out]
    # Both real voices survive; the Alice-like fragments join James.
    assert labels == [
        "Speaker 1", "Speaker 2", "Speaker 3", "Speaker 4", "Speaker 1", "Speaker 1",
    ]
    assert "Speaker 2" in labels, "Jenny must not be absorbed into James"


def test_merge_labels_to_count_noop_when_embeddable_labels_are_within_count():
    # Five labels but only two can merge, so there is nothing to do — the old
    # code would still have fused those two to chase the count.
    from shared.diarize_sortformer import _merge_labels_to_count

    turns = [
        (0.0, 100.0, "A"), (100.0, 200.0, "B"),
        (200.0, 200.4, "C"), (200.4, 200.8, "D"), (200.8, 201.2, "E"),
    ]
    embs = {"A": _ALICE, "B": _BOB, "C": None, "D": None, "E": None}
    assert _merge_labels_to_count(turns, embs, 2) == turns


def test_voice_affinity_graph_merges_window_fragments_without_manual_count():
    from shared.diarize_sortformer import _auto_merge_labels_by_graph

    # Two people, each appearing under two independent window labels.
    turns = [
        (0.0, 20.0, "A"), (20.0, 40.0, "B"),
        (300.0, 320.0, "C"), (320.0, 340.0, "D"),
    ]
    embs = {
        "A": _ALICE, "B": _BOB,
        "C": _ALICE_LIKE, "D": _BOB_LIKE,
    }

    merged, count, score = _auto_merge_labels_by_graph(turns, embs)

    assert count == 2
    assert score is not None
    assert [label for _, _, label in merged] == ["A", "B", "A", "B"]


# ── calendar-derived expected speaker count ───────────────────────────────────


def test_expected_speakers_from_calendar_counts_non_declined():
    from types import SimpleNamespace as NS
    from shared.diarize_sortformer import _expected_speakers_from_calendar

    attendees = tuple(
        NS(name=n, declined=d)
        for n, d in [("A", False), ("B", False), ("C", False), ("D", True)]
    )
    context = NS(
        ambiguous=False,
        selected_event_id="evt-1",
        events=(NS(id="evt-1", attendees=attendees),),
    )
    assert _expected_speakers_from_calendar(context) == 3


def test_expected_speakers_from_calendar_rejects_ambiguous_and_missing():
    from types import SimpleNamespace as NS
    from shared.diarize_sortformer import _expected_speakers_from_calendar

    event = NS(id="evt-1", attendees=(NS(name="A", declined=False), NS(name="B", declined=False)))
    assert _expected_speakers_from_calendar(NS(ambiguous=True, selected_event_id="evt-1", events=(event,))) is None
    assert _expected_speakers_from_calendar(NS(ambiguous=False, selected_event_id=None, events=(event,))) is None
    assert _expected_speakers_from_calendar(NS(ambiguous=False, selected_event_id="other", events=(event,))) is None
    assert _expected_speakers_from_calendar(None) is None


def test_expected_speakers_from_calendar_ignores_singletons():
    from types import SimpleNamespace as NS
    from shared.diarize_sortformer import _expected_speakers_from_calendar

    event = NS(id="evt-1", attendees=(NS(name="A", declined=False),))
    context = NS(ambiguous=False, selected_event_id="evt-1", events=(event,))
    assert _expected_speakers_from_calendar(context) is None


def test_stitch_respects_linker_threshold_override():
    """Per-model linkers carry their own calibrated threshold (WeSpeaker
    0.65 vs TitaNet 0.70); a stricter threshold must reject the link."""
    w1, w2 = _two_person_call_windows()
    linker = _FakeLinker({
        (0, "speaker_0"): _ALICE,
        (0, "speaker_1"): _BOB,
        (1, "speaker_0"): _BOB_LIKE,
        (1, "speaker_1"): _ALICE_LIKE,  # cosine ≈ 0.9986 vs Alice
    })
    linker.threshold = 0.9995  # stricter than the pair's similarity
    out = _stitch_windows(
        [(0.0, w1), (W2_OFFSET, w2)], overlap_sec=OVERLAP, linker=linker
    )
    assert len({lab for _, _, lab in out}) == 3  # link rejected -> fresh label


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
