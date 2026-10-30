from shared.split_recording import split_transcript_payload, trim_transcript_payload


def test_split_rebases_second_part_and_does_not_duplicate_boundary_words():
    source = {"audio_file": "source.mp3", "duration_s": 20, "segments": [{
        "start": 8, "end": 12, "text": "one two", "speaker": "A",
        "words": [{"word": "one", "start": 8, "end": 9}, {"word": "two", "start": 11, "end": 12}],
    }]}
    first = split_transcript_payload(source, 10, False, "first.mp3")
    second = split_transcript_payload(source, 10, True, "second.mp3")
    assert first["audio_file"] == "first.mp3"
    assert first["segments"][0]["text"] == "one"
    assert second["duration_s"] == 10
    assert second["segments"][0]["start"] == 1
    assert second["segments"][0]["text"] == "two"


def test_split_handles_word_less_segments():
    source = {"segments": [
        {"start": 0, "end": 4, "text": "early"},
        {"start": 8, "end": 12, "text": "straddles"},
        {"start": 14, "end": 18, "text": "late"},
    ]}
    first = split_transcript_payload(source, 10, False, "first.mp3")
    second = split_transcript_payload(source, 10, True, "second.mp3")
    assert [s["text"] for s in first["segments"]] == ["early"]
    assert [(s["text"], s["start"]) for s in second["segments"]] == [("late", 4)]


def test_trim_drops_everything_after_the_end():
    source = {"audio_file": "rec.mp3", "duration_s": 100, "segments": [
        {"start": 10, "end": 20, "text": "kept", "speaker": "A",
         "words": [{"word": "kept", "start": 10, "end": 20}]},
        {"start": 55, "end": 65, "text": "half cut", "speaker": "B",
         "words": [{"word": "half", "start": 55, "end": 58}, {"word": "cut", "start": 61, "end": 65}]},
        {"start": 70, "end": 80, "text": "gone", "speaker": "A",
         "words": [{"word": "gone", "start": 70, "end": 80}]},
    ]}
    trimmed = trim_transcript_payload(source, 0, 60)
    assert [s["text"] for s in trimmed["segments"]] == ["kept", "half"]
    assert trimmed["segments"][1]["end"] == 58
    assert trimmed["duration_s"] == 60
    assert trimmed["audio_file"] == "rec.mp3"
    assert len(source["segments"]) == 3  # input untouched


def test_trim_rebases_to_the_new_start():
    source = {"duration": 100, "segments": [
        {"start": 2, "end": 4, "text": "intro"},
        {"start": 30, "end": 40, "text": "meeting",
         "words": [{"word": "meeting", "start": 30, "end": 40}]},
    ]}
    trimmed = trim_transcript_payload(source, 25, 90)
    assert [(s["text"], s["start"], s["end"]) for s in trimmed["segments"]] == [("meeting", 5, 15)]
    assert trimmed["segments"][0]["words"][0]["start"] == 5
    assert trimmed["duration"] == 65


def test_write_trimmed_artifacts_rewrites_in_place(tmp_path):
    import json
    from shared.split_recording import write_trimmed_artifacts

    audio = tmp_path / "2026Oct14-145921-Rec70.mp3"
    audio.write_bytes(b"")
    stem = audio.stem
    segments = [
        {"start": 1, "end": 3, "text": "before the end", "speaker": "SPEAKER_00",
         "words": [{"word": "before", "start": 1, "end": 2}, {"word": "the", "start": 2, "end": 2.5},
                   {"word": "end", "start": 2.5, "end": 3}]},
        {"start": 50, "end": 52, "text": "after the trim", "speaker": "SPEAKER_01",
         "words": [{"word": "after", "start": 50, "end": 51}, {"word": "the", "start": 51, "end": 51.5},
                   {"word": "trim", "start": 51.5, "end": 52}]},
    ]
    (tmp_path / f"{stem}_diarized.json").write_text(json.dumps(
        {"audio_file": str(audio), "segments": segments, "speaker_names": {}}))
    (tmp_path / f"{stem}_asr.json").write_text(json.dumps(
        {"audio_file": str(audio), "segments": [dict(s, speaker=None) for s in segments]}))

    md = write_trimmed_artifacts(audio, tmp_path, 0, 10)

    assert md == tmp_path / f"{stem}.md"
    diarized = json.loads((tmp_path / f"{stem}_diarized.json").read_text())
    asr = json.loads((tmp_path / f"{stem}_asr.json").read_text())
    assert [s["text"] for s in diarized["segments"]] == ["before the end"]
    assert [s["text"] for s in asr["segments"]] == ["before the end"]
    text = md.read_text() + (tmp_path / f"{stem}.srt").read_text()
    assert "before the end" in text
    assert "after the trim" not in text


def test_write_trimmed_artifacts_without_transcript(tmp_path):
    from shared.split_recording import write_trimmed_artifacts

    audio = tmp_path / "rec.mp3"
    audio.write_bytes(b"")
    assert write_trimmed_artifacts(audio, tmp_path, 0, 10) is None
    assert list(tmp_path.iterdir()) == [audio]
