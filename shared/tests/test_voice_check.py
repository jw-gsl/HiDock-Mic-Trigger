"""Voice check: propose line/speaker corrections from saved voices, apply accepted ones."""
import json

import numpy as np

from shared import voice_check

CHRIS, JEFF, IAN, JORDAN = np.eye(4)


def _library(**voices):
    return {"speakers": {
        name: {"samples": [{"embedding": list(vec), "audio_file": f"/r/{src}.mp3", "source_file": f"/t/{src}_diarized.json"}
                           for vec, src in samples]}
        for name, samples in voices.items()
    }}


def _data():
    # Speaker 0 is named Jordan but is Chris; one of Ian's lines is Jeff's.
    return {
        "speaker_names": {"0": "Jordan Di Scola", "1": "Ian Reay", "2": "Jeff Chow"},
        "speaker_meta": {"0": {"verified": True}, "1": {"verified": True}, "2": {"verified": True}},
        "segments": [
            {"start": 0.0, "end": 5.0, "speaker_id": 0, "text": "Chris one"},
            {"start": 5.0, "end": 10.0, "speaker_id": 0, "text": "Chris two"},
            {"start": 10.0, "end": 15.0, "speaker_id": 1, "text": "Ian"},
            {"start": 15.0, "end": 20.0, "speaker_id": 1, "text": "actually Jeff"},
            {"start": 20.0, "end": 25.0, "speaker_id": 2, "text": "Jeff"},
            {"start": 25.0, "end": 25.5, "speaker_id": 2, "text": "ok"},
        ],
    }


VOICE_AT = {0.0: CHRIS, 5.0: CHRIS, 10.0: IAN, 15.0: JEFF, 20.0: JEFF}


def _propose(library, **kw):
    return voice_check.propose(
        _data(), lambda s, e: VOICE_AT.get(s), library,
        scorer="max", threshold=0.5, min_margin=0.2,
        people=["Jordan Di Scola", "Ian Reay", "Jeff Chow", "Chris Wildsmith"], **kw)


def test_proposes_line_moves_and_a_misnamed_speaker():
    lib = _library(**{
        "Chris Wildsmith": [(CHRIS, "Rec1")], "Jeff Chow": [(JEFF, "Rec1")],
        "Ian Reay": [(IAN, "Rec2")], "Jordan Di Scola": [(JORDAN, "Rec3")],
    })
    result = _propose(lib)
    moves = {(line["index"], line["to_name"]) for line in result["lines"]}
    assert moves == {(0, "Chris Wildsmith"), (1, "Chris Wildsmith"), (3, "Jeff Chow")}
    assert [(s["name"], s["suggested"]) for s in result["speakers"]] == [("Jordan Di Scola", "Chris Wildsmith")]
    assert result["skipped_short"] == 1


def test_this_recordings_own_samples_cannot_vouch_for_it():
    # The only Chris sample came from this very recording: ignored.
    lib = _library(**{"Chris Wildsmith": [(CHRIS, "RecX")], "Jeff Chow": [(JEFF, "Rec1")], "Ian Reay": [(IAN, "Rec2")]})
    result = _propose(lib, recording="RecX")
    assert "Chris Wildsmith" not in result["voices_known"]
    assert all(line["to_name"] != "Chris Wildsmith" for line in result["lines"])


def test_people_outside_the_meeting_are_never_proposed():
    lib = _library(**{"Someone Else": [(CHRIS, "Rec1")], "Jeff Chow": [(JEFF, "Rec1")], "Ian Reay": [(IAN, "Rec2")]})
    result = _propose(lib)
    assert all(line["to_name"] != "Someone Else" for line in result["lines"])


def test_too_few_known_voices_says_so():
    result = _propose(_library(**{"Jeff Chow": [(JEFF, "Rec1")]}))
    assert result["lines"] == [] and "at least two" in result["note"]


def test_apply_moves_to_existing_and_new_people():
    data = _data()
    summary = voice_check.apply_moves(data, [
        {"index": 3, "to_name": "Jeff Chow", "start": 15.0},
        {"index": 0, "to_name": "Chris Wildsmith", "start": 0.0},
        {"index": 1, "to_name": "Chris Wildsmith", "start": 5.0},
    ])
    assert summary["moved"] == 3
    assert data["segments"][3]["speaker_id"] == 2
    chris = data["segments"][0]["speaker_id"]
    assert data["speaker_names"][str(chris)] == "Chris Wildsmith"
    assert data["segments"][1]["speaker_id"] == chris
    assert data["speaker_meta"][str(chris)]["verified"] is True


def test_apply_skips_a_line_that_no_longer_exists():
    data = _data()
    summary = voice_check.apply_moves(data, [{"index": 3, "to_name": "Jeff Chow", "start": 99.0}])
    assert summary == {"moved": 0, "requested": 1, "not_found": 1}
    assert data["segments"][3]["speaker_id"] == 1


def test_apply_finds_the_line_after_an_earlier_split():
    # A split before the line shifts its index by one; its start time still finds it.
    data = _data()
    data["segments"].insert(1, {"start": 2.5, "end": 5.0, "speaker_id": 0, "text": "split off"})
    summary = voice_check.apply_moves(data, [{"index": 3, "to_name": "Jeff Chow", "start": 15.0}])
    assert summary["moved"] == 1
    assert data["segments"][4]["start"] == 15.0 and data["segments"][4]["speaker_id"] == 2


def test_meeting_people_combines_names_and_invitees():
    data = _data()
    data["speaker_names"]["3"] = "Speaker 4"
    data["calendar_context"] = {"calendar_candidate_names": ["Garry Clarke"]}
    assert voice_check.meeting_people(data) == ["Garry Clarke", "Ian Reay", "Jeff Chow", "Jordan Di Scola"]


def test_renaming_a_speaker_keeps_the_lines_that_really_are_them():
    # Speaker 0 "Jordan" is mostly Chris, but its third line is Jordan himself.
    data = _data()
    data["segments"].insert(2, {"start": 30.0, "end": 35.0, "speaker_id": 0, "text": "Jordan answering"})
    voices = {**VOICE_AT, 30.0: JORDAN}
    lib = _library(**{
        "Chris Wildsmith": [(CHRIS, "Rec1")], "Jeff Chow": [(JEFF, "Rec1")],
        "Ian Reay": [(IAN, "Rec2")], "Jordan Di Scola": [(JORDAN, "Rec3")],
    })
    result = voice_check.propose(
        data, lambda s, e: voices.get(s), lib, scorer="max", threshold=0.5, min_margin=0.2,
        people=["Jordan Di Scola", "Ian Reay", "Jeff Chow", "Chris Wildsmith"])
    speaker = result["speakers"][0]
    assert speaker["suggested"] == "Chris Wildsmith"
    assert speaker["kept"] == [2]
    assert [m["index"] for m in speaker["moves"]] == [0, 1]


def test_unnamed_speaker_is_named_from_anyone_saved():
    # "Speaker 4" isn't on the invite; their voice is saved under Ian.
    data = {
        "speaker_names": {"0": "Jeff Chow", "3": "Speaker 4"},
        "speaker_meta": {"0": {"verified": True}},
        "segments": [
            {"start": 0.0, "end": 5.0, "speaker_id": 0, "text": "Jeff"},
            {"start": 5.0, "end": 10.0, "speaker_id": 3, "text": "Ian really"},
        ],
    }
    voices = {0.0: JEFF, 5.0: IAN}
    lib = _library(**{"Jeff Chow": [(JEFF, "Rec1")], "Ian Reay": [(IAN, "Rec2")]})
    result = voice_check.propose(data, lambda s, e: voices.get(s), lib, scorer="max",
                                 threshold=0.5, min_margin=0.2, people=["Jeff Chow"])
    assert [(s["name"], s["suggested"]) for s in result["speakers"]] == [("Speaker 4", "Ian Reay")]
    assert result["lines"] == []


def _write_transcript(tmp_path, starts):
    path = tmp_path / "Rec1_diarized.json"
    segs = [{"start": s, "end": s + 2, "speaker_id": 0, "text": "x"} for s in starts]
    path.write_text(json.dumps({"segments": segs, "speaker_names": {"0": "Ann"}}))
    return path


def _line(index, start):
    return {"index": index, "start": start, "end": start + 2, "text": "x",
            "from_name": "Ann", "to_name": "Bob", "score": 0.7, "current_score": 0.2}


def test_saved_suggestions_go_stale_when_the_transcript_changes(tmp_path):
    path = _write_transcript(tmp_path, [0, 5])
    voice_check.save_cache(path, {"lines": [_line(1, 5)], "speakers": []})
    assert voice_check.load_cache(path)["lines"][0]["start"] == 5
    path.write_text(path.read_text() + " ")
    assert voice_check.load_cache(path) is None


def test_a_result_computed_before_an_edit_is_saved_as_stale(tmp_path):
    path = _write_transcript(tmp_path, [0, 5])
    digest = voice_check._digest(path)
    path.write_text(path.read_text() + " ")  # edited while the check ran
    voice_check.save_cache(path, {"lines": [_line(1, 5)]}, digest=digest)
    assert voice_check.load_cache(path) is None


def test_applied_and_skipped_suggestions_are_forgotten_and_the_rest_kept(tmp_path):
    path = _write_transcript(tmp_path, [0, 5, 9])
    voice_check.save_cache(path, {"lines": [_line(1, 5), _line(2, 9)], "speakers": [{"speaker_id": "0"}]})
    before = voice_check._digest(path)
    data = json.loads(path.read_text())
    data["segments"].insert(0, {"start": -3, "end": -1, "speaker_id": 0, "text": "y"})  # shifts indices
    data["segments"][2]["speaker_id"] = 1
    path.write_text(json.dumps(data))
    voice_check.forget_suggestions(path, [{"index": 1, "start": 5, "to_name": "Bob"}], digest_before=before)
    cached = voice_check.load_cache(path)
    assert [(l["start"], l["index"]) for l in cached["lines"]] == [(9, 3)]
    assert cached["speakers"] == [{"speaker_id": "0"}]


def test_forgetting_the_last_suggestion_removes_the_saved_file(tmp_path):
    path = _write_transcript(tmp_path, [0, 5])
    voice_check.save_cache(path, {"lines": [_line(1, 5)]})
    voice_check.forget_suggestions(path, [{"start": 5}], digest_before=voice_check._digest(path))
    assert not voice_check.cache_path(path).exists()


def test_an_error_result_is_not_saved(tmp_path):
    path = _write_transcript(tmp_path, [0])
    voice_check.save_cache(path, {"error": "no library"})
    assert not voice_check.cache_path(path).exists()
