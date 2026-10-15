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


def _nemotron_refused(monkeypatch):
    monkeypatch.setattr(pipeline_dispatch, "_active", lambda _stage, _default: "nemotron")

    def refused(*_args, **_kwargs):
        raise RuntimeError('nemotron sidecar HTTP 401: {"detail":"missing or invalid X-Auth-Token"}')

    monkeypatch.setattr("shared.diarize_nemotron.diarize", refused)


def test_refused_nemotron_falls_back_to_sortformer_and_says_so(monkeypatch):
    """After a migration the Keychain token is missing; that used to leave a
    whole meeting under one speaker instead of detecting speakers locally."""
    _nemotron_refused(monkeypatch)
    monkeypatch.setattr(pipeline_dispatch, "_local_fallback_backends", lambda: ["sortformer", "lite"])
    monkeypatch.setattr("shared.diarize_sortformer.diarize",
                        lambda *a, **k: {"segments": [], "backend": "sortformer"})
    result = pipeline_dispatch.diarize("/tmp/m.mp3", [])
    assert result["backend"] == "sortformer"
    assert result["diarization_fallback"]["wanted"] == "nemotron"
    assert "401" in result["diarization_fallback"]["reason"]


def test_fallback_moves_on_to_lite_when_sortformer_fails(monkeypatch):
    _nemotron_refused(monkeypatch)
    monkeypatch.setattr(pipeline_dispatch, "_local_fallback_backends", lambda: ["sortformer", "lite"])

    def broken(*_a, **_k):
        raise RuntimeError("weights not downloaded")

    monkeypatch.setattr("shared.diarize_sortformer.diarize", broken)
    monkeypatch.setattr("shared.diarize_lite.diarize", lambda *a, **k: {"segments": [], "backend": "lite"})
    result = pipeline_dispatch.diarize("/tmp/m.mp3", [])
    assert result["diarization_fallback"]["used"] == "lite"
