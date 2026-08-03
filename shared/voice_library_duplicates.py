"""Find people enrolled twice, across both voice libraries at once.

There are two stores. The Voice Library window lists the live matching library
(`~/HiDock/Voice Library/embeddings.json`); automatic naming reads whichever
candidate library has been promoted (`Voice Library Candidates/<model>/…`). They
are kept roughly in step by `voice_library_sync`, but they drift, and the drift is
invisible: on 2026-08-03 the matching library held `Ian Wedgewood` while the
naming library held a bare `Ian` with the *same two source meetings* — one person,
two names, in two stores, and no screen in the app where that could be seen.

Duplicates are not cosmetic. Naming requires the best match to lead the runner-up
by a margin (`shared.speaker_match_policy`), so a person enrolled twice competes
with themselves and can suppress their own match entirely. Rec01 speaker 1 scored
0.822 as "Adam" and 0.807 as "Adam Gardner" and was therefore named nothing.

This module only *reports*. Merging stays an explicit human decision, because the
evidence is suggestive rather than conclusive and the failure mode — attributing
one person's words to another — is worse than leaving a duplicate in place. The
2026-08-03 pass is the case for that caution: of six same-first-name pairs, four
were one person and two were genuinely different people who happened to share a
first name (`Ian` was not Ian Reay; `John` was not John Henderson).
"""
from __future__ import annotations

import itertools
import json
from pathlib import Path

from shared.speaker_match_policy import alias_compatible

#: Mean cosine at or above which two entries are reported as the same person.
#: Below this they are reported as "unclear" rather than dropped, because a name
#: collision the voices disagree about is exactly what a human needs to see.
SAME_PERSON_MEAN = 0.60
#: Ratio of cross-similarity to the entry's own self-consistency. A pair whose
#: cross-similarity approaches how similar an entry is to *itself* is one voice.
#: This is what separated Adam/Adam Gardner (0.752 vs 0.789 self) from
#: Ian/Ian Reay (0.300 vs 0.685 self).
SAME_PERSON_SELF_RATIO = 0.80


def _vectors(entry: dict) -> list:
    import numpy as np

    out = []
    for sample in entry.get("samples") or ():
        if sample.get("active") is False:
            continue
        embedding = sample.get("embedding")
        if embedding:
            out.append(np.asarray(embedding, dtype=float))
    return out


def _cosine(a, b) -> float:
    import numpy as np

    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator <= 0:
        return -1.0
    return float(a @ b / denominator)


def _self_consistency(vectors: list) -> float | None:
    if len(vectors) < 2:
        return None
    pairs = [_cosine(a, b) for a, b in itertools.combinations(vectors, 2)]
    return sum(pairs) / len(pairs)


def _compare(a_vectors: list, b_vectors: list) -> tuple[float, float] | None:
    if not a_vectors or not b_vectors:
        return None
    scores = [_cosine(x, y) for x in a_vectors for y in b_vectors]
    return max(scores), sum(scores) / len(scores)


def _source_meetings(entry: dict) -> set[str]:
    out = set()
    for sample in entry.get("samples") or ():
        source = sample.get("source_file") or sample.get("audio_file")
        if source:
            out.add(Path(str(source)).name)
    return out


def _speakers(path: Path) -> dict:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    speakers = data.get("speakers")
    return speakers if isinstance(speakers, dict) else {}


def _verdict(mean: float, self_a: float | None, self_b: float | None) -> str:
    """Same person, different people, or not enough evidence to say.

    Judged against the entries' own self-consistency rather than a bare
    threshold: cosine scales differ per embedding model, so "how close are these
    two compared with how close each is to itself" travels between models in a
    way that a fixed number does not.
    """
    reference = max([s for s in (self_a, self_b) if s is not None], default=None)
    if reference is None:
        # A single sample on both sides. The absolute score is all there is.
        return "same" if mean >= SAME_PERSON_MEAN else "unclear"
    if mean >= reference * SAME_PERSON_SELF_RATIO:
        return "same"
    if mean < reference * 0.6:
        return "different"
    return "unclear"


def find_duplicates(
    matching_library: str | Path,
    candidate_library: str | Path | None = None,
) -> list[dict]:
    """Candidate duplicate pairs across both libraries, strongest evidence first.

    A pair is considered when the names are alias-compatible (one is a bare first
    name extending into the other) or identical across the two stores. Every pair
    carries its measured evidence so the UI can show *why*, and a `verdict` of
    "same" / "unclear" / "different" so a user is never asked to merge on a
    name coincidence alone.
    """
    matching = _speakers(Path(matching_library))
    candidate = _speakers(Path(candidate_library)) if candidate_library else {}

    # One view of every person, remembering which store each spelling came from.
    stores: dict[str, set[str]] = {}
    for name in matching:
        stores.setdefault(name, set()).add("matching")
    for name in candidate:
        stores.setdefault(name, set()).add("naming")

    def entry_for(name: str) -> dict:
        # Prefer whichever store has more samples to compare with.
        options = [lib.get(name) for lib in (candidate, matching) if lib.get(name)]
        return max(options, key=lambda e: len(e.get("samples") or ()), default={})

    rows: list[dict] = []
    for a, b in itertools.combinations(sorted(stores), 2):
        if not alias_compatible(a, b):
            continue
        entry_a, entry_b = entry_for(a), entry_for(b)
        vectors_a, vectors_b = _vectors(entry_a), _vectors(entry_b)
        compared = _compare(vectors_a, vectors_b)
        shared_meetings = sorted(_source_meetings(entry_a) & _source_meetings(entry_b))
        self_a = _self_consistency(vectors_a)
        self_b = _self_consistency(vectors_b)

        if compared is None:
            # No embeddings to compare. Shared source meetings are still hard
            # evidence — the same clip cannot be two people.
            verdict = "same" if shared_meetings else "unclear"
            best = mean = None
        else:
            best, mean = compared
            verdict = "same" if shared_meetings else _verdict(mean, self_a, self_b)

        rows.append({
            "names": [a, b],
            # The fuller spelling is the one worth keeping.
            "suggested_keep": b if len(b.split()) > len(a.split()) else a,
            "verdict": verdict,
            "max_similarity": None if best is None else round(best, 4),
            "mean_similarity": None if mean is None else round(mean, 4),
            "self_consistency": {
                a: None if self_a is None else round(self_a, 4),
                b: None if self_b is None else round(self_b, 4),
            },
            "shared_meetings": shared_meetings,
            "sample_counts": {a: len(vectors_a), b: len(vectors_b)},
            "stores": {a: sorted(stores[a]), b: sorted(stores[b])},
        })

    order = {"same": 0, "unclear": 1, "different": 2}
    rows.sort(key=lambda r: (order.get(r["verdict"], 3), -(r["mean_similarity"] or 0)))
    return rows


def find_drift(
    matching_library: str | Path,
    candidate_library: str | Path,
) -> dict:
    """People present in one store but not the other.

    `naming_only` people cannot be seen or edited in the Voice Library window;
    `matching_only` people can never be named automatically, because the library
    that does the naming has never heard of them. Both are silent failures worth
    a screen of their own.
    """
    matching = set(_speakers(Path(matching_library)))
    candidate = set(_speakers(Path(candidate_library)))
    return {
        "matching_only": sorted(matching - candidate),
        "naming_only": sorted(candidate - matching),
        "matching_count": len(matching),
        "naming_count": len(candidate),
    }
