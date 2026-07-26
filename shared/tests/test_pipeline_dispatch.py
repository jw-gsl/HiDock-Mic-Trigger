"""Tests for backend routing decisions that affect user-visible controls."""
from __future__ import annotations

from shared import pipeline_dispatch


def test_explicit_speaker_count_goes_to_sortformer_post_hoc(monkeypatch):
    """An explicit count no longer reroutes to lite: sortformer honours it
    post-hoc by merging stitched labels down by voice similarity."""
    calls = {}

    monkeypatch.setattr(
        pipeline_dispatch,
        "_active",
        lambda _stage, _default: "sortformer",
    )

    def fake_sortformer(audio_path, segments, n_speakers=None, calendar_context=None):
        calls.update(
            audio_path=audio_path,
            segments=segments,
            n_speakers=n_speakers,
            calendar_context=calendar_context,
        )
        return {"backend": "sortformer"}

    monkeypatch.setattr("shared.diarize_sortformer.diarize", fake_sortformer)

    context = object()
    result = pipeline_dispatch.diarize(
        "/tmp/meeting.mp3",
        [{"start": 0, "end": 1, "text": "hello"}],
        n_speakers=2,
        calendar_context=context,
    )

    assert result == {"backend": "sortformer"}
    assert calls == {
        "audio_path": "/tmp/meeting.mp3",
        "segments": [{"start": 0, "end": 1, "text": "hello"}],
        "n_speakers": 2,
        "calendar_context": context,
    }
