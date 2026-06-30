"""Tests for merge_partial_catalog — preserving the newest recordings the H1
firmware truncates from a partial file-list read."""
from extractor import merge_partial_catalog


def rec(name):
    return {"name": name, "length": 100, "signature": name}


def test_partial_read_keeps_cached_newest():
    # Device declares 5 but only returned the 3 oldest; cache has all 5.
    live = [rec("Rec01"), rec("Rec02"), rec("Rec03")]
    prev = [rec(f"Rec0{i}") for i in range(1, 6)]
    merged = merge_partial_catalog(live, declared=5, prev=prev)
    names = [r["name"] for r in merged]
    assert names[:3] == ["Rec01", "Rec02", "Rec03"]   # live order preserved
    assert "Rec04" in names and "Rec05" in names        # newest not evicted


def test_fewer_than_cached_triggers_union_even_without_declared():
    live = [rec("Rec01")]
    prev = [rec("Rec01"), rec("Rec02"), rec("Rec03")]
    merged = merge_partial_catalog(live, declared=None, prev=prev)
    assert {r["name"] for r in merged} == {"Rec01", "Rec02", "Rec03"}


def test_complete_read_is_authoritative_allows_shrink():
    # declared == returned → trust it; a deleted recording really goes away.
    live = [rec("Rec01"), rec("Rec02")]
    prev = [rec("Rec01"), rec("Rec02"), rec("Rec03")]
    merged = merge_partial_catalog(live, declared=2, prev=prev)
    assert {r["name"] for r in merged} == {"Rec01", "Rec02"}


def test_live_wins_for_duplicate_names():
    live = [{"name": "Rec01", "length": 999, "signature": "new"}]
    prev = [{"name": "Rec01", "length": 100, "signature": "old"},
            {"name": "Rec02", "length": 100, "signature": "old"}]
    merged = merge_partial_catalog(live, declared=2, prev=prev)
    by_name = {r["name"]: r for r in merged}
    assert by_name["Rec01"]["length"] == 999   # live entry preferred
    assert "Rec02" in by_name                    # cached extra preserved


def test_no_prev_returns_live():
    live = [rec("Rec01")]
    assert merge_partial_catalog(live, declared=5, prev=[]) == live
