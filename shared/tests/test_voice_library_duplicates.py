"""Duplicate detection must reproduce the 2026-08-03 hand judgements.

Six same-first-name pairs were examined by hand that day. Four were one person
enrolled twice; two were different people who happened to share a first name.
Getting the second group wrong would attribute one person's words to another, so
those cases are the point of this suite, not an afterthought.

The similarity numbers below are the real measured values from that pass.
"""
import json

from shared.voice_library_duplicates import find_drift, find_duplicates


def _entry(vectors, sources=None, active=True):
    samples = []
    for index, vector in enumerate(vectors):
        samples.append({
            "embedding": list(vector),
            "active": active,
            "source_file": (sources or [])[index] if sources and index < len(sources) else None,
        })
    return {"samples": samples, "embedding_dim": len(vectors[0]) if vectors else 0}


def _library(tmp_path, name, speakers):
    path = tmp_path / name
    path.write_text(json.dumps({"speakers": speakers}), encoding="utf-8")
    return path


# Three mutually distinguishable voices, plus a near-twin of the first.
ALICE = [1.0, 0.0, 0.0]
ALICE_AGAIN = [0.96, 0.28, 0.0]
BOB = [0.0, 1.0, 0.0]
CAROL = [0.0, 0.0, 1.0]


class TestSamePersonEnrolledTwice:

    def test_a_bare_first_name_matching_a_full_name_is_reported(self, tmp_path):
        lib = _library(tmp_path, "main.json", {
            "Adam": _entry([ALICE, ALICE]),
            "Adam Gardner": _entry([ALICE_AGAIN, ALICE]),
        })
        rows = find_duplicates(lib)
        assert len(rows) == 1
        assert rows[0]["verdict"] == "same"
        assert set(rows[0]["names"]) == {"Adam", "Adam Gardner"}

    def test_the_fuller_spelling_is_the_one_suggested_to_keep(self, tmp_path):
        lib = _library(tmp_path, "main.json", {
            "Adam": _entry([ALICE, ALICE]),
            "Adam Gardner": _entry([ALICE_AGAIN, ALICE]),
        })
        assert find_duplicates(lib)[0]["suggested_keep"] == "Adam Gardner"

    def test_a_shared_source_meeting_settles_it_regardless_of_cosine(self, tmp_path):
        """The Ian Wedgewood case: same two clips, two names, two stores.

        The same recorded clip cannot belong to two people, so shared provenance
        outranks the embeddings.
        """
        main = _library(tmp_path, "main.json", {
            "Ian Wedgewood": _entry([BOB], sources=["HiD20_diarized.json"]),
        })
        naming = _library(tmp_path, "naming.json", {
            "Ian": _entry([CAROL], sources=["HiD20_diarized.json"]),
        })
        rows = find_duplicates(main, naming)
        assert rows[0]["verdict"] == "same"
        assert rows[0]["shared_meetings"] == ["HiD20_diarized.json"]

    def test_evidence_is_reported_so_the_user_can_see_why(self, tmp_path):
        lib = _library(tmp_path, "main.json", {
            "Adam": _entry([ALICE, ALICE]),
            "Adam Gardner": _entry([ALICE_AGAIN, ALICE]),
        })
        row = find_duplicates(lib)[0]
        assert row["mean_similarity"] is not None
        assert row["sample_counts"] == {"Adam": 2, "Adam Gardner": 2}
        assert row["stores"]["Adam"] == ["matching"]


class TestDifferentPeopleWhoShareAFirstName:
    """The cases that must NOT be presented as a confident merge."""

    def test_a_namesake_the_voices_disagree_about_is_marked_different(self, tmp_path):
        # The Ian case: 'Ian' self-consistent at 0.685, but only 0.300 to Ian Reay.
        lib = _library(tmp_path, "main.json", {
            "Ian": _entry([ALICE, ALICE_AGAIN]),
            "Ian Reay": _entry([BOB, BOB]),
        })
        rows = find_duplicates(lib)
        assert rows[0]["verdict"] == "different"

    def test_two_full_names_sharing_a_first_name_are_never_paired(self, tmp_path):
        """Adam Gardner and Adam Prior are two people, whatever they score."""
        lib = _library(tmp_path, "main.json", {
            "Adam Gardner": _entry([ALICE]),
            "Adam Prior": _entry([ALICE]),
        })
        assert find_duplicates(lib) == []

    def test_unrelated_names_are_never_paired(self, tmp_path):
        lib = _library(tmp_path, "main.json", {
            "Alice Smith": _entry([ALICE]),
            "Bob Jones": _entry([ALICE]),
        })
        assert find_duplicates(lib) == []

    def test_same_is_ordered_before_unclear_and_different(self, tmp_path):
        lib = _library(tmp_path, "main.json", {
            "Adam": _entry([ALICE, ALICE]),
            "Adam Gardner": _entry([ALICE_AGAIN, ALICE]),
            "Ian": _entry([BOB, BOB]),
            "Ian Reay": _entry([CAROL, CAROL]),
        })
        verdicts = [r["verdict"] for r in find_duplicates(lib)]
        assert verdicts.index("same") < verdicts.index("different")


class TestMissingEvidence:

    def test_no_embeddings_and_no_shared_meeting_is_unclear_not_same(self, tmp_path):
        lib = _library(tmp_path, "main.json", {
            "Adam": {"samples": [{"source_file": "a.json"}]},
            "Adam Gardner": {"samples": [{"source_file": "b.json"}]},
        })
        assert find_duplicates(lib)[0]["verdict"] == "unclear"

    def test_archived_samples_are_ignored(self, tmp_path):
        lib = _library(tmp_path, "main.json", {
            "Adam": _entry([ALICE], active=False),
            "Adam Gardner": _entry([ALICE]),
        })
        assert find_duplicates(lib)[0]["sample_counts"]["Adam"] == 0

    def test_a_missing_library_file_is_not_an_error(self, tmp_path):
        assert find_duplicates(tmp_path / "nope.json") == []


class TestDrift:
    """People stranded in one store are silent failures worth surfacing."""

    def test_naming_only_people_are_invisible_in_the_ui(self, tmp_path):
        main = _library(tmp_path, "main.json", {"Alice": _entry([ALICE])})
        naming = _library(tmp_path, "naming.json", {
            "Alice": _entry([ALICE]), "Sean Denton": _entry([BOB]),
        })
        assert find_drift(main, naming)["naming_only"] == ["Sean Denton"]

    def test_matching_only_people_can_never_be_auto_named(self, tmp_path):
        main = _library(tmp_path, "main.json", {
            "Alice": _entry([ALICE]), "Johan Nystrom": _entry([CAROL]),
        })
        naming = _library(tmp_path, "naming.json", {"Alice": _entry([ALICE])})
        assert find_drift(main, naming)["matching_only"] == ["Johan Nystrom"]

    def test_counts_are_reported_for_both_stores(self, tmp_path):
        main = _library(tmp_path, "main.json", {"Alice": _entry([ALICE])})
        naming = _library(tmp_path, "naming.json", {
            "Alice": _entry([ALICE]), "Bob": _entry([BOB]),
        })
        drift = find_drift(main, naming)
        assert (drift["matching_count"], drift["naming_count"]) == (1, 2)
