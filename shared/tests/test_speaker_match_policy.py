"""Tests for the voice-library match decision.

The numbers in the Rec01 cases are the real scores measured against the promoted
ReDimNet2 library on 2026-07-31, when both speakers came back unnamed.
"""
import pytest

from shared.speaker_match_policy import (
    alias_compatible,
    decide,
    required_margin,
)

THRESHOLD = 0.5
MARGIN = 0.23


def ranked(*pairs):
    return [{"name": n, "score": s} for n, s in pairs]


# --- alias_compatible ------------------------------------------------------

@pytest.mark.parametrize("a,b,expected", [
    ("Adam", "Adam Gardner", True),
    ("Adam Gardner", "Adam", True),
    ("Andy", "Andy Wheeler", True),
    ("adam", "Adam Gardner", True),          # case-insensitive
    ("Adam Gardner", "Adam Prior", False),   # two surnames = two people
    ("Adam", "Alan", False),
    ("Adam", "Adam", True),
    ("Adam Gardner", "Adam Gardner", True),
    ("", "Adam", False),
])
def test_alias_compatible(a, b, expected):
    assert alias_compatible(a, b) is expected


# --- required_margin -------------------------------------------------------

def test_margin_is_full_at_the_threshold():
    assert required_margin(THRESHOLD, THRESHOLD, MARGIN) == pytest.approx(MARGIN)


def test_margin_relaxes_as_confidence_rises():
    assert required_margin(0.847, THRESHOLD, MARGIN) < MARGIN
    assert required_margin(0.95, THRESHOLD, MARGIN) < required_margin(0.7, THRESHOLD, MARGIN)


def test_margin_never_collapses_to_nothing():
    # Even a near-perfect score must lead by something.
    assert required_margin(1.0, THRESHOLD, MARGIN) == pytest.approx(MARGIN * 0.25)


def test_margin_handles_a_degenerate_threshold():
    assert required_margin(1.0, 1.0, MARGIN) == pytest.approx(MARGIN)


# --- the Rec01 regressions -------------------------------------------------

def test_rec01_speaker0_names_james_whiting():
    """0.847 vs 0.765 was refused by the old flat 0.23 margin."""
    decision = decide(
        ranked(("James Whiting", 0.8473), ("Nick Clark", 0.7652),
               ("Chris Laidler", 0.5922), ("Luke Warren", 0.5464)),
        THRESHOLD, MARGIN,
    )
    assert decision.name == "James Whiting"
    assert decision.confidence == pytest.approx(0.8473)


def test_rec01_speaker1_collapses_the_adam_alias():
    """"Adam" and "Adam Gardner" are one person; they must not cancel out."""
    decision = decide(
        ranked(("Adam", 0.8215), ("Adam Gardner", 0.8074), ("Chris Laidler", 0.568),
               ("Adam Prior", 0.5275), ("Chris Wildsmith", 0.5148)),
        THRESHOLD, MARGIN,
    )
    assert decision.name == "Adam Gardner"          # full name beats the bare one
    assert set(decision.alias_group) == {"Adam", "Adam Gardner"}
    assert "Adam Prior" not in decision.alias_group  # different person


# --- the guards that must still bite ---------------------------------------

def test_a_far_below_namesake_is_never_promoted_to():
    """A shared first name alone must not hand the match to the wrong surname.

    "Adam Prior" sits 0.29 below "Adam" here, so the voices disagree: he is a
    different person who happens to share a first name, and naming him would be
    a fabricated identity. The bare "Adam" is reported instead — vague, but true.
    """
    decision = decide(
        ranked(("Adam", 0.82), ("Adam Prior", 0.53)),
        THRESHOLD, MARGIN,
    )
    assert decision.name == "Adam"
    assert decision.alias_group == ["Adam"]


def test_a_namesake_the_voices_cannot_separate_counts_against_the_margin():
    """Two people, one first name, indistinguishable scores -> name nobody."""
    decision = decide(
        ranked(("Adam Gardner", 0.62), ("Adam Prior", 0.60)),
        THRESHOLD, MARGIN,
    )
    assert not decision.matched
    assert decision.near_miss["runner_up"] == "Adam Prior"


def test_one_first_name_two_surnames_is_ambiguous_not_confident():
    """"Andy" tied with both Andy Wheeler and Andy Wilmott names nobody."""
    decision = decide(
        ranked(("Andy", 0.80), ("Andy Wheeler", 0.79), ("Andy Wilmott", 0.78)),
        THRESHOLD, MARGIN,
    )
    assert not decision.matched
    assert decision.near_miss["why"] == "two people share this first name"


def test_a_genuine_coin_flip_is_still_refused():
    decision = decide(
        ranked(("Alice", 0.72), ("Bob", 0.71)),
        THRESHOLD, MARGIN,
    )
    assert not decision.matched
    assert decision.near_miss["name"] == "Alice"
    assert decision.near_miss["why"] == "too close to the runner-up"


def test_below_threshold_is_refused_but_still_reported():
    decision = decide(ranked(("Alice", 0.30), ("Bob", 0.10)), THRESHOLD, MARGIN)
    assert not decision.matched
    assert decision.near_miss["why"] == "below threshold"


def test_an_uncontested_match_is_accepted():
    decision = decide(ranked(("Alice", 0.90)), THRESHOLD, MARGIN)
    assert decision.name == "Alice"


def test_empty_library_matches_nothing():
    decision = decide([], THRESHOLD, MARGIN)
    assert not decision.matched
    assert decision.near_miss is None
