"""Confirming a speaker learns only a clip that sounds like them; the audit
finds and reversibly sets aside samples saved under the wrong person."""
import numpy as np

from shared import voice_library_audit as audit
from shared.legacy_transcript_recovery import LegacyTurn
from shared.voice_candidate_review import choose_verified_clip

IAN, JEFF, NEW = (list(v) for v in np.eye(3))


def _sample(vec, rec):
    return {"embedding": list(vec), "audio_file": f"/r/{rec}.mp3", "source_file": f"/t/{rec}_diarized.json"}


def _library(**people):
    return {"speakers": {n: {"samples": [_sample(v, r) for v, r in s]} for n, s in people.items()}}


# Two 20 s stretches: the longest is really Jeff, the other is Ian.
TURNS = [LegacyTurn(0.0, 25.0, "Ian Reay"), LegacyTurn(100.0, 120.0, "Ian Reay")]


def _embed(voices):
    return lambda start, end: (np.asarray(voices[start]), 3, "test")


def test_known_person_skips_a_clip_that_is_someone_else():
    lib = _library(**{"Ian Reay": [(IAN, "Rec1"), (IAN, "Rec2")], "Jeff Chow": [(JEFF, "Rec1"), (JEFF, "Rec3")]})
    clip, reason = choose_verified_clip(TURNS, "Ian Reay", _embed({0.0: JEFF, 100.0: IAN}), lib,
                                        scorer="top3_median", recording="Rec63")
    assert reason == "" and clip["segment_start"] == 100.0


def test_known_person_with_no_matching_clip_is_not_learned():
    lib = _library(**{"Ian Reay": [(IAN, "Rec1"), (IAN, "Rec2")], "Jeff Chow": [(JEFF, "Rec1"), (JEFF, "Rec3")]})
    clip, reason = choose_verified_clip(TURNS, "Ian Reay", _embed({0.0: JEFF, 100.0: JEFF}), lib,
                                        scorer="top3_median", recording="Rec63")
    assert clip is None and "Jeff Chow" in reason


def test_this_recordings_samples_dont_count_as_the_persons_voice():
    # Ian's only "saved voice" came from this recording and is really Jeff.
    lib = _library(**{"Ian Reay": [(JEFF, "Rec63")], "Jeff Chow": [(JEFF, "Rec1"), (JEFF, "Rec3")]})
    clip, reason = choose_verified_clip(TURNS, "Ian Reay", _embed({0.0: JEFF, 100.0: JEFF}), lib,
                                        scorer="top3_median", recording="Rec63")
    assert clip is None and "already in the library" in reason


def test_new_person_gets_their_most_consistent_clip():
    turns = TURNS + [LegacyTurn(200.0, 215.0, "New Person")]
    lib = _library(**{"Jeff Chow": [(JEFF, "Rec1"), (JEFF, "Rec3")]})
    clip, reason = choose_verified_clip(turns, "New Person", _embed({0.0: JEFF, 100.0: NEW, 200.0: NEW}), lib,
                                        scorer="top3_median", recording="Rec63")
    assert reason == "" and clip["segment_start"] in (100.0, 200.0)


def test_audit_flags_set_aside_and_restore():
    lib = _library(**{
        "Ian Reay": [(IAN, "Rec1"), (IAN, "Rec2"), (JEFF, "Rec5")],
        "Jeff Chow": [(JEFF, "Rec1"), (JEFF, "Rec3")],
    })
    flagged = audit.audit(lib)
    assert [(f["name"], f["sounds_like"], f["recording"]) for f in flagged] == [("Ian Reay", "Jeff Chow", "Rec5")]
    assert audit.set_aside(lib, flagged) == 1
    sample = lib["speakers"]["Ian Reay"]["samples"][2]
    assert sample["active"] is False and sample["quarantined"]["sounds_like"] == "Jeff Chow"
    assert audit.audit(lib) == []
    assert audit.restore(lib) == 1 and sample["active"] is True and "quarantined" not in sample
    # A restored sample is the reviewer's call: later audits leave it alone.
    assert audit.audit(lib) == []


def test_auto_clean_sets_aside_and_keeps_few_backups(tmp_path):
    import json
    path = tmp_path / "voice-library.json"
    lib = _library(**{
        "Ian Reay": [(IAN, "Rec1"), (IAN, "Rec2"), (JEFF, "Rec5")],
        "Jeff Chow": [(JEFF, "Rec1"), (JEFF, "Rec3")],
    })
    path.write_text(json.dumps(lib))
    for _ in range(7):
        (tmp_path / f"voice-library.json.before-audit-2026010{_}-000000").write_text("{}")
    result = audit.auto_clean(path)
    assert result["set_aside"] == 1 and result["total_set_aside"] == 1 and result["people"] == ["Ian Reay"]
    assert len(list(tmp_path.glob("voice-library.json.before-audit-*"))) == audit.KEEP_BACKUPS


def test_auto_clean_holds_back_when_suspiciously_many(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(audit, "AUTO_LIMIT", 0)
    path = tmp_path / "voice-library.json"
    path.write_text(json.dumps(_library(**{
        "Ian Reay": [(IAN, "Rec1"), (IAN, "Rec2"), (JEFF, "Rec5")],
        "Jeff Chow": [(JEFF, "Rec1"), (JEFF, "Rec3")],
    })))
    result = audit.auto_clean(path)
    assert result["held_back"] is True and result["set_aside"] == 0


def test_a_new_enrolment_does_not_reactivate_set_aside_samples():
    from shared.voice_library_lite import _refresh_active_samples
    lib = _library(**{
        "Ian Reay": [(IAN, "Rec1"), (IAN, "Rec2"), (JEFF, "Rec5")],
        "Jeff Chow": [(JEFF, "Rec1"), (JEFF, "Rec3")],
    })
    audit.set_aside(lib, audit.audit(lib))
    _refresh_active_samples(lib["speakers"]["Ian Reay"])
    assert lib["speakers"]["Ian Reay"]["samples"][2]["active"] is False
