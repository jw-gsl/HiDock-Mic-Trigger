"""Review decisions are remembered and summarised."""
from collections import Counter

from shared import review_feedback as fb


def test_skips_are_remembered_and_suppressed(tmp_path):
    fb.record_refine("Rec1", "skipped", [{"start": 10.0, "to_name": "Ian", "from_name": "Jeff", "score": 0.7}], tmp_path)
    skipped = fb.skipped_suggestions("Rec1", tmp_path)
    lines = [{"start": 10.0, "to_name": "Ian", "score": 0.72}, {"start": 20.0, "to_name": "Ian", "score": 0.8}]
    kept, dropped = fb.suppress_skipped(lines, skipped)
    assert dropped == 1 and [line["start"] for line in kept] == [20.0]


def test_a_skipped_line_returns_when_evidence_grows(tmp_path):
    fb.record_refine("Rec1", "skipped", [{"start": 10.0, "to_name": "Ian", "score": 0.6}], tmp_path)
    kept, dropped = fb.suppress_skipped([{"start": 10.0, "to_name": "Ian", "score": 0.9}],
                                        fb.skipped_suggestions("Rec1", tmp_path))
    assert dropped == 0 and len(kept) == 1


def test_a_later_apply_overrides_a_skip(tmp_path):
    move = {"start": 10.0, "to_name": "Ian", "score": 0.7}
    fb.record_refine("Rec1", "skipped", [move], tmp_path)
    fb.record_refine("Rec1", "applied", [move], tmp_path)
    assert fb.skipped_suggestions("Rec1", tmp_path) == []


def test_refine_report_counts_by_pair(tmp_path):
    fb.record_refine("Rec1", "applied", [{"start": 1, "from_name": "Ian", "to_name": "Jeff"}] * 3, tmp_path)
    fb.record_refine("Rec1", "skipped", [{"start": 2, "from_name": "Ian", "to_name": "Jeff"}], tmp_path)
    report = fb.refine_report(tmp_path)
    assert report["applied"] == 3 and report["accept_rate"] == 0.75
    assert report["by_pair"][0] == {"pair": "Ian → Jeff", "applied": 3, "skipped": 1, "accept_rate": 0.75}


def test_confirmed_titles_count_recurring_meetings(tmp_path):
    for decision in ("accepted", "accepted", "declined", "chose_other"):
        fb.record_calendar({"decision": decision, "final_title": "Weekly sync" if decision != "chose_other" else "1:1"}, tmp_path)
    assert fb.confirmed_meeting_titles(tmp_path) == Counter({"Weekly sync": 2, "1:1": 1})


def test_tuning_raises_margin_for_a_pair_that_keeps_being_skipped(tmp_path):
    fb.record_refine("Rec1", "applied", [{"start": 1, "from_name": "Ian", "to_name": "Jeff"}] * 3, tmp_path)
    fb.record_refine("Rec1", "skipped", [{"start": 2, "from_name": "Ian", "to_name": "Jeff"}] * 6, tmp_path)
    fb.record_refine("Rec1", "applied", [{"start": 3, "from_name": "Chris", "to_name": "Jordan"}] * 20, tmp_path)
    tuning = fb.tune(tmp_path)
    assert tuning["refine_margin_adjust"] == {"Ian → Jeff": 0.10, "Chris → Jordan": -0.05}
    assert fb.load_tuning(tmp_path)["refine_margin_adjust"]["Ian → Jeff"] == 0.10


def test_too_few_decisions_change_nothing(tmp_path):
    fb.record_refine("Rec1", "skipped", [{"start": 2, "from_name": "Ian", "to_name": "Jeff"}] * 7, tmp_path)
    assert fb.tune(tmp_path)["refine_margin_adjust"] == {}


def test_recurring_preference_turns_off_when_mostly_wrong(tmp_path):
    for decision in ("declined", "chose_other", "declined", "accepted"):
        fb.record_calendar({"decision": decision, "chosen_by": "recurring"}, tmp_path)
    assert fb.tune(tmp_path)["recurring_meetings"] is False


def test_summary_reads_as_sentences():
    full = {"refine": {"applied": 3, "skipped": 1, "by_pair": [{"pair": "Ian → Jeff", "applied": 1, "skipped": 7}]},
            "calendar": {"by_how_suggested": {"time": {"accepted": 4, "declined": 1}}},
            "corrections": {"by_backend": {"nemotron": {"transcripts": 15, "mean_relabelled_share": 0.173}}},
            "tuning": {"refine_margin_adjust": {"Ian → Jeff": 0.1}, "recurring_meetings": True}}
    lines = fb.summary_lines(full)
    assert lines[0] == "Refine: you accepted 3 of 4 suggestions (75%)."
    assert "stronger evidence" in lines[1]
    assert lines[2].startswith("Meetings: you accepted 4 of 5")
    assert "17% of speech" in lines[3]


def test_summary_reports_samples_moved_to_the_transcript_speaker():
    from shared.review_feedback import summary_lines
    lines = summary_lines({"library": {"relabelled": 2, "live_relabelled": 3, "set_aside": 0, "total_set_aside": 0}})
    assert any("moved 5 sample(s)" in line for line in lines)
    assert not any("no mislabelled samples" in line for line in lines)


def test_summary_flags_a_held_back_relabel():
    from shared.review_feedback import summary_lines
    lines = summary_lines({"library": {"live_relabel_held_back": 40, "total_set_aside": 0}})
    assert any("40 samples" in line and "not moved" in line for line in lines)
