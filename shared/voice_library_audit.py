"""Find and set aside voice-library samples saved under the wrong person.

Confirming a speaker saves one clip of their voice. Until 2026-10-12 that clip
was never checked, so a speaker that had absorbed someone else's lines could
teach the library the wrong voice under their name. The audit on that date
found 37 of 1,012 active samples that sound clearly more like another person
(e.g. a Chris Wildsmith sample matching Cedric Hummel at 1.00 — the same clip
saved under both names).

Each active sample is compared with every person's samples *from other
recordings* (median of the top three matches, as the naming scorer does). A
sample is flagged when another person beats its own name by at least
`MARGIN`. Setting aside only marks the sample inactive and records why; it is
undone with `restore`, and nothing is deleted.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

MARGIN = 0.15
QUARANTINE_KEY = "quarantined"


def _stem(sample: dict) -> str:
    audio = str(sample.get("audio_file") or "")
    if audio:
        return Path(audio).stem
    return str(sample.get("source_file") or sample.get("id") or "")


def audit(library: dict, *, margin: float = MARGIN) -> list[dict]:
    """Samples that sound more like someone else than their own person."""
    rows = []
    for name, entry in (library.get("speakers") or {}).items():
        for index, sample in enumerate(entry.get("samples") or []):
            vector = sample.get("embedding")
            if sample.get("active") is False or not isinstance(vector, list) or not vector:
                continue
            v = np.asarray(vector, dtype=np.float32)
            norm = float(np.linalg.norm(v))
            if norm <= 1e-10:
                continue
            rows.append((name, index, _stem(sample), v / norm))
    if not rows:
        return []
    dims = {len(r[3]) for r in rows}
    if len(dims) != 1:
        raise ValueError(f"library mixes embedding sizes {sorted(dims)}; audit one model at a time")
    names = np.array([r[0] for r in rows])
    stems = np.array([r[2] for r in rows])
    matrix = np.stack([r[3] for r in rows])
    sims = matrix @ matrix.T
    people = {p: np.where(names == p)[0] for p in set(names)}

    flagged = []
    for k, (name, index, stem, _) in enumerate(rows):
        def score(person: str) -> float | None:
            others = [j for j in people[person] if stems[j] != stem]
            if not others:
                return None
            top = np.sort(sims[k, others])[::-1][:3]
            return float(np.median(top))
        own = score(name)
        if own is None:
            continue
        best = max(((s, p) for p in people if p != name for s in [score(p)] if s is not None),
                   default=None)
        if best and best[0] - own >= margin:
            flagged.append({"name": name, "index": index, "recording": stem,
                            "sounds_like": best[1], "score": round(best[0], 3), "own_score": round(own, 3)})
    return sorted(flagged, key=lambda f: f["own_score"] - f["score"])


def set_aside(library: dict, flagged: list[dict]) -> int:
    """Mark flagged samples inactive, recording why."""
    when = datetime.now(timezone.utc).isoformat()
    count = 0
    for flag in flagged:
        sample = library["speakers"][flag["name"]]["samples"][flag["index"]]
        if sample.get("active") is False:
            continue
        sample["active"] = False
        sample[QUARANTINE_KEY] = {"at": when, "sounds_like": flag["sounds_like"],
                                  "score": flag["score"], "own_score": flag["own_score"]}
        count += 1
    return count


def restore(library: dict) -> int:
    """Undo every set-aside made by the audit."""
    count = 0
    for entry in (library.get("speakers") or {}).values():
        for sample in entry.get("samples") or []:
            if sample.pop(QUARANTINE_KEY, None) is not None:
                sample["active"] = True
                count += 1
    return count


def backup(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    copy = path.with_name(f"{path.name}.before-audit-{stamp}")
    shutil.copy2(path, copy)
    return copy
