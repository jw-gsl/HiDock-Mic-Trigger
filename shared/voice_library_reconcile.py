"""Move voice-library samples to the person the transcript says is speaking.

Every diarized sample records the recording and the time window its clip came
from. The transcript for that recording is the record of who spoke when, and
it gets corrected after the sample is saved: renamed speakers, re-diarised
recordings, and the 2025 transcripts rebuilt from the human-verified archive on
2026-07-22. The library was never re-checked against those corrections.

On 2026-10-15, 231 of 1,441 live-library samples were filed under someone
other than the person speaking in their window, 185 of them from one backfill
on 2026-07-20 that shifted names: 59 of Rebecca Nemaric's 62 samples were Joe
Kraft, 56 of Ian Reay's were Chris Wildsmith, 50 of James Whiting's were Ian
Reay. The acoustic audit (`voice_library_audit`) cannot catch this: once most of
a person's samples are someone else, that someone else *is* their voice.

A sample is moved when the transcript gives its window to a different named
person whose label is trusted by the backfill policy (typed by the user, or
from the human-verified archive). An automatic match is not trusted — it came
from this library — so its sample is set aside instead (inactive, reversible with `voice_library_audit.restore`). Short forms
and typos of the same name ("Gary" for Gary Francis, "Chris Wildmsith") are the
same person and are left alone.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable

from shared.speaker_meta import backfill_label_source, is_generic_name
from shared.voice_library_audit import QUARANTINE_KEY

# Share of the transcribed speech in a sample's window that must belong to one
# person before the transcript is trusted over the library's label.
MIN_SHARE = 0.6
RELABEL_KEY = "relabelled"
_TYPO_RATIO = 0.9


def _tokens(name: str) -> list[str]:
    return " ".join(str(name or "").split()).casefold().split()


def same_person(a: str, b: str) -> bool:
    """True for the same name, a short form of it, or a near-typo of it."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    if ta == tb:
        return True
    for short, long_ in ((ta, tb), (tb, ta)):
        if len(short) <= len(long_) and all(long_[i].startswith(t) for i, t in enumerate(short)):
            return True
    return ta[0] == tb[0] and SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio() >= _TYPO_RATIO


def resolve_name(library: dict, name: str) -> str | None:
    """The library key for `name`, or None when it is ambiguous.

    An exact (case-insensitive) match wins; otherwise a single library person
    who is the same person by `same_person`; with no match, the name itself
    (a new profile)."""
    speakers = library.get("speakers") or {}
    folded = " ".join(name.split()).casefold()
    for existing in speakers:
        if existing.casefold() == folded:
            return existing
    matches = [existing for existing in speakers if same_person(existing, name)]
    if len(matches) == 1:
        return matches[0]
    return None if matches else " ".join(name.split())


def window_speaker(transcript: dict, start: float, end: float) -> dict | None:
    """Who the transcript says is speaking in [start, end]."""
    names = transcript.get("speaker_names") or {}
    meta = transcript.get("speaker_meta") or {}
    talk: dict[str, float] = {}
    for segment in transcript.get("segments") or []:
        overlap = min(float(segment.get("end", 0)), end) - max(float(segment.get("start", 0)), start)
        if overlap > 0:
            sid = str(segment.get("speaker_id"))
            talk[sid] = talk.get(sid, 0.0) + overlap
    if not talk:
        return None
    sid = max(talk, key=talk.get)
    name = names.get(sid) or ""
    return {
        "speaker_id": sid,
        "name": name,
        "share": talk[sid] / sum(talk.values()),
        "trusted": backfill_label_source(name, meta.get(sid)) is not None,
    }


def _transcript_loader() -> Callable[[str], dict | None]:
    cache: dict[str, dict | None] = {}

    def load(path: str) -> dict | None:
        if path not in cache:
            try:
                cache[path] = json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cache[path] = None
        return cache[path]
    return load


def find_moves(library: dict, load_transcript: Callable[[str], dict | None] | None = None,
               *, min_share: float = MIN_SHARE) -> list[dict]:
    """Samples whose transcript window belongs to someone else."""
    load_transcript = load_transcript or _transcript_loader()
    moves = []
    for name, entry in (library.get("speakers") or {}).items():
        for index, sample in enumerate(entry.get("samples") or []):
            source, start, end = sample.get("source_file"), sample.get("segment_start"), sample.get("segment_end")
            if not source or start is None or end is None:
                continue
            transcript = load_transcript(str(source))
            if not transcript:
                continue
            who = window_speaker(transcript, float(start), float(end))
            if (not who or who["share"] < min_share or is_generic_name(who["name"])
                    or who["name"].strip().casefold() in {"unknown", "unknown speaker"}
                    or same_person(who["name"], name)):
                continue
            target = resolve_name(library, who["name"]) if who["trusted"] else None
            if target is None and QUARANTINE_KEY in sample:
                continue  # already set aside
            moves.append({
                "name": name, "index": index, "id": sample.get("id"),
                "recording": Path(str(sample.get("audio_file") or source)).stem.replace("_diarized", ""),
                "segment_start": start, "transcript_says": who["name"],
                "speaker_id": who["speaker_id"], "share": round(who["share"], 3),
                "trusted": who["trusted"],
                "action": "move" if target else "set_aside",
                "to": target,
            })
    return moves


def _same_clip(a: dict, b: dict) -> bool:
    return (a.get("audio_file"), a.get("segment_start")) == (b.get("audio_file"), b.get("segment_start"))


def apply_moves(library: dict, moves: list[dict]) -> dict:
    """Move or set aside each sample in place; drop profiles left empty."""
    when = datetime.now(timezone.utc).isoformat()
    speakers = library["speakers"]
    counts = {"moved": 0, "set_aside": 0, "duplicates_dropped": 0, "profiles_removed": []}
    # Pop from the end of each list so earlier indices stay valid.
    for move in sorted(moves, key=lambda m: (m["name"], -m["index"])):
        entry = speakers[move["name"]]
        sample = entry["samples"][move["index"]]
        if move["action"] == "set_aside":
            if QUARANTINE_KEY not in sample:
                sample["active"] = False
                sample[QUARANTINE_KEY] = {"at": when, "sounds_like": move["transcript_says"],
                                          "reason": "transcript names someone else (automatic match)"}
                counts["set_aside"] += 1
            continue
        entry["samples"].pop(move["index"])
        entry["last_updated"] = when
        target = speakers.setdefault(move["to"], {
            "enrolled_at": when, "embedding_dim": sample.get("embedding_dim"),
            "model": sample.get("model"), "samples": [],
        })
        if any(_same_clip(sample, other) for other in target.setdefault("samples", [])):
            counts["duplicates_dropped"] += 1
            continue
        # The acoustic audit judged this clip against the wrong name.
        if sample.pop(QUARANTINE_KEY, None) is not None:
            sample["active"] = True
        sample.update({
            "speaker_id": move["speaker_id"], "observed_name": move["transcript_says"],
            RELABEL_KEY: {"at": when, "from": move["name"], "share": move["share"]},
        })
        target["samples"].append(sample)
        target["last_updated"] = when
        counts["moved"] += 1
    for name in [n for n, e in speakers.items() if not e.get("samples")]:
        del speakers[name]
        counts["profiles_removed"].append(name)
    return counts


def reconcile_file(path: Path, *, apply: bool, limit: int | None = None) -> dict:
    """Find (and with `apply`, make) the moves for one library file.

    With `limit`, a run that would touch more samples than that is held back
    for a person: that many at once points at a bulk transcript change."""
    from shared.voice_candidate_review import _atomic_write
    from shared.voice_library_audit import backup

    library = json.loads(path.read_text(encoding="utf-8"))
    moves = find_moves(library)
    result = {"library": str(path), "found": len(moves), "moves": moves}
    if not (apply and moves):
        return result
    if limit is not None and len(moves) > limit:
        result["held_back"] = True
        return result
    result["backup"] = str(backup(path))
    result.update(apply_moves(library, moves))
    _atomic_write(path, library)
    return result
