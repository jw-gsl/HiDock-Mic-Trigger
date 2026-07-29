"""The naming library must not inherit the live library's bad labels.

Speaker names are confirmed into the live library, but automatic naming ranks
against the promoted candidate library, and only the review path writes there —
so people enrolled live can be silently unnameable. Backfilling closes that gap,
but the live library is not clean: Jenny Helland's only live sample came from a
113 s block it had itself flagged "very long segment may be mixed", and the clip
inside it was James. Enrolling it taught the naming library that James is Jenny,
at 0.857 against his real voice and 0.024 against hers.
"""
import json

import numpy as np
import pytest

from shared.voice_library_sync import (
    _ALIAS_SIMILARITY,
    _clip_window,
    _collision,
    _has_active_sample,
    diff_libraries,
)


ALICE = [1.0, 0.0, 0.0]
BOB = [0.0, 1.0, 0.0]
# Cosine 0.864 to ALICE — the real figure the contaminated "Jenny" clip scored
# against James Whiting: unmistakably a different recording of a similar voice,
# not the same exemplar under another name.
ALICE_ISH = [0.864, 0.5035, 0.0]


def _entry(*samples):
    return {"samples": list(samples)}


def _sample(embedding, *, active=True, source="m.json"):
    return {"embedding": list(embedding), "active": active, "source_file": source}


# --- clip windowing ----------------------------------------------------------


def test_clip_window_keeps_a_short_span_whole():
    assert _clip_window(10.0, 25.0) == (10.0, 25.0)


def test_clip_window_centres_a_long_span():
    # A long attributed stretch usually starts on a handover, so the middle is
    # the safer sample of a single voice.
    start, end = _clip_window(0.0, 100.0, max_seconds=30.0)
    assert (start, end) == (35.0, 65.0)


def test_clip_window_rejects_unusable_timing():
    assert _clip_window(5.0, 5.0) is None
    assert _clip_window(9.0, 4.0) is None
    assert _clip_window(None, 4.0) is None
    assert _clip_window("x", "y") is None


# --- contamination guard -----------------------------------------------------


def test_collision_flags_a_clip_that_sounds_like_someone_else():
    library = {"speakers": {"James Whiting": _entry(_sample(ALICE))}}
    found = _collision(library, np.asarray(ALICE_ISH, dtype=np.float32),
                       "Jenny Helland", "max", 0.5)
    assert found is not None
    assert found["name"] == "James Whiting"
    assert found["kind"] == "contamination"
    assert found["score"] == pytest.approx(0.864, abs=0.002)


def test_collision_calls_a_near_identical_match_an_alias():
    # "Emma" and "Emma Thorne" are the same voice under two name forms, built
    # from the same source transcripts. That wants a merge, not a rejection.
    library = {"speakers": {"Emma": _entry(_sample(ALICE))}}
    found = _collision(library, np.asarray(ALICE, dtype=np.float32),
                       "Emma Thorne", "max", 0.5)
    assert found["kind"] == "alias"
    assert found["score"] >= _ALIAS_SIMILARITY


def test_collision_ignores_the_target_and_passes_a_clean_clip():
    library = {"speakers": {
        "Rebecca Nemaric": _entry(_sample(ALICE)),   # the target itself
        "Someone Else": _entry(_sample(BOB)),
    }}
    assert _collision(library, np.asarray(ALICE, dtype=np.float32),
                      "Rebecca Nemaric", "max", 0.5) is None


def test_collision_is_case_insensitive_about_the_target():
    library = {"speakers": {"Theo Moss": _entry(_sample(ALICE))}}
    assert _collision(library, np.asarray(ALICE, dtype=np.float32),
                      "theo moss", "max", 0.5) is None


def test_collision_ignores_inactive_samples_like_ranking_does():
    library = {"speakers": {"James Whiting": _entry(_sample(ALICE, active=False))}}
    assert _collision(library, np.asarray(ALICE, dtype=np.float32),
                      "Jenny Helland", "max", 0.5) is None


# --- nameability -------------------------------------------------------------


def test_present_without_an_active_sample_is_not_nameable():
    # Garry Clarke's only backfilled clip was archived on quality, which leaves
    # him as invisible to naming as never being enrolled — `_rank_library` drops
    # identities with no active exemplar entirely.
    library = {"speakers": {"Garry Clarke": _entry(_sample(ALICE, active=False))}}
    assert _has_active_sample(library, "Garry Clarke") is False


def test_active_sample_makes_a_name_reachable():
    library = {"speakers": {"Hanna Ha": _entry(_sample(ALICE))}}
    assert _has_active_sample(library, "Hanna Ha") is True


def test_unknown_or_empty_name_is_not_nameable():
    assert _has_active_sample({"speakers": {}}, "Nobody") is False
    assert _has_active_sample({"speakers": {"X": _entry()}}, "X") is False


# --- the diff ----------------------------------------------------------------


@pytest.fixture
def libraries(tmp_path):
    live = tmp_path / "embeddings.json"
    naming = tmp_path / "voice-library.json"
    live.write_text(json.dumps({"speakers": {
        "Jenny Helland": _entry(_sample(ALICE)),
        "Shared Person": _entry(_sample(BOB)),
    }}), encoding="utf-8")
    naming.write_text(json.dumps({"speakers": {
        "Shared Person": _entry(_sample(BOB)),
        "Naming Only": _entry(_sample(ALICE)),
        "Inert Person": _entry(_sample(ALICE, active=False)),
    }}), encoding="utf-8")
    config = tmp_path / "active.json"
    config.write_text(json.dumps({
        "schema_version": 1,
        "enabled": True,
        "review_only": False,
        "candidate_dir": str(tmp_path),
        "model_key": "redimnet2_b6",
        "model_path": str(tmp_path / "model.pt"),
        "library_path": str(naming),
        "scorer": "max",
        "threshold": 0.5,
    }), encoding="utf-8")
    (tmp_path / "model.pt").write_bytes(b"stub")
    return {"live": live, "naming": naming, "config": config}


def test_diff_separates_the_three_failure_classes(libraries):
    report = diff_libraries(
        config_path=libraries["config"], main_path=libraries["live"],
    )
    assert report["available"] is True
    # Enrolled live, absent from naming: silently unnameable.
    assert report["missing_from_naming"] == ["Jenny Helland"]
    # In naming only — usually a stale or short name form from the bulk build.
    assert report["candidate_only"] == ["Inert Person", "Naming Only"]
    # Enrolled in naming yet unreachable by it; invisible to a name-only compare.
    assert report["present_but_unnameable"] == ["Inert Person"]
    assert report["shared_count"] == 1


def test_diff_reports_unavailable_rather_than_raising(tmp_path, libraries):
    report = diff_libraries(
        config_path=libraries["config"], main_path=tmp_path / "absent.json",
    )
    assert report["available"] is False
    assert "not found" in report["reason"]
    assert report["missing_from_naming"] == []
