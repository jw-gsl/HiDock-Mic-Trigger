"""Tests for backend routing decisions that affect user-visible controls."""
from __future__ import annotations

from shared import pipeline_dispatch


def test_explicit_speaker_count_uses_count_aware_backend(monkeypatch):
    calls = {}

    monkeypatch.setattr(
        pipeline_dispatch,
        "_active",
        lambda _stage, _default: "sortformer",
    )

    def fake_lite(audio_path, segments, n_speakers=None, calendar_context=None):
        calls.update(
            audio_path=audio_path,
            segments=segments,
            n_speakers=n_speakers,
            calendar_context=calendar_context,
        )
        return {"backend": "lite"}

    monkeypatch.setattr("shared.diarize_lite.diarize", fake_lite)

    context = object()
    result = pipeline_dispatch.diarize(
        "/tmp/meeting.mp3",
        [{"start": 0, "end": 1, "text": "hello"}],
        n_speakers=2,
        calendar_context=context,
    )

    assert result == {"backend": "lite"}
    assert calls == {
        "audio_path": "/tmp/meeting.mp3",
        "segments": [{"start": 0, "end": 1, "text": "hello"}],
        "n_speakers": 2,
        "calendar_context": context,
    }
