from shared.split_recording import split_transcript_payload


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
