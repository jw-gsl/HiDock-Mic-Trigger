"""Check every line of a transcript against the saved voices of its people.

Diarisation decides who spoke when by clustering voices within one meeting,
then names each cluster. Two kinds of error survive the reviewer confirming
the names, and neither is visible from the speaker list:

* a whole cluster carries the wrong name (Rec63, 2026-10-09: the speaker
  named Jordan was mostly Chris);
* a cluster swallowed some of another person's lines (Rec63: 13 of Jeff's
  lines sat under Ian).

This compares each line (and each speaker as a whole) with the active
voice-identity library, restricted to the people in this meeting — its
named speakers plus the calendar invitees — and proposes moves where another
of those people clearly fits better. On Rec63, 47 of 59 proposals matched what
the reviewer then did by hand, so they are *proposals*: the app shows them for
the reviewer to accept, it never applies them on its own.

Library samples taken from this same recording are left out, so a speaker the
reviewer confirmed with the wrong lines in it can't vouch for itself.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable

import numpy as np

from shared.speaker_meta import is_generic_name

MIN_LINE_SECONDS = 1.0
_SAMPLE_RATE = 16000


def meeting_people(data: dict, audio_path: Path | None = None) -> list[str]:
    """The names this meeting could plausibly contain."""
    names = {
        str(name).strip()
        for name in (data.get("speaker_names") or {}).values()
        if str(name).strip() and not is_generic_name(str(name))
    }
    context = data.get("calendar_context") or {}
    names.update(str(n).strip() for n in context.get("calendar_candidate_names") or [] if str(n).strip())
    if audio_path is not None:
        try:
            from shared.calendar_context import load_context_for_audio
            linked = load_context_for_audio(audio_path)
            if linked is not None:
                names.update(str(n).strip() for n in linked.candidate_names if str(n).strip())
        except Exception:
            pass
    return sorted(names)


def _restricted_library(library: dict, people: list[str] | None, recording: str | None) -> dict:
    """Only this meeting's people (everyone when None), and none of this
    recording's own samples."""
    wanted = None if people is None else {p.casefold(): p for p in people}
    speakers = {}
    for name, entry in (library.get("speakers") or {}).items():
        if wanted is not None and name.casefold() not in wanted:
            continue
        entry = copy.deepcopy(entry)
        samples = entry.get("samples") or []
        if recording:
            samples = [
                s for s in samples
                if Path(str(s.get("audio_file") or "")).stem != recording
                and Path(str(s.get("source_file") or "")).stem.replace("_diarized", "") != recording
            ]
        entry["samples"] = samples
        if samples:
            speakers[name] = entry
    return {"speakers": speakers}


def _speaker_name(data: dict, speaker_id: Any) -> str:
    return str((data.get("speaker_names") or {}).get(str(speaker_id), f"Speaker {speaker_id}"))


def propose(
    data: dict,
    embed: Callable[[float, float], np.ndarray | None],
    library: dict,
    *,
    scorer: str,
    threshold: float,
    min_margin: float,
    recording: str | None = None,
    people: list[str] | None = None,
    on_progress: Callable[[float], None] | None = None,
    margin_adjust: dict[str, float] | None = None,
) -> dict:
    """Proposed corrections for one diarised transcript.

    `embed(start, end)` returns a voice embedding for that stretch of audio.
    A line is proposed for a move when another of the meeting's people scores
    at least `threshold` and beats the line's current person by `min_margin`
    — the same rule automatic naming uses to call a match.
    """
    from shared.voice_candidate_review import _rank_library

    people = people if people is not None else meeting_people(data)
    sub = _restricted_library(library, people, recording)
    known = sorted(sub["speakers"])
    result: dict[str, Any] = {
        "people": people, "voices_known": known, "lines": [], "speakers": [],
        "checked": 0, "skipped_short": 0,
    }
    meta = data.get("speaker_meta") or {}
    unnamed = {
        sid for sid, name in (data.get("speaker_names") or {}).items()
        if is_generic_name(str(name)) and (meta.get(sid) or {}).get("verified") is not True
    }
    # Line checks compare people within the meeting, so they need two voices.
    # A still-unnamed speaker can be named from anyone saved (what Rematch did).
    check_lines = len(known) >= 2
    if not check_lines and not unnamed:
        result["note"] = ("Need saved voices for at least two of this meeting's people; "
                          f"have {len(known)}.")
        return result
    everyone = _restricted_library(library, None, recording) if unnamed else None

    def ranked(vector: np.ndarray, within: dict | None = None) -> list[tuple[str, float]]:
        return [(r["name"], float(r["score"])) for r in _rank_library(within or sub, vector, scorer)]

    def margin_for(source: str, target: str) -> float:
        # Tuned per pair from the reviewer's apply/skip decisions.
        return min_margin + float((margin_adjust or {}).get(f"{source} → {target}", 0.0))

    def score_of(ranks: list[tuple[str, float]], name: str) -> float:
        return next((s for n, s in ranks if n.casefold() == name.casefold()), 0.0)

    segments = data.get("segments") or []
    by_speaker: dict[str, list[np.ndarray]] = {}
    line_ranks: dict[int, list[tuple[str, float]]] = {}
    for index, seg in enumerate(segments):
        if on_progress:
            on_progress(index / max(1, len(segments)))
        start, end = float(seg.get("start", 0)), float(seg.get("end", 0))
        if end - start < MIN_LINE_SECONDS:
            result["skipped_short"] += 1
            continue
        vector = embed(start, end)
        if vector is None:
            result["skipped_short"] += 1
            continue
        norm = float(np.linalg.norm(vector))
        if norm <= 1e-10:
            continue
        vector = vector / norm
        result["checked"] += 1
        speaker_id = seg.get("speaker_id")
        by_speaker.setdefault(str(speaker_id), []).append(vector)
        if not check_lines:
            continue
        ranks = ranked(vector)
        if not ranks:
            continue
        line_ranks[index] = ranks
        current = _speaker_name(data, speaker_id)
        top, top_score = ranks[0]
        current_score = score_of(ranks, current)
        if top.casefold() != current.casefold() and top_score >= threshold \
                and top_score - current_score >= margin_for(current, top):
            result["lines"].append({
                "index": index, "start": start, "end": end,
                "text": str(seg.get("text") or "").strip(),
                "from_speaker_id": speaker_id, "from_name": current,
                "to_name": top, "score": round(top_score, 3),
                "current_score": round(current_score, 3),
            })

    for speaker_id, vectors in sorted(by_speaker.items()):
        centroid = np.mean(vectors, axis=0)
        centroid = centroid / max(float(np.linalg.norm(centroid)), 1e-10)
        current = _speaker_name(data, speaker_id)
        if speaker_id in unnamed:
            # Naming an unnamed speaker: anyone saved, if one person clearly
            # stands out from the next.
            ranks = ranked(centroid, everyone)
            if not ranks:
                continue
            top, top_score = ranks[0]
            current_score = ranks[1][1] if len(ranks) > 1 else 0.0
        elif check_lines:
            ranks = ranked(centroid)
            if not ranks:
                continue
            top, top_score = ranks[0]
            current_score = score_of(ranks, current)
        else:
            continue
        if top.casefold() != current.casefold() and top_score >= threshold \
                and top_score - current_score >= margin_for(current, top):
            # Renaming the whole speaker must not sweep up the lines that
            # really are the current name. Rec63's "Jordan" was mostly Chris,
            # but four of its lines were Jordan answering when asked; moving
            # them all would have deleted Jordan from the meeting.
            moves, kept = [], []
            for index, seg in enumerate(segments):
                if str(seg.get("speaker_id")) != speaker_id:
                    continue
                own = line_ranks.get(index)
                if own and score_of(own, current) >= threshold \
                        and score_of(own, current) - score_of(own, top) >= min_margin:
                    kept.append(index)
                else:
                    moves.append({"index": index, "start": float(seg.get("start", 0))})
            result["speakers"].append({
                "speaker_id": speaker_id, "name": current, "suggested": top,
                "score": round(top_score, 3), "current_score": round(current_score, 3),
                "lines": len(moves) + len(kept), "moves": moves, "kept": kept,
            })
    return result


def apply_moves(data: dict, moves: list[dict]) -> dict:
    """Move lines to named people the reviewer accepted.

    A name already in the transcript keeps its speaker id; a new one gets the
    next free id. Accepting a proposal is the reviewer's decision, so the
    target is recorded as confirmed by the user.
    """
    names = data.setdefault("speaker_names", {})
    meta = data.setdefault("speaker_meta", {})
    segments = data.get("segments") or []
    by_name = {str(n).casefold(): sid for sid, n in names.items()}
    moved = 0
    missing: list[dict] = []
    for move in moves:
        index, name = int(move["index"]), str(move["to_name"]).strip()
        if not name:
            continue
        # Indices come from the check; an edit in between (a split, a merge)
        # shifts them — on Rec63 one split silently voided ten accepted moves.
        # The start time identifies the line, so find it by that.
        if "start" in move:
            start = float(move["start"])
            if not (0 <= index < len(segments)
                    and abs(float(segments[index].get("start", 0)) - start) <= 0.05):
                index = next((i for i, seg in enumerate(segments)
                              if abs(float(seg.get("start", 0)) - start) <= 0.05), -1)
        if not 0 <= index < len(segments):
            missing.append(move)
            continue
        sid = by_name.get(name.casefold())
        if sid is None:
            used = {int(k) for k in names if str(k).lstrip("-").isdigit()}
            used |= {int(s.get("speaker_id", 0)) for s in segments}
            sid = str(max(used, default=-1) + 1)
            names[sid] = name
            by_name[name.casefold()] = sid
        entry = meta.setdefault(sid, {})
        entry["verified"] = True
        entry.setdefault("source", "user")
        if str(segments[index].get("speaker_id")) != sid:
            segments[index]["speaker_id"] = int(sid)
            segments[index].pop("speaker", None)
            moved += 1
    return {"moved": moved, "requested": len(moves), "not_found": len(missing)}


def run(json_path: Path, *, on_progress: Callable[[float], None] | None = None) -> dict:
    """Load everything for one transcript and return its proposals."""
    from shared.audio_utils import extract_embedding, load_audio
    from shared.voice_candidate_review import load_candidate_config
    from shared.voice_library_lite import _get_speaker_embed_session

    config = load_candidate_config()
    if not (config.get("available") and config.get("model_path") and config.get("library_path")):
        return {"error": "No voice-identity library is active, so there is nothing to check against."}
    data = json.loads(json_path.read_text(encoding="utf-8"))
    audio_path = Path(data.get("audio_file") or "")
    if not audio_path.is_file():
        return {"error": f"Audio not found: {audio_path}"}
    session = _get_speaker_embed_session(str(config.get("model_key")), Path(config["model_path"]))
    if session is None:
        return {"error": f"Could not load the {config.get('model_key')} voice model."}
    library = json.loads(Path(config["library_path"]).read_text(encoding="utf-8"))
    audio = load_audio(str(audio_path), sr=_SAMPLE_RATE)

    def embed(start: float, end: float):
        chunk = audio[int(start * _SAMPLE_RATE):int(end * _SAMPLE_RATE)]
        if chunk.size == 0:
            return None
        try:
            return np.asarray(extract_embedding(chunk, sr=_SAMPLE_RATE, onnx_session=session), dtype=np.float32)
        except Exception:
            return None

    from shared.review_feedback import load_tuning
    result = propose(
        data, embed, library,
        margin_adjust=load_tuning().get("refine_margin_adjust"),
        scorer=str(config.get("scorer") or "top3_median"),
        threshold=float(config.get("threshold", 0.5)),
        min_margin=float(config.get("min_margin", 0.23)),
        recording=audio_path.stem,
        people=meeting_people(data, audio_path),
        on_progress=on_progress,
    )
    # Don't re-suggest what the reviewer already skipped for this recording,
    # unless the evidence for it has grown since.
    from shared.review_feedback import skipped_suggestions, suppress_skipped
    result["lines"], result["suppressed_skipped"] = suppress_skipped(
        result["lines"], skipped_suggestions(audio_path.stem))
    result["model"] = config.get("model_key")
    return result
