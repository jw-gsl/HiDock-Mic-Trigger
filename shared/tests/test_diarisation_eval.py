"""Cover the diarisation metric functions.

These are pure — no audio, no model — so the scoring logic can be trusted before
any conclusion is drawn from a corpus run. The bar matters: a wrong metric is
worse than no metric, because it makes a regression look like an improvement.
"""

import json

import pytest

from shared.diarisation_eval import (
    aggregate,
    asr_input_segments,
    compare,
    discover_cases,
    optimal_mapping,
    overlap_seconds,
    score_case,
    select_sample,
    verified_speaker_ids,
    verified_truth,
)


def _sidecar(**overrides):
    data = {
        "audio_file": "/nope/audio.mp3",
        "speaker_names": {"0": "Alice", "1": "Bob", "2": "Speaker 3"},
        "speaker_meta": {
            "0": {"source": "user", "verified": True},
            "1": {"source": "user", "verified": True},
            "2": {"source": "auto", "verified": False},
        },
        "segments": [
            {"start": 0.0, "end": 10.0, "speaker_id": 0, "speaker": "Alice", "text": "one"},
            {"start": 10.0, "end": 20.0, "speaker_id": 1, "speaker": "Bob", "text": "two"},
            {"start": 20.0, "end": 30.0, "speaker_id": 2, "speaker": "Speaker 3", "text": "three"},
        ],
    }
    data.update(overrides)
    return data


# --- ground truth ------------------------------------------------------------

def test_only_confirmed_speakers_are_ground_truth():
    assert verified_speaker_ids(_sidecar()) == {"0", "1"}


def test_unconfirmed_speech_is_excluded_not_guessed():
    truth = verified_truth(_sidecar())
    # Speaker 3 is auto/unverified: unknown, so it must not appear as truth.
    assert [name for _, _, name in truth] == ["Alice", "Bob"]
    assert sum(end - start for start, end, _ in truth) == 20.0


def test_truth_is_clipped_to_the_window():
    truth = verified_truth(_sidecar(), window=(5.0, 15.0))
    assert truth == [(5.0, 10.0, "Alice"), (10.0, 15.0, "Bob")]


def test_truth_is_empty_without_any_confirmation():
    data = _sidecar(speaker_meta={"0": {"source": "auto", "verified": False}})
    assert verified_truth(data) == []


def test_asr_input_strips_every_speaker_attribution():
    segments = asr_input_segments(_sidecar())
    assert len(segments) == 3
    for segment in segments:
        # Leaving speaker_id in would hand the diarizer its own answer.
        assert "speaker_id" not in segment
        assert "speaker" not in segment
        assert set(segment) <= {"start", "end", "text", "words"}


def test_asr_input_drops_segments_outside_the_window():
    segments = asr_input_segments(_sidecar(), window=(0.0, 12.0))
    assert [(s["start"], s["end"]) for s in segments] == [(0.0, 10.0), (10.0, 12.0)]


# --- mapping -----------------------------------------------------------------

def _pred(*triples):
    return [
        {"start": start, "end": end, "speaker_id": sid}
        for start, end, sid in triples
    ]


def test_overlap_seconds_accumulates_per_pair():
    truth = [(0.0, 10.0, "Alice")]
    predicted = _pred((0.0, 4.0, 0), (4.0, 10.0, 1))
    assert overlap_seconds(truth, predicted) == {("Alice", "0"): 4.0, ("Alice", "1"): 6.0}


def test_optimal_mapping_matches_the_obvious_case():
    truth = [(0.0, 10.0, "Alice"), (10.0, 20.0, "Bob")]
    predicted = _pred((0.0, 10.0, 0), (10.0, 20.0, 1))
    assert optimal_mapping(truth, predicted) == {"0": "Alice", "1": "Bob"}


def test_optimal_mapping_is_one_to_one_when_a_person_is_split():
    """One person split across two predicted speakers must only be credited once.

    A greedy mapping would hand Alice to both halves and report perfect
    accuracy for a clearly wrong result.
    """
    truth = [(0.0, 20.0, "Alice")]
    predicted = _pred((0.0, 10.0, 0), (10.0, 20.0, 1))
    mapping = optimal_mapping(truth, predicted)
    assert list(mapping.values()).count("Alice") == 1


def test_optimal_mapping_maximises_total_overlap_not_first_come():
    # Greedy on iteration order would mis-assign; the optimal solution swaps.
    truth = [(0.0, 10.0, "Alice"), (10.0, 30.0, "Bob")]
    predicted = _pred((0.0, 10.0, 1), (10.0, 30.0, 0))
    assert optimal_mapping(truth, predicted) == {"1": "Alice", "0": "Bob"}


def test_optimal_mapping_is_empty_without_overlap():
    assert optimal_mapping([(0.0, 5.0, "Alice")], _pred((100.0, 105.0, 0))) == {}


# --- scoring -----------------------------------------------------------------

def test_perfect_prediction_scores_zero_confusion():
    truth = [(0.0, 10.0, "Alice"), (10.0, 20.0, "Bob")]
    predicted = _pred((0.0, 10.0, 0), (10.0, 20.0, 1))
    score = score_case(truth, predicted)
    assert score["confusion_rate"] == 0.0
    assert score["count_error"] == 0
    assert score["truth_speakers"] == 2
    assert score["predicted_speakers"] == 2


def test_swapped_speakers_are_not_penalised_labels_are_arbitrary():
    # Predicted ids are arbitrary; only the partition matters.
    truth = [(0.0, 10.0, "Alice"), (10.0, 20.0, "Bob")]
    predicted = _pred((0.0, 10.0, 1), (10.0, 20.0, 0))
    assert score_case(truth, predicted)["confusion_rate"] == 0.0


def test_merging_two_people_reports_confusion_and_a_count_error():
    truth = [(0.0, 10.0, "Alice"), (10.0, 20.0, "Bob")]
    predicted = _pred((0.0, 20.0, 0))
    score = score_case(truth, predicted)
    assert score["count_error"] == -1
    # Half the confirmed speech lands on the wrong person.
    assert score["confusion_rate"] == 0.5


def test_splitting_one_person_reports_a_count_error():
    truth = [(0.0, 20.0, "Alice")]
    predicted = _pred((0.0, 10.0, 0), (10.0, 20.0, 1))
    score = score_case(truth, predicted)
    assert score["count_error"] == 1
    assert score["confusion_rate"] == 0.5


def test_confusion_is_none_when_nothing_overlaps():
    score = score_case([(0.0, 10.0, "Alice")], _pred((99.0, 100.0, 0)))
    assert score["confusion_rate"] is None


def test_name_recall_counts_only_correct_auto_matches():
    truth = [(0.0, 10.0, "Alice"), (10.0, 20.0, "Bob")]
    predicted = _pred((0.0, 10.0, 0), (10.0, 20.0, 1))
    names = {"0": "Alice", "1": "Bob"}
    meta = {"0": {"source": "auto"}, "1": {"source": "generic"}}
    # Only Alice was auto-matched correctly; Bob stayed generic.
    assert score_case(truth, predicted, names, meta)["name_recall"] == 0.5


def test_name_recall_ignores_a_name_the_human_supplied():
    truth = [(0.0, 10.0, "Alice")]
    predicted = _pred((0.0, 10.0, 0))
    names = {"0": "Alice"}
    # source=user means the human named it; that is not auto-tagging recall.
    meta = {"0": {"source": "user"}}
    assert score_case(truth, predicted, names, meta)["name_recall"] == 0.0


# --- aggregation -------------------------------------------------------------

def test_aggregate_reports_exact_rate_bias_and_mae():
    cases = [
        {"count_error": 0, "confusion_rate": 0.0, "name_recall": 1.0, "truth_seconds": 10},
        {"count_error": -1, "confusion_rate": 0.5, "name_recall": 0.0, "truth_seconds": 10},
        {"count_error": 1, "confusion_rate": 0.25, "name_recall": 0.5, "truth_seconds": 10},
    ]
    summary = aggregate(cases)
    assert summary["count_exact_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert summary["count_mae"] == pytest.approx(2 / 3, abs=1e-4)
    # Bias distinguishes "wrong in both directions" from "systematically low".
    assert summary["count_bias"] == 0.0
    assert summary["confusion_rate"] == 0.25
    assert summary["total_truth_seconds"] == 30


def test_aggregate_skips_unscoreable_cases_for_rate_metrics():
    cases = [
        {"count_error": 0, "confusion_rate": None, "truth_seconds": 5},
        {"count_error": 0, "confusion_rate": 0.4, "truth_seconds": 5},
    ]
    summary = aggregate(cases)
    assert summary["scored_cases"] == 1
    assert summary["confusion_rate"] == 0.4


def test_compare_reports_signed_deltas():
    baseline = {"summary": {"confusion_rate": 0.30, "count_mae": 1.0, "name_recall": 0.2}}
    current = {"summary": {"confusion_rate": 0.20, "count_mae": 1.5, "name_recall": 0.5}}
    delta = compare(baseline, current)["delta"]
    assert delta["confusion_rate"] == -0.10   # improvement
    assert delta["count_mae"] == 0.5          # regression
    assert delta["name_recall"] == 0.3        # improvement


def test_compare_tolerates_a_missing_metric():
    assert compare({"summary": {}}, {"summary": {"confusion_rate": 0.1}})["delta"][
        "confusion_rate"
    ] is None


# --- corpus discovery --------------------------------------------------------

def test_discover_skips_sidecars_without_audio_or_confirmation(tmp_path):
    audio = tmp_path / "present.mp3"
    audio.write_bytes(b"\0")

    good = _sidecar(audio_file=str(audio))
    (tmp_path / "good_diarized.json").write_text(json.dumps(good), encoding="utf-8")
    # Audio missing.
    (tmp_path / "noaudio_diarized.json").write_text(
        json.dumps(_sidecar(audio_file=str(tmp_path / "gone.mp3"))), encoding="utf-8"
    )
    # Only one confirmed speaker.
    one = _sidecar(audio_file=str(audio), speaker_meta={"0": {"verified": True}})
    (tmp_path / "one_diarized.json").write_text(json.dumps(one), encoding="utf-8")
    (tmp_path / "broken_diarized.json").write_text("{not json", encoding="utf-8")

    cases = discover_cases(tmp_path)
    assert [case.sidecar.name for case in cases] == ["good_diarized.json"]
    assert cases[0].truth_speakers == 2
    assert cases[0].duration == 30.0


def test_sample_is_deterministic_and_covers_every_speaker_count():
    class Case:
        def __init__(self, name, speakers):
            self.sidecar = type("P", (), {"name": name})()
            self.truth_speakers = speakers
            self.duration = 100.0

    cases = [Case(f"two-{i}", 2) for i in range(20)] + [
        Case("five", 5), Case("eight", 8),
    ]
    first = select_sample(cases, 4, seed=1)
    second = select_sample(cases, 4, seed=1)
    assert [c.sidecar.name for c in first] == [c.sidecar.name for c in second]
    # Stratified: the rare many-speaker meetings appear before a second
    # two-speaker meeting is drawn.
    assert {c.truth_speakers for c in first} == {2, 5, 8}


def test_sample_returns_everything_when_asked_for_more_than_exists():
    class Case:
        truth_speakers = 2
        duration = 1.0
    cases = [Case(), Case()]
    assert len(select_sample(cases, 0)) == 2
    assert len(select_sample(cases, 99)) == 2
