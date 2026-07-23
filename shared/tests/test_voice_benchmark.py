from shared.voice_benchmark import benchmark_open_set, benchmark_voice_library


def _sample(vector, source, *, active=True):
    return {
        "embedding": vector,
        "source_file": source,
        "quality_score": 0.9,
        "active": active,
    }


def test_benchmark_holds_out_whole_meetings_and_scores_robustly():
    library = {"speakers": {
        "Alice": {"samples": [
            _sample([1.0, 0.0], "meeting-1"),
            _sample([0.99, 0.01], "meeting-2"),
            _sample([0.98, 0.02], "meeting-3"),
            _sample([0.97, 0.03], "meeting-4"),
        ]},
        "Bob": {"samples": [
            _sample([0.0, 1.0], "meeting-1"),
            _sample([0.01, 0.99], "meeting-2"),
            _sample([0.02, 0.98], "meeting-3"),
            _sample([0.03, 0.97], "meeting-4"),
        ]},
    }}

    result = benchmark_voice_library(library, min_gallery_meetings=3, max_cases_per_speaker=20)

    assert result["target_cases"] == 8
    assert result["eligible_target_speakers"] == 2
    assert result["scorers"]["top3_median"]["top1_accuracy"] == 1.0
    assert result["scorers"]["centroid"]["top1_accuracy"] == 1.0
    assert all(case["best_supporting_meetings"] == 3 for case in result["scorers"]["top3_median"]["case_results"])


def test_robust_scorer_excludes_people_without_enough_independent_meetings():
    library = {"speakers": {
        "Alice": {"samples": [
            _sample([1.0, 0.0], "a-1"), _sample([1.0, 0.0], "a-2"),
            _sample([1.0, 0.0], "a-3"), _sample([1.0, 0.0], "a-4"),
        ]},
        "One Clip": {"samples": [_sample([1.0, 0.0], "other")]},
    }}

    result = benchmark_voice_library(library, min_gallery_meetings=3)

    assert all(case["best_name"] == "Alice" for case in result["scorers"]["top3_median"]["case_results"])
    assert any(case["best_name"] == "One Clip" for case in result["scorers"]["max"]["case_results"])


def test_benchmark_excludes_competing_source_from_targets_and_gallery():
    library = {"speakers": {
        "Alice": {"samples": [
            _sample([1.0, 0.0], "meeting-1"), _sample([1.0, 0.0], "meeting-2"),
            _sample([1.0, 0.0], "meeting-3"), _sample([1.0, 0.0], "meeting-4"),
            _sample([0.0, 1.0], "competing"),
        ]},
        "Bob": {"samples": [
            _sample([0.0, 1.0], "bob-1"), _sample([0.0, 1.0], "bob-2"),
            _sample([0.0, 1.0], "bob-3"), _sample([0.0, 1.0], "bob-4"),
        ]},
    }}

    result = benchmark_voice_library(
        library, min_gallery_meetings=3, excluded_sources=["competing"],
    )

    assert result["target_cases"] == 8
    assert all(
        case["held_out_source"] != "competing"
        for case in result["scorers"]["max"]["case_results"]
    )


def test_gate_search_never_accepts_cases_without_a_runner_up():
    # Only Alice has enough meetings for the robust scorers, so no runner-up
    # identity exists and the trivially-huge margin must not count as accepted.
    library = {"speakers": {
        "Alice": {"samples": [
            _sample([1.0, 0.0], "a-1"), _sample([1.0, 0.0], "a-2"),
            _sample([1.0, 0.0], "a-3"), _sample([1.0, 0.0], "a-4"),
        ]},
        "One Clip": {"samples": [_sample([1.0, 0.0], "other")]},
    }}

    result = benchmark_voice_library(library, min_gallery_meetings=3)

    robust = result["scorers"]["top3_median"]
    assert robust["cases"] == 4
    assert robust["production_gate"]["auto_decisions"] == 0
    assert robust["best_observed_zero_error_gate"]["auto_decisions"] == 0


def test_closed_set_report_shape_is_unchanged_by_open_set_additions():
    library = {"speakers": {
        "Alice": {"samples": [
            _sample([1.0, 0.0], "a-1"), _sample([1.0, 0.0], "a-2"),
            _sample([1.0, 0.0], "a-3"), _sample([1.0, 0.0], "a-4"),
        ]},
        "Bob": {"samples": [
            _sample([0.0, 1.0], "b-1"), _sample([0.0, 1.0], "b-2"),
            _sample([0.0, 1.0], "b-3"), _sample([0.0, 1.0], "b-4"),
        ]},
    }}

    result = benchmark_voice_library(library, min_gallery_meetings=3)

    assert set(result) == {
        "generated_at", "kind", "leakage_control", "min_gallery_meetings",
        "max_cases_per_speaker", "excluded_sources", "library_speakers",
        "library_samples", "eligible_target_speakers", "target_cases", "scorers",
    }
    case = result["scorers"]["max"]["case_results"][0]
    assert set(case) == {
        "actual", "held_out_source", "best_name", "best_score",
        "runner_up_score", "margin", "best_supporting_meetings", "correct",
    }


def test_open_set_impostor_gallery_fully_excludes_query_identity():
    # Alice's held-out embedding is identical to her own gallery (sim 1.0);
    # if she leaked into the impostor gallery she would win every case.
    library = {"speakers": {
        "Alice": {"samples": [_sample([1.0, 0.0], f"alice-{i}") for i in range(4)]},
        "Bob": {"samples": [_sample([0.0, 1.0], f"bob-{i}") for i in range(4)]},
        "Carol": {"samples": [_sample([-1.0, 0.0], f"carol-{i}") for i in range(4)]},
    }}

    result = benchmark_open_set(library, min_gallery_meetings=3)

    for mode in ("max", "top3_median", "centroid"):
        cases = result["scorers"][mode]["case_results"]
        assert len(cases) == 12
        assert all(case["impostor_name"] != case["actual"] for case in cases)
        assert all(
            case["impostor_score"] < 0.5
            for case in cases
            if case["actual"] == "Alice"
        )


def test_open_set_still_excludes_the_held_out_meeting_from_impostors():
    # Bob's "a-1" sample is identical to Alice's held-out query; the meeting
    # exclusion must strip it from his impostor gallery for that case.
    library = {"speakers": {
        "Alice": {"samples": [_sample([1.0, 0.0], f"a-{i}") for i in range(1, 5)]},
        "Bob": {"samples": [
            _sample([1.0, 0.0], "a-1"),
            _sample([0.0, 1.0], "b-1"), _sample([0.0, 1.0], "b-2"),
            _sample([0.0, 1.0], "b-3"),
        ]},
        "Carol": {"samples": [_sample([-1.0, 0.0], f"c-{i}") for i in range(4)]},
    }}

    result = benchmark_open_set(library, min_gallery_meetings=3)

    matches = [
        case
        for case in result["scorers"]["max"]["case_results"]
        if case["actual"] == "Alice" and case["held_out_source"] == "a-1"
    ]
    assert len(matches) == 1
    assert matches[0]["impostor_score"] < 0.5


def test_open_set_counts_confident_impostor_as_false_gate_pass():
    # Bob sits just above the 0.71 gate next to Alice, and far from Carol,
    # so both Alice and Bob cases clear the margin over the runner-up.
    library = {"speakers": {
        "Alice": {"samples": [_sample([1.0, 0.0], f"alice-{i}") for i in range(4)]},
        "Bob": {"samples": [_sample([0.9, 0.1], f"bob-{i}") for i in range(4)]},
        "Carol": {"samples": [_sample([0.0, 1.0], f"carol-{i}") for i in range(4)]},
    }}

    result = benchmark_open_set(library, min_gallery_meetings=3)

    summary = result["scorers"]["max"]
    alice_cases = [case for case in summary["case_results"] if case["actual"] == "Alice"]
    assert all(case["impostor_name"] == "Bob" for case in alice_cases)
    assert all(case["accepted"] for case in alice_cases)
    assert summary["false_passes"] == 8
    assert summary["false_pass_rate"] == round(8 / 12, 4)
    assert summary["accepted_names"] == {"Alice": 4, "Bob": 4}
    assert summary["per_speaker"]["Alice"]["false_passes"] == 4
    assert summary["per_speaker"]["Carol"]["false_passes"] == 0
    assert summary["worst_cases"][0]["impostor_name"] in {"Alice", "Bob"}
    assert summary["impostor_score"]["max"] >= 0.71


def test_open_set_ignores_weak_impostor_scores():
    library = {"speakers": {
        "Alice": {"samples": [_sample([1.0, 0.0], f"alice-{i}") for i in range(4)]},
        "Bob": {"samples": [_sample([0.0, 1.0], f"bob-{i}") for i in range(4)]},
        "Carol": {"samples": [_sample([-1.0, 0.0], f"carol-{i}") for i in range(4)]},
    }}

    result = benchmark_open_set(library, min_gallery_meetings=3)

    for mode in ("max", "top3_median", "centroid"):
        summary = result["scorers"][mode]
        assert summary["impostor_cases"] == 12
        assert summary["false_passes"] == 0
        assert summary["false_pass_rate"] == 0.0
        assert summary["accepted_names"] == {}
        assert summary["worst_cases"] == []


def test_open_set_without_runner_up_is_never_a_gate_pass():
    # A single impostor identity leaves no margin to judge, even at sim ~1.
    library = {"speakers": {
        "Alice": {"samples": [_sample([1.0, 0.0], f"alice-{i}") for i in range(4)]},
        "Bob": {"samples": [_sample([0.99, 0.01], f"bob-{i}") for i in range(4)]},
    }}

    result = benchmark_open_set(library, min_gallery_meetings=3)

    summary = result["scorers"]["max"]
    assert summary["impostor_cases"] == 8
    assert all(case["runner_up_name"] is None for case in summary["case_results"])
    assert all(case["impostor_score"] > 0.71 for case in summary["case_results"])
    assert summary["false_passes"] == 0


def _many_meeting_library(meetings):
    return {"speakers": {
        "Alice": {"samples": [_sample([1.0, 0.0], f"alice-{i:02d}") for i in range(meetings)]},
        "Bob": {"samples": [_sample([0.0, 1.0], f"bob-{i:02d}") for i in range(meetings)]},
    }}


def _held_out_sources(result):
    return sorted(
        case["held_out_source"] for case in result["scorers"]["max"]["case_results"]
    )


def test_stratified_case_selection_is_seeded_and_deterministic():
    library = _many_meeting_library(25)

    first = benchmark_voice_library(
        library, min_gallery_meetings=3, max_cases_per_speaker=20,
        case_selection="stratified", seed=7,
    )
    second = benchmark_voice_library(
        library, min_gallery_meetings=3, max_cases_per_speaker=20,
        case_selection="stratified", seed=7,
    )
    third = benchmark_voice_library(
        library, min_gallery_meetings=3, max_cases_per_speaker=20,
        case_selection="stratified", seed=8,
    )

    assert len(_held_out_sources(first)) == 40
    assert _held_out_sources(first) == _held_out_sources(second)
    assert _held_out_sources(first) != _held_out_sources(third)


def test_first_case_selection_ignores_seed():
    library = _many_meeting_library(25)

    plain = benchmark_voice_library(library, min_gallery_meetings=3, max_cases_per_speaker=20)
    seeded = benchmark_voice_library(
        library, min_gallery_meetings=3, max_cases_per_speaker=20, seed=99,
    )

    assert _held_out_sources(plain) == _held_out_sources(seeded)
