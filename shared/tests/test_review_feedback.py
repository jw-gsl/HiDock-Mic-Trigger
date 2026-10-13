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
