import json

from shared.merge_speaker_labels import (
    apply_confirmed_speaker_labels,
    preserve_existing_speaker_labels,
)
from shared.legacy_transcript_recovery import (
    build_human_label_anchor_result,
    find_human_label_source,
    parse_legacy_transcript,
    parse_human_named_transcript,
    restore_legacy_labels,
    restore_legacy_timed_segments,
)


def test_human_named_markdown_is_a_preservation_source(tmp_path):
    transcript = tmp_path / "meeting.md"
    transcript.write_text(
        "## Transcript\n\n"
        "[00:00-00:04] **Alice Smallwood:** Hello there.\n\n"
        "[00:04-00:08] **Speaker 2:** Generic text.\n",
        encoding="utf-8",
    )
    _created, turns = parse_human_named_transcript(transcript)

    assert [(turn.start, turn.end, turn.name) for turn in turns] == [
        (0.0, 4.0, "Alice Smallwood"),
    ]

    source = find_human_label_source(tmp_path / "meeting_diarized.json", transcript)
    assert source is not None
    assert source[0] == transcript


def test_human_named_anchors_preserve_names_while_retaining_word_arrays(tmp_path):
    result = build_human_label_anchor_result(
        [
            {
                "start": 0.0,
                "end": 4.0,
                "text": "Hello there.",
                "words": [{"word": "Hello", "start": 0.0, "end": 1.0}],
            },
            {"start": 4.0, "end": 8.0, "text": "Hi there."},
        ],
        [
            type("Turn", (), {"start": 0.0, "end": 4.0, "name": "Alice Smallwood"})(),
            type("Turn", (), {"start": 4.0, "end": 8.0, "name": "Bob Jones"})(),
        ],
        source_file=tmp_path / "meeting.md",
    )

    assert result is not None
    assert result["speaker_names"] == {"0": "Alice Smallwood", "1": "Bob Jones"}
    assert result["segments"][0]["words"][0]["word"] == "Hello"
    assert result["speaker_meta"]["0"]["source"] == "legacy_import"


def test_confirmed_child_labels_are_propagated_and_duplicate_clusters_collapse(tmp_path):
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    (transcripts / "one_diarized.json").write_text(json.dumps({
        "segments": [
            {"start": 0, "end": 3, "speaker_id": 0, "speaker": "Speaker 1"},
            {"start": 3, "end": 6, "speaker_id": 1, "speaker": "Speaker 2"},
        ],
        "speaker_names": {"0": "Alice", "1": "Bob"},
        "speaker_meta": {
            "0": {"source": "user", "verified": True},
            "1": {"source": "user", "verified": True},
        },
    }), encoding="utf-8")
    (transcripts / "two_diarized.json").write_text(json.dumps({
        "segments": [
            {"start": 0, "end": 3, "speaker_id": 0, "speaker": "Speaker 1"},
        ],
        "speaker_names": {"0": "Alice"},
        "speaker_meta": {"0": {"source": "auto", "confidence": 0.9, "verified": True}},
    }), encoding="utf-8")

    result = apply_confirmed_speaker_labels(
        {
            "segments": [
                {"start": 0, "end": 2, "speaker_id": 0},
                {"start": 3, "end": 5, "speaker_id": 1},
                {"start": 6, "end": 8, "speaker_id": 2},
                {"start": 9, "end": 11, "speaker_id": 3},
            ],
            "speaker_names": {"0": "Speaker 1", "1": "Speaker 2", "2": "Speaker 3", "3": "Speaker 4"},
            "speaker_meta": {
                "0": {"source": "auto", "verified": False},
                "1": {"source": "auto", "verified": False},
                "2": {"source": "generic", "verified": False},
                "3": {"source": "generic", "verified": False},
            },
            "speaker_embeddings": {
                "0": [1.0, 0.0], "1": [0.0, 1.0], "2": [1.0, 1.0], "3": [1.0, 1.0],
            },
        },
        [tmp_path / "one.mp3", tmp_path / "two.mp3"],
        [6.0, 3.0],
        transcripts,
    )

    assert result["speaker_names"] == {"0": "Alice", "1": "Bob", "2": "Speaker 4"}
    assert [segment["speaker_id"] for segment in result["segments"]] == [0, 1, 0, 2]
    assert [segment["source_speaker_id"] for segment in result["segments"]] == ["0", "1", "2", "3"]
    assert result["speaker_meta"]["0"]["verified"] is True
    assert result["confirmed_from_children"] == ["Alice", "Bob"]
    assert result["speaker_lineage"] == {
        "0": {"source_cluster_ids": ["0", "2"], "surviving_name": "Alice"},
        "1": {"source_cluster_ids": ["1"], "surviving_name": "Bob"},
        "2": {"source_cluster_ids": ["3"], "surviving_name": "Speaker 4"},
    }


def test_rediarize_preserves_verified_and_legacy_names_but_rechecks_auto_matches():
    previous = {
        "segments": [
            {"start": 0, "end": 4, "speaker_id": 0, "speaker": "Alice"},
            {"start": 4, "end": 8, "speaker_id": 1, "speaker": "Bob"},
            {"start": 8, "end": 12, "speaker_id": 2, "speaker": "Carol"},
        ],
        "speaker_names": {"0": "Alice", "1": "Bob", "2": "Carol"},
        "speaker_meta": {
            "0": {"source": "user", "verified": True},
            "1": {"source": "auto", "verified": False},
            # No metadata is how older imported sidecars represented a named
            # speaker. That label is still useful historical evidence.
        },
    }
    fresh = {
        "segments": [
            {"start": 0, "end": 4, "speaker_id": 7},
            {"start": 4, "end": 8, "speaker_id": 3},
            {"start": 8, "end": 12, "speaker_id": 9},
        ],
        "speaker_names": {"7": "Speaker 1", "3": "Speaker 2", "9": "Speaker 3"},
        "speaker_meta": {
            "7": {"source": "generic", "verified": False},
            "3": {"source": "generic", "verified": False},
            "9": {"source": "generic", "verified": False},
        },
        "speaker_embeddings": {
            "7": [1.0, 0.0],
            "3": [0.0, 1.0],
            "9": [1.0, 1.0],
        },
    }

    result = preserve_existing_speaker_labels(fresh, previous)

    assert result["speaker_names"] == {"0": "Alice", "1": "Speaker 2", "2": "Carol"}
    assert [segment["speaker"] for segment in result["segments"]] == [
        "Alice", "Speaker 2", "Carol"
    ]
    assert result["speaker_meta"]["0"]["verified"] is True
    assert result["speaker_meta"]["2"]["source"] == "legacy"
    assert result["preserved_speaker_labels"] == ["Alice", "Carol"]
    assert result["speaker_lineage"] == {
        "0": {"source_cluster_ids": ["7"], "surviving_name": "Alice"},
        "1": {"source_cluster_ids": ["3"], "surviving_name": "Speaker 2"},
        "2": {"source_cluster_ids": ["9"], "surviving_name": "Carol"},
    }


def test_legacy_named_export_restores_generic_sidecar_without_overwriting_verified(tmp_path):
    exports = tmp_path / "exports"
    transcripts = tmp_path / "transcripts"
    exports.mkdir()
    transcripts.mkdir()
    export = exports / "meeting.txt"
    export.write_text(
        "Creation Time: 2025/10/17 12:16\n\n"
        "00:00:00 - 00:00:04 Andy Wheeler:\n\nHello\n\n"
        "00:00:04 - 00:00:08 James Whiting:\n\nHi\n",
        encoding="utf-8",
    )
    sidecar = transcripts / "2025Oct17-121600-HiD73_diarized.json"
    sidecar.write_text(json.dumps({
        "audio_file": "/tmp/meeting.mp3",
        "speaker_names": {"0": "Speaker 1", "1": "James Whiting"},
        "speaker_meta": {"0": {"source": "generic", "verified": False},
                          "1": {"source": "user", "verified": True}},
        "segments": [
            {"start": 0, "end": 4, "speaker_id": 0, "speaker": "Speaker 1", "text": "Hello"},
            {"start": 4, "end": 8, "speaker_id": 1, "speaker": "James Whiting", "text": "Hi"},
        ],
    }), encoding="utf-8")

    created, turns = parse_legacy_transcript(export)
    report = restore_legacy_labels(exports, transcripts, apply=True)
    restored = json.loads(sidecar.read_text(encoding="utf-8"))

    assert created is not None
    assert [turn.name for turn in turns] == ["Andy Wheeler", "James Whiting"]
    assert report["changed_sidecars"] == 1
    assert report["restored_speakers"] == 1
    assert restored["speaker_names"] == {"0": "Andy Wheeler", "1": "James Whiting"}
    assert restored["speaker_meta"]["0"]["source"] == "legacy_import"


def test_legacy_timestamps_rebuild_collapsed_diarization_without_overwriting_verified():
    turns = [
        type("Turn", (), {"start": 0.0, "end": 4.0, "name": "Chris Wildsmith"})(),
        type("Turn", (), {"start": 4.0, "end": 8.0, "name": "James Whiting"})(),
    ]
    data = {
        "audio_file": "/tmp/meeting.mp3",
        "segments": [
            {"start": 0.0, "end": 8.0, "speaker_id": 0, "speaker": "Chris Wildsmith", "text": "collapsed"},
        ],
        "speaker_names": {"0": "Chris Wildsmith"},
        "speaker_meta": {"0": {"source": "auto", "verified": False}},
    }
    whisper = {
        "segments": [
            {"start": 0.0, "end": 3.8, "text": "Morning Chris"},
            {"start": 4.1, "end": 7.8, "text": "Hi James"},
        ]
    }

    result = restore_legacy_timed_segments(
        data,
        whisper,
        turns,
        export_path="/tmp/imported.txt",
    )

    assert result is not None
    assert result["speaker_names"] == {"0": "Chris Wildsmith", "1": "James Whiting"}
    assert [segment["speaker"] for segment in result["segments"]] == [
        "Chris Wildsmith", "James Whiting"
    ]
    assert result["speaker_meta"]["1"]["source"] == "legacy_import"


def test_legacy_timestamps_leave_verified_sidecars_untouched():
    turns = [type("Turn", (), {"start": 0.0, "end": 4.0, "name": "James Whiting"})()]
    data = {
        "segments": [{"start": 0.0, "end": 4.0, "speaker_id": 0}],
        "speaker_names": {"0": "Chris Wildsmith"},
        "speaker_meta": {"0": {"source": "user", "verified": True}},
    }
    whisper = {"segments": [{"start": 0.0, "end": 3.9, "text": "hello"}]}

    assert restore_legacy_timed_segments(data, whisper, turns, export_path="/tmp/imported.txt") is None
