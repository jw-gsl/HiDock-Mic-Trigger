"""Merging must not let a fragment donate its identity to a real speaker.

Identity (name, confidence, persisted embedding) used to be resolved once
*before* the count reconciliation, and every merge inherited whichever label
survived — which was the earliest by first appearance. A four-second fragment
could therefore absorb a six-minute speaker and overwrite their auto-matched
name and their stored embedding. That embedding is not cosmetic: `rematch`
re-identifies from it without touching audio, so a bad vector poisons every
later pass.
"""

import numpy as np
import pytest

from shared.diarize_sortformer import (
    _label_groups,
    _label_speech_seconds,
    _merge_labels_to_count,
    _pooled_embedding,
    _repool_merged_speakers,
)


def _vec(*values):
    return np.asarray(values, dtype=np.float32)


JEEVAN = _vec(1.0, 0.0, 0.0)
JEEVAN_ISH = _vec(0.999, 0.01, 0.0)
OTHER = _vec(0.0, 1.0, 0.0)


# --- survivor selection ------------------------------------------------------

def test_longest_speaker_survives_a_merge_not_the_earliest():
    # The exact reported defect: a 4 s fragment appears first, a 390 s speaker
    # second, and they are the same voice.
    turns = [
        (0.0, 4.0, "Speaker 1"),        # fragment
        (10.0, 400.0, "Speaker 2"),     # the real speaker
        (410.0, 500.0, "Speaker 3"),    # someone else
    ]
    embeddings = {"Speaker 1": JEEVAN, "Speaker 2": JEEVAN_ISH, "Speaker 3": OTHER}
    out = _merge_labels_to_count(turns, embeddings, 2)
    assert [label for _, _, label in out] == ["Speaker 2", "Speaker 2", "Speaker 3"]


def test_ties_keep_the_earliest_label_for_determinism():
    turns = [(0.0, 10.0, "A"), (10.0, 20.0, "B"), (20.0, 30.0, "C")]
    embeddings = {"A": JEEVAN, "B": JEEVAN_ISH, "C": OTHER}
    out = _merge_labels_to_count(turns, embeddings, 2)
    assert [label for _, _, label in out] == ["A", "A", "C"]


def test_survivor_is_chosen_per_cluster_not_globally():
    turns = [
        (0.0, 5.0, "A"), (5.0, 100.0, "B"),      # cluster one → B
        (100.0, 105.0, "C"), (105.0, 300.0, "D"),  # cluster two → D
    ]
    embeddings = {"A": JEEVAN, "B": JEEVAN_ISH, "C": OTHER, "D": _vec(0.01, 0.999, 0.0)}
    out = _merge_labels_to_count(turns, embeddings, 2)
    assert [label for _, _, label in out] == ["B", "B", "D", "D"]


# --- helpers -----------------------------------------------------------------

def test_speech_seconds_sums_per_label():
    turns = [(0.0, 4.0, "A"), (10.0, 20.0, "B"), (30.0, 33.0, "A")]
    assert _label_speech_seconds(turns) == {"A": 7.0, "B": 10.0}


def test_label_groups_maps_absorbed_members_to_survivor():
    before = [(0, 4, "A"), (4, 400, "B"), (400, 500, "C")]
    after = [(0, 4, "B"), (4, 400, "B"), (400, 500, "C")]
    assert _label_groups(before, after) == {"B": ["A", "B"], "C": ["C"]}


def test_pooled_embedding_is_duration_weighted():
    info = {
        "frag": {"embedding": [0.0, 1.0]},
        "real": {"embedding": [1.0, 0.0]},
    }
    speech = {"frag": 4.0, "real": 396.0}
    pooled = _pooled_embedding(["frag", "real"], info, speech)
    # Dominated by the 396 s voice, and unit length.
    assert pooled[0] > 0.99
    assert pooled[1] < 0.11
    assert pytest.approx(1.0, abs=1e-6) == float(np.linalg.norm(pooled))


def test_pooled_embedding_skips_members_without_one():
    info = {"a": {"embedding": [1.0, 0.0]}, "b": {"embedding": None}}
    assert _pooled_embedding(["a", "b"], info, {"a": 10.0, "b": 5.0}) == [1.0, 0.0]


def test_pooled_embedding_is_none_when_nothing_usable():
    info = {"a": {"embedding": None}}
    assert _pooled_embedding(["a"], info, {"a": 10.0}) is None


def test_pooled_embedding_rejects_mismatched_dimensions():
    info = {"a": {"embedding": [1.0, 0.0]}, "b": {"embedding": [1.0, 0.0, 0.0]}}
    assert _pooled_embedding(["a", "b"], info, {"a": 1.0, "b": 1.0}) is None


# --- re-pooling identity -----------------------------------------------------

@pytest.fixture
def no_library(monkeypatch):
    """Simulate an unavailable voice library so the fallback path is exercised."""
    import shared.voice_library_lite as lib

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("library offline")

    monkeypatch.setattr(lib, "identify_speaker", unavailable)


@pytest.fixture
def library_says(monkeypatch):
    """Stub the library's verdict, and whether it had anything to compare.

    `library_scores` returns an empty list both when the library is empty and
    when the embedding dimension does not match its model, so the re-pooler uses
    it to tell "no confident match" apart from "cannot tell".
    """
    def install(name, confidence, *, comparable=True):
        import shared.voice_library_lite as lib
        monkeypatch.setattr(
            lib, "identify_speaker", lambda *_a, **_k: (name, confidence),
        )
        monkeypatch.setattr(
            lib, "library_scores",
            lambda *_a, **_k: [("Someone", 0.4)] if comparable else [],
        )
    return install


def _merged_case():
    """A 4 s fragment ('Speaker 1') absorbed into a 390 s speaker."""
    before = [(0.0, 4.0, "Speaker 1"), (10.0, 400.0, "Speaker 2")]
    after = [(0.0, 4.0, "Speaker 2"), (10.0, 400.0, "Speaker 2")]
    info = {
        "Speaker 1": {
            "name": "Speaker 1", "source": "generic",
            "confidence": None, "embedding": [0.0, 1.0],
        },
        "Speaker 2": {
            "name": "Jeevan", "source": "auto",
            "confidence": 0.82, "embedding": [1.0, 0.0],
        },
    }
    return info, before, after


def test_repool_keeps_the_best_evidenced_identity_without_a_library(no_library):
    info, before, after = _merged_case()
    out = _repool_merged_speakers(info, before, after, ["Speaker 2"])
    # Jeevan's match survives the merge, and the stored embedding is pooled
    # (duration-weighted), not the fragment's.
    assert out["Speaker 2"]["name"] == "Jeevan"
    assert out["Speaker 2"]["source"] == "auto"
    assert out["Speaker 2"]["embedding"][0] > 0.99


def test_repool_rematches_on_the_pooled_vector(library_says):
    library_says("Jeevan Kumar", 0.91)
    info, before, after = _merged_case()
    out = _repool_merged_speakers(info, before, after, ["Speaker 2"])
    assert out["Speaker 2"]["name"] == "Jeevan Kumar"
    assert out["Speaker 2"]["confidence"] == 0.91
    assert out["Speaker 2"]["source"] == "auto"


def test_repool_demotes_when_the_pooled_voice_no_longer_matches(library_says):
    # Forcing two different people together should not keep asserting one's name.
    library_says(None, 0.0)
    info, before, after = _merged_case()
    out = _repool_merged_speakers(info, before, after, ["Speaker 2"])
    assert out["Speaker 2"]["source"] == "generic"
    assert out["Speaker 2"]["name"] == "Speaker 2"
    assert out["Speaker 2"]["confidence"] is None


def test_repool_leaves_untouched_labels_alone(library_says):
    library_says("Someone Else", 0.99)
    info, before, after = _merged_case()
    info["Speaker 3"] = {
        "name": "Alice", "source": "auto", "confidence": 0.7, "embedding": [0.0, 1.0],
    }
    before = before + [(400.0, 500.0, "Speaker 3")]
    after = after + [(400.0, 500.0, "Speaker 3")]
    out = _repool_merged_speakers(info, before, after, ["Speaker 2", "Speaker 3"])
    # Speaker 3 absorbed nobody, so it must not be re-matched or renamed.
    assert out["Speaker 3"] == info["Speaker 3"]


def test_repool_is_a_noop_when_nothing_merged(library_says):
    library_says("Should Not Be Used", 0.99)
    info = {"A": {"name": "Alice", "source": "auto", "confidence": 0.8, "embedding": [1.0, 0.0]}}
    turns = [(0.0, 10.0, "A")]
    assert _repool_merged_speakers(info, turns, turns, ["A"]) is info


def test_repool_generic_result_uses_the_surviving_label_name(no_library):
    # Both members generic: the result must be named for the label that actually
    # exists downstream, not for the absorbed one.
    before = [(0.0, 4.0, "Speaker 1"), (10.0, 400.0, "Speaker 2")]
    after = [(0.0, 4.0, "Speaker 2"), (10.0, 400.0, "Speaker 2")]
    info = {
        "Speaker 1": {"name": "Speaker 1", "source": "generic", "confidence": None,
                      "embedding": [0.0, 1.0]},
        "Speaker 2": {"name": "Speaker 2", "source": "generic", "confidence": None,
                      "embedding": [1.0, 0.0]},
    }
    out = _repool_merged_speakers(info, before, after, ["Speaker 2"])
    assert out["Speaker 2"]["name"] == "Speaker 2"


def test_repool_keeps_a_match_when_the_library_cannot_compare(library_says):
    """An empty library, or an embedding from a different model, returns zero
    scores. That is not evidence against an existing match, so the name must
    survive — otherwise switching embedding model silently wipes auto-tags."""
    library_says(None, 0.0, comparable=False)
    info, before, after = _merged_case()
    out = _repool_merged_speakers(info, before, after, ["Speaker 2"])
    assert out["Speaker 2"]["name"] == "Jeevan"
    assert out["Speaker 2"]["source"] == "auto"
    assert out["Speaker 2"]["confidence"] == 0.82
