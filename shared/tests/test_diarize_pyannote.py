"""Cover the pyannote backend's plumbing, which is testable without the model.

The models are gated on Hugging Face, so the diarization itself cannot run in CI
or on a machine without an accepted licence and a token. What *can* be pinned is
everything around it: how a gated failure is reported, how turns are converted,
and that the dispatcher routes correctly. Those are the parts that would
otherwise fail silently or confusingly.
"""

import sys
import types

import pytest

from shared import diarize_pyannote as backend


class _Segment:
    def __init__(self, start, end):
        self.start = start
        self.end = end


class _Annotation:
    """Minimal stand-in for pyannote's Annotation.itertracks(yield_label=True)."""

    def __init__(self, rows):
        self._rows = rows

    def itertracks(self, yield_label=False):
        for start, end, label in self._rows:
            yield _Segment(start, end), "_", label


def test_turn_conversion_sorts_and_drops_empty_spans():
    annotation = _Annotation([
        (10.0, 20.0, "SPEAKER_01"),
        (0.0, 5.0, "SPEAKER_00"),
        (30.0, 30.0, "SPEAKER_02"),   # zero-length, must be dropped
    ])
    assert backend._turns_from_annotation(annotation) == [
        (0.0, 5.0, "SPEAKER_00"),
        (10.0, 20.0, "SPEAKER_01"),
    ]


def test_turn_conversion_unwraps_a_diarize_output():
    """pyannote 4 may return a DiarizeOutput wrapping the annotation."""
    inner = _Annotation([(0.0, 5.0, "SPEAKER_00")])
    wrapper = types.SimpleNamespace(speaker_diarization=inner)
    assert backend._turns_from_annotation(wrapper) == [(0.0, 5.0, "SPEAKER_00")]


def test_newest_model_is_preferred_over_the_older_one():
    models = backend.available_models()
    assert models[0] == "pyannote/speaker-diarization-community-1"
    assert "pyannote/speaker-diarization-3.1" in models
    # community-1 supersedes 3.1 on speaker counting, the metric this pipeline
    # measured worst on, so it must be tried first.
    assert models.index("pyannote/speaker-diarization-community-1") < models.index(
        "pyannote/speaker-diarization-3.1"
    )


def test_token_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_example")
    backend._pipeline_cache.clear()
    assert backend._hf_token() == "hf_example"


def test_token_falls_back_to_the_alternate_env_names(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("HUGGING_FACE_HUB_TOKEN", "hf_other")
    assert backend._hf_token() == "hf_other"


def test_a_gated_model_returning_none_is_not_mistaken_for_success(monkeypatch, capsys):
    """`from_pretrained` returns None for a gated model without access.

    It does not raise, which is easy to read as a successful load — and would
    then fail much later with an unhelpful AttributeError.
    """
    fake = types.ModuleType("pyannote.audio")
    fake.Pipeline = types.SimpleNamespace(from_pretrained=lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, "pyannote.audio", fake)
    backend._pipeline_cache.clear()

    assert backend.load_pipeline("pyannote/speaker-diarization-community-1") is None
    message = capsys.readouterr().err
    assert "gated" in message
    # The remedy must be actionable rather than a bare failure.
    assert "HF_TOKEN" in message


def test_load_falls_through_to_the_next_candidate(monkeypatch):
    attempted = []

    def from_pretrained(checkpoint, **_kwargs):
        attempted.append(checkpoint)
        if checkpoint == "pyannote/speaker-diarization-3.1":
            return "a-pipeline"
        raise RuntimeError("gated")

    fake = types.ModuleType("pyannote.audio")
    fake.Pipeline = types.SimpleNamespace(from_pretrained=from_pretrained)
    monkeypatch.setitem(sys.modules, "pyannote.audio", fake)
    backend._pipeline_cache.clear()
    try:
        assert backend.load_pipeline() == "a-pipeline"
        assert attempted == [
            "pyannote/speaker-diarization-community-1",
            "pyannote/speaker-diarization-3.1",
        ]
    finally:
        backend._pipeline_cache.clear()


def test_diarize_raises_a_useful_error_when_no_model_is_available(monkeypatch):
    monkeypatch.setattr(backend, "load_pipeline", lambda *a, **k: None)
    with pytest.raises(RuntimeError) as excinfo:
        backend.diarize("/nope.mp3", [])
    message = str(excinfo.value)
    # A transcription failing on a licence gate must say so, not surface as a
    # generic diarization error.
    assert "huggingface.co" in message
    assert "HF_TOKEN" in message


def test_dispatcher_routes_to_pyannote_when_selected(monkeypatch):
    import shared.pipeline_dispatch as dispatch

    monkeypatch.setattr(dispatch, "_active", lambda stage, default: "pyannote")
    called = {}

    fake = types.ModuleType("shared.diarize_pyannote")

    def fake_diarize(audio_path, segments, n_speakers=None, calendar_context=None, **kw):
        called["args"] = (audio_path, n_speakers, kw)
        return {"backend": "pyannote", "segments": []}

    fake.diarize = fake_diarize
    monkeypatch.setitem(sys.modules, "shared.diarize_pyannote", fake)

    result = dispatch.diarize("/a.mp3", [], n_speakers=4, two_sided_partition=True)
    assert result["backend"] == "pyannote"
    # Sortformer-only refinements are forwarded and absorbed, not rejected, so a
    # caller opting into them cannot crash on a different backend.
    assert called["args"][1] == 4
    assert "two_sided_partition" in called["args"][2]


def test_sortformer_only_options_are_absorbed_by_the_signature():
    import inspect

    signature = inspect.signature(backend.diarize)
    # A **kwargs sink is what lets the dispatcher stay simple.
    assert any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
