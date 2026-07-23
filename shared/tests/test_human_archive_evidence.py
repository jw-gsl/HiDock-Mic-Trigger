import json

from shared.human_archive_evidence import (
    build_human_archive_inventory,
    plan_sidecar_replacements,
)


def _write_export(path, created, turns):
    body = [f"Creation Time: {created}", ""]
    for start, end, name in turns:
        body.extend([f"{start} - {end} {name}:", "", "Text", ""])
    path.write_text("\n".join(body), encoding="utf-8")


def test_inventory_creates_canonical_human_verified_candidate(tmp_path):
    exports = tmp_path / "exports"
    transcripts = tmp_path / "transcripts"
    recordings = tmp_path / "recordings"
    exports.mkdir()
    transcripts.mkdir()
    recordings.mkdir()
    audio = recordings / "meeting.mp3"
    audio.touch()
    _write_export(
        exports / "meeting.txt", "2025/10/17 12:16",
        [("00:00:00", "00:00:09", "James"), ("00:00:10", "00:00:20", "James")],
    )
    sidecar = transcripts / "2025Oct17-121600-HiD73_diarized.json"
    sidecar.write_text(json.dumps({"audio_file": str(audio), "segments": []}), encoding="utf-8")

    result = build_human_archive_inventory(
        exports, transcripts, aliases={"james": "James Whiting"},
    )

    assert result["summary"]["matched"] == 1
    candidate = result["candidates"][0]
    assert candidate["person"] == "James Whiting"
    assert candidate["observed_names"] == ["James"]
    assert candidate["eligible"] is True
    assert candidate["segment_seconds"] == 20.0


def test_inventory_holds_equal_timestamp_matches_as_ambiguous(tmp_path):
    exports = tmp_path / "exports"
    transcripts = tmp_path / "transcripts"
    exports.mkdir()
    transcripts.mkdir()
    _write_export(exports / "meeting.txt", "2025/10/17 12:16", [("00:00:00", "00:00:09", "Alice")])
    for suffix in ("A", "B"):
        (transcripts / f"2025Oct17-121600-{suffix}_diarized.json").write_text("{}", encoding="utf-8")

    result = build_human_archive_inventory(exports, transcripts)

    assert result["summary"]["ambiguous"] == 1
    assert result["candidates"] == []


def test_replacement_plan_uses_human_timestamps_and_holds_conflicting_user_label(tmp_path):
    exports = tmp_path / "exports"
    transcripts = tmp_path / "transcripts"
    exports.mkdir()
    transcripts.mkdir()
    export = exports / "meeting.txt"
    _write_export(
        export, "2025/10/17 12:16",
        [("00:00:00", "00:00:05", "Alice"), ("00:00:05", "00:00:10", "Alice")],
    )
    sidecar = transcripts / "2025Oct17-121600-HiD73_diarized.json"
    sidecar.write_text(json.dumps({
        "segments": [
            {"start": 0, "end": 5, "text": "One"},
            {"start": 5, "end": 10, "text": "Two"},
        ],
        "speaker_names": {"0": "Speaker 1"},
    }), encoding="utf-8")
    inventory = {
        "meetings": [{
            "status": "matched", "export": str(export), "sidecar": str(sidecar),
            "export_sha256": "test-hash",
        }],
    }

    report, replacements = plan_sidecar_replacements(inventory)

    assert report["summary"] == {"ready": 1}
    assert replacements[0][1]["speaker_names"] == {"0": "Alice"}
    assert replacements[0][1]["speaker_meta"]["0"]["source"] == "human_archive_verified"
    assert "speaker_embeddings" not in replacements[0][1]

    original = json.loads(sidecar.read_text())
    original["speaker_names"] = {"0": "Bob"}
    original["speaker_meta"] = {"0": {"source": "user", "verified": True}}
    sidecar.write_text(json.dumps(original), encoding="utf-8")
    held, replacements = plan_sidecar_replacements(inventory)

    assert held["summary"] == {"held_user_verified_conflict": 1}
    assert replacements == []


def test_replacement_plan_deduplicates_identical_exports_and_holds_competing_ones(tmp_path):
    exports = tmp_path / "exports"
    transcripts = tmp_path / "transcripts"
    exports.mkdir()
    transcripts.mkdir()
    first = exports / "first.txt"
    duplicate = exports / "duplicate.txt"
    competing = exports / "competing.txt"
    _write_export(first, "2025/10/17 12:16", [("00:00:00", "00:00:05", "Alice")])
    duplicate.write_text(first.read_text(encoding="utf-8"), encoding="utf-8")
    _write_export(competing, "2025/10/17 12:16", [("00:00:00", "00:00:05", "Bob")])
    sidecar = transcripts / "2025Oct17-121600-HiD73_diarized.json"
    sidecar.write_text(json.dumps({"segments": [{"start": 0, "end": 5, "text": "One"}]}), encoding="utf-8")

    identical_inventory = {"meetings": [
        {"status": "matched", "export": str(first), "sidecar": str(sidecar), "export_sha256": "same"},
        {"status": "matched", "export": str(duplicate), "sidecar": str(sidecar), "export_sha256": "same"},
    ]}
    report, replacements = plan_sidecar_replacements(identical_inventory)

    assert report["summary"] == {"ready": 1}
    assert report["operations"][0]["identical_export_copies"] == [str(first), str(duplicate)]
    assert len(replacements) == 1

    competing_inventory = {"meetings": [
        {"status": "matched", "export": str(first), "sidecar": str(sidecar), "export_sha256": "one"},
        {"status": "matched", "export": str(competing), "sidecar": str(sidecar), "export_sha256": "two"},
    ]}
    held, replacements = plan_sidecar_replacements(competing_inventory)

    assert held["summary"] == {"held_competing_verified_exports": 1}
    assert replacements == []
