"""Tests for the auto-vs-human .md name filter in legacy_transcript_recovery."""
from __future__ import annotations

import json

from shared.legacy_transcript_recovery import LegacyTurn, _drop_unverified_auto_turns


def _sidecar(tmp_path, names_meta):
    p = tmp_path / "meeting_diarized.json"
    p.write_text(json.dumps({
        "speaker_names": {sid: name for sid, (name, _meta) in names_meta.items()},
        "speaker_meta": {sid: meta for sid, (name, meta) in names_meta.items()},
    }))
    return p


def test_unverified_auto_names_are_dropped_from_md_anchors(tmp_path):
    """An .md refreshed by rediarize carries auto names; read back as
    'human' anchors they collapsed fresh clusters (Rec76: 4 -> 2)."""
    sidecar = _sidecar(tmp_path, {
        "0": ("James Whiting", {"source": "auto", "verified": False}),
        "1": ("Chris Wildsmith", {"source": "user", "verified": True}),
        "2": ("Speaker 3", {"source": "generic", "verified": False}),
    })
    turns = [
        LegacyTurn(0.0, 10.0, "James Whiting"),    # auto, unverified -> drop
        LegacyTurn(10.0, 20.0, "Chris Wildsmith"),  # verified human -> keep
        LegacyTurn(20.0, 30.0, "Riley Roberts"),    # not in sidecar -> keep
    ]

    kept = _drop_unverified_auto_turns(turns, sidecar)

    assert [t.name for t in kept] == ["Chris Wildsmith", "Riley Roberts"]


def test_filter_is_case_insensitive_and_tolerates_missing_sidecar(tmp_path):
    sidecar = _sidecar(tmp_path, {
        "0": ("James Whiting", {"source": "auto", "verified": False}),
    })
    turns = [LegacyTurn(0.0, 10.0, "james whiting")]
    assert _drop_unverified_auto_turns(turns, sidecar) == []

    turns = [LegacyTurn(0.0, 10.0, "James Whiting")]
    missing = tmp_path / "nope_diarized.json"
    assert _drop_unverified_auto_turns(turns, missing) == turns


def test_verified_and_legacy_names_are_never_dropped(tmp_path):
    sidecar = _sidecar(tmp_path, {
        "0": ("James Whiting", {"source": "auto", "verified": True}),
        "1": ("Chris Wildsmith", {"source": "legacy_import", "verified": False}),
    })
    turns = [LegacyTurn(0.0, 10.0, "James Whiting"), LegacyTurn(10.0, 20.0, "Chris Wildsmith")]
    assert _drop_unverified_auto_turns(turns, sidecar) == turns
