"""Samples move to whoever the transcript says is speaking in their window."""
import json

from shared import voice_library_reconcile as rec


def _transcript(*turns, source="user"):
    names = {}
    segments = []
    for sid, (name, start, end) in enumerate(turns):
        names[str(sid)] = name
        segments.append({"start": start, "end": end, "speaker_id": sid, "text": "x"})
    meta = {sid: {"source": source, "verified": True} for sid in names}
    return {"speaker_names": names, "speaker_meta": meta, "segments": segments}


def _sample(rec_name, start, end, **extra):
    return {"embedding": [1.0, 0.0], "audio_file": f"/r/{rec_name}.mp3",
            "source_file": f"/t/{rec_name}_diarized.json",
            "segment_start": start, "segment_end": end, "speaker_id": "9", **extra}


def _loader(transcripts):
    return lambda path: transcripts.get(path)


def test_same_person_accepts_short_forms_and_typos_only():
    assert rec.same_person("Gary", "Gary Francis")
    assert rec.same_person("Lucy M", "Lucy McKay")
    assert rec.same_person("Lucy Reed", "Lucy R")
    assert rec.same_person("Chris Wildmsith", "Chris Wildsmith")
    assert not rec.same_person("Chris Laidler", "Chris Wildsmith")
    assert not rec.same_person("Joe Kraft", "Rebecca Nemaric")


def test_sample_moves_to_the_verified_speaker_in_its_window():
    lib = {"speakers": {
        "Rebecca Nemaric": {"samples": [_sample("HiD94", 100, 130), _sample("Rec45", 10, 40)]},
        "Joe Kraft": {"samples": []},
    }}
    transcripts = {
        "/t/HiD94_diarized.json": _transcript(("Joe Kraft", 90, 140)),
        "/t/Rec45_diarized.json": _transcript(("Rebecca Nemaric", 0, 50)),
    }
    moves = rec.find_moves(lib, _loader(transcripts))
    assert [(m["name"], m["to"], m["action"]) for m in moves] == [("Rebecca Nemaric", "Joe Kraft", "move")]
    counts = rec.apply_moves(lib, moves)
    assert counts["moved"] == 1
    assert [s["audio_file"] for s in lib["speakers"]["Rebecca Nemaric"]["samples"]] == ["/r/Rec45.mp3"]
    moved = lib["speakers"]["Joe Kraft"]["samples"][0]
    assert moved["speaker_id"] == "0" and moved["relabelled"]["from"] == "Rebecca Nemaric"


def test_typo_resolves_to_the_existing_profile_and_clears_quarantine():
    sample = _sample("Rec1", 0, 30, active=False, quarantined={"sounds_like": "Chris Wildsmith"})
    lib = {"speakers": {"Ian Reay": {"samples": [sample]}, "Chris Wildsmith": {"samples": []}}}
    moves = rec.find_moves(lib, _loader({"/t/Rec1_diarized.json": _transcript(("Chris Wildmsith", 0, 30))}))
    rec.apply_moves(lib, moves)
    moved = lib["speakers"]["Chris Wildsmith"]["samples"][0]
    assert moved["active"] is True and "quarantined" not in moved
    assert "Ian Reay" not in lib["speakers"]  # left empty, removed


def test_short_form_generic_and_mixed_windows_are_left_alone():
    lib = {"speakers": {"Gary Francis": {"samples": [
        _sample("A", 0, 30), _sample("B", 0, 30), _sample("C", 0, 30)]}}}
    transcripts = {
        "/t/A_diarized.json": _transcript(("Gary", 0, 30)),
        "/t/B_diarized.json": _transcript(("Speaker 2", 0, 30)),
        "/t/C_diarized.json": _transcript(("Gary Francis", 0, 15), ("Joe Kraft", 15, 30)),
    }
    assert rec.find_moves(lib, _loader(transcripts)) == []


def test_automatic_match_sets_aside_instead_of_moving():
    # An auto match came from the library itself, even once flagged verified.
    lib = {"speakers": {"Ian Reay": {"samples": [_sample("A", 0, 30)]}}}
    transcripts = {"/t/A_diarized.json": _transcript(("Jeff Chow", 0, 30), source="auto")}
    moves = rec.find_moves(lib, _loader(transcripts))
    assert moves[0]["action"] == "set_aside"
    assert rec.apply_moves(lib, moves)["set_aside"] == 1
    sample = lib["speakers"]["Ian Reay"]["samples"][0]
    assert sample["active"] is False and sample["quarantined"]["sounds_like"] == "Jeff Chow"
    assert rec.find_moves(lib, _loader(transcripts)) == []  # not re-reported


def test_duplicate_clip_already_under_target_is_dropped():
    lib = {"speakers": {"A Person": {"samples": [_sample("R", 0, 30)]},
                        "Joe Kraft": {"samples": [_sample("R", 0, 30)]}}}
    moves = rec.find_moves(lib, _loader({"/t/R_diarized.json": _transcript(("Joe Kraft", 0, 30))}))
    counts = rec.apply_moves(lib, moves)
    assert counts["duplicates_dropped"] == 1 and len(lib["speakers"]["Joe Kraft"]["samples"]) == 1


def test_reconcile_file_holds_back_bulk_changes(tmp_path):
    path = tmp_path / "lib.json"
    path.write_text(json.dumps({"speakers": {"X Y": {"samples": [_sample("Z", 0, 30)]}}}))
    # No transcript on disk → nothing to do.
    assert rec.reconcile_file(path, apply=True, limit=0)["found"] == 0


def test_sample_put_back_by_the_reviewer_is_not_set_aside_again():
    lib = {"speakers": {"Ian Reay": {"samples": [_sample("A", 0, 30, keep_after_audit=True)]}}}
    transcripts = {"/t/A_diarized.json": _transcript(("Jeff Chow", 0, 30), source="auto")}
    assert rec.find_moves(lib, _loader(transcripts)) == []
