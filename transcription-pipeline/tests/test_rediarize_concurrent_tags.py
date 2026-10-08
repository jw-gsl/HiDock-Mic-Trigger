"""Tags made while a re-diarisation runs must survive its save (Rec45, 2026-10-08)."""
import json
import os


def _seg(start, end, speaker_id):
    return {"start": start, "end": end, "speaker_id": speaker_id,
            "speaker": f"Speaker {speaker_id + 1}", "text": "x"}


def _fresh_result():
    # What the re-run computed from the file as it was when it started.
    return {
        "segments": [_seg(0, 60, 0), _seg(60, 120, 1), _seg(120, 180, 0)],
        "speaker_names": {"0": "Speaker 1", "1": "Speaker 2"},
        "speaker_meta": {"0": {"source": "generic"}, "1": {"source": "generic"}},
    }


def test_tags_written_during_the_run_are_kept(tmp_path):
    import transcribe

    sidecar = tmp_path / "Rec_diarized.json"
    sidecar.write_text(json.dumps({"segments": [], "speaker_names": {}}))
    loaded = sidecar.stat().st_mtime_ns

    # Meanwhile the reviewer confirms both people in the viewer.
    sidecar.write_text(json.dumps({
        "segments": [_seg(0, 58, 0), _seg(58, 122, 1), _seg(122, 180, 0)],
        "speaker_names": {"0": "James Whiting", "1": "Ian Reay"},
        "speaker_meta": {"0": {"source": "user", "verified": True},
                         "1": {"source": "user", "verified": True}},
    }))
    os.utime(sidecar, ns=(loaded + 1_000_000, loaded + 1_000_000))

    out = transcribe._carry_over_concurrent_speaker_tags(sidecar, loaded, _fresh_result(), None)
    names = out["speaker_names"]
    assert sorted(names.values()) == ["Ian Reay", "James Whiting"]
    assert names[str(out["segments"][1]["speaker_id"])] == "Ian Reay"


def test_untouched_file_leaves_the_result_alone(tmp_path):
    import transcribe

    sidecar = tmp_path / "Rec_diarized.json"
    sidecar.write_text("{}")
    result = _fresh_result()
    out = transcribe._carry_over_concurrent_speaker_tags(
        sidecar, sidecar.stat().st_mtime_ns, result, None)
    assert out is result
