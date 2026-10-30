from shared.diarize_lite import _split_long_segments
from shared.diarize_sortformer import _assign_speakers_word_level, _smooth_label_flicker
from shared.word_timing import aligned_tokens_to_words, words_to_text


def _word(text, start, end):
    return {"word": text, "start": start, "end": end}


def test_words_to_text_keeps_punctuation_attached():
    assert words_to_text([
        _word("Hello", 0, 0.3),
        _word(",", 0.3, 0.4),
        _word("world", 0.4, 0.8),
        _word("!", 0.8, 0.9),
    ]) == "Hello, world!"


def test_parakeet_subword_tokens_are_grouped_at_leading_space_boundaries():
    result = aligned_tokens_to_words([
        {"text": "P", "start": 0.0, "end": 0.1},
        {"text": "er", "start": 0.1, "end": 0.1},
        {"text": "fect", "start": 0.1, "end": 0.3},
        {"text": ".", "start": 0.3, "end": 0.4},
        {"text": " James", "start": 0.5, "end": 0.6},
        {"text": " is", "start": 0.7, "end": 0.8},
        {"text": " on", "start": 0.9, "end": 1.0},
    ])

    assert [item["word"] for item in result] == ["Perfect.", "James", "is", "on"]
    assert result[0]["start"] == 0.0
    assert result[0]["end"] == 0.4


def test_sortformer_splits_a_sentence_at_word_speaker_change():
    segments = [{
        "start": 0.0,
        "end": 4.0,
        "text": "Hello there yes thanks",
        "words": [
            _word("Hello", 0.0, 0.8),
            _word("there", 0.8, 1.5),
            _word("yes", 2.0, 2.8),
            _word("thanks", 2.8, 3.5),
        ],
    }]
    turns = [(0.0, 1.6, "Speaker 1"), (1.9, 3.8, "Speaker 2")]

    result = _assign_speakers_word_level(segments, turns)

    assert [(item["speaker"], item["text"]) for item in result] == [
        ("Speaker 1", "Hello there"),
        ("Speaker 2", "yes thanks"),
    ]
    assert result[0]["start"] == 0.0
    assert result[0]["end"] == 1.5
    assert result[1]["start"] == 2.0
    assert result[1]["end"] == 3.5
    assert result[0]["words"] == segments[0]["words"][:2]
    assert result[1]["words"] == segments[0]["words"][2:]


def test_long_timed_segment_is_split_without_losing_word_timing():
    words = [_word(f"word{i}", i * 2.0, i * 2.0 + 0.8) for i in range(20)]
    segment = {
        "start": 0.0,
        "end": 40.0,
        "text": words_to_text(words),
        "speaker": "Speaker 1",
        "speaker_id": 0,
        "words": words,
    }

    result = _split_long_segments([segment], max_duration=10.0)

    assert len(result) == 4
    assert all(item["end"] - item["start"] <= 10.0 for item in result)
    assert [word["word"] for item in result for word in item["words"]] == [
        word["word"] for word in words
    ]
    assert result[0]["text"] == "word0 word1 word2 word3 word4"


def _runs(*spec):
    # (speaker, word, start, end) per word, each its own run.
    return [{"start": s, "end": e, "text": w, "words": [_word(w, s, e)], "speaker": spk}
            for spk, w, s, e in spec]


def test_sub_second_flips_inside_one_persons_speech_are_folded_back():
    # Rec79: one person's answer cut into alternating one-word lines.
    runs = _runs(
        ("Speaker 2", "i want to see how people use it", 0.0, 3.0),
        ("Speaker 4", "here", 3.1, 3.4),
        ("Speaker 2", "but yeah this is", 3.5, 5.0),
        ("Speaker 1", "the", 5.1, 5.3),
        ("Speaker 4", "first", 5.3, 5.6),
        ("Speaker 2", "one that I can credibly see", 5.7, 8.0),
    )

    result = _smooth_label_flicker(runs)

    assert len(result) == 1
    assert result[0]["speaker"] == "Speaker 2"
    assert result[0]["text"] == "i want to see how people use it here but yeah this is the first one that I can credibly see"
    assert (result[0]["start"], result[0]["end"]) == (0.0, 8.0)
    assert len(result[0]["words"]) == 6


def test_a_short_reply_after_a_pause_is_kept():
    runs = _runs(
        ("Speaker 1", "shall we ship it", 0.0, 2.0),
        ("Speaker 2", "yeah", 2.8, 3.0),
        ("Speaker 1", "great let's do that", 3.6, 5.0),
    )

    assert [r["speaker"] for r in _smooth_label_flicker(runs)] == ["Speaker 1", "Speaker 2", "Speaker 1"]


def test_long_runs_between_other_speakers_are_kept():
    runs = _runs(
        ("Speaker 1", "so what do you think", 0.0, 2.0),
        ("Speaker 2", "i think it works well", 2.1, 4.0),
        ("Speaker 1", "good", 4.1, 4.5),
    )

    assert [r["speaker"] for r in _smooth_label_flicker(runs)] == ["Speaker 1", "Speaker 2", "Speaker 1"]


def test_word_level_assignment_smooths_a_flicker_from_the_diarizer():
    segments = [{
        "start": 0.0, "end": 3.0, "text": "one two three four",
        "words": [_word("one", 0.0, 0.8), _word("two", 0.9, 1.2),
                  _word("three", 1.3, 2.1), _word("four", 2.2, 3.0)],
    }]
    turns = [(0.0, 0.85, "Speaker 1"), (0.85, 1.25, "Speaker 2"), (1.25, 3.0, "Speaker 1")]

    result = _assign_speakers_word_level(segments, turns)

    assert [(r["speaker"], r["text"]) for r in result] == [("Speaker 1", "one two three four")]
