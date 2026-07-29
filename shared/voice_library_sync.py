"""Reconcile the live voice library with the library that actually names people.

There are two speaker libraries, written by two independent paths:

* the **live** library (`~/HiDock/Voice Library/embeddings.json`, TitaNet), written
  by `voice_library_lite.enroll_*` on *every* confirmed speaker name; and
* the **candidate** library (`voice-library.json` under the active entry in
  `~/HiDock/Voice Library Candidates`, ReDimNet2), written by
  `voice_candidate_review.record_suggestion_outcome` — which the app calls only
  when the model had already *proposed* a name for that speaker.

While the candidate model was under evaluation that isolation was the point: it
is what made the 2.1% -> 62% naming comparison trustworthy. Then the candidate
library was promoted to do automatic naming and the isolation was never removed,
so the library receiving every human confirmation stopped being the library that
names anyone.

The result is a bootstrapping deadlock. The candidate library only learns a name
the model already suggested, and it can only suggest someone already in it — so a
person absent from it can never be added through the app, however many times they
are confirmed. Jenny Helland was confirmed repeatedly and stayed unnameable.

This module makes the gap visible (`diff_libraries`) and closes it for people who
are already enrolled live (`backfill_candidate_from_main`), re-embedding their
human-confirmed clips with the candidate model. It never writes the live library
and never invents a name: every identity it enrols was labelled by a human in the
live library already.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from shared.voice_candidate_review import (
    ACTIVE_CANDIDATE_CONFIG,
    _atomic_write,
    _meeting_key,
    _rank_library,
    _sha256,
    load_candidate_config,
)
from shared.voice_library_lite import (
    _MAX_SAMPLES,
    _audio_quality_from_path,
    _enroll_into,
    _extract_audio_embedding,
    _get_speaker_embed_session,
    _samples_of,
    EMBEDDINGS_FILE,
)

# Cap the clip we re-embed. A live sample's window can be minutes long, and
# `_assess_sample_quality` scores anything over 90 s down to "very long segment
# may be mixed" — enough to push the result under `_MIN_ACTIVE_QUALITY` and land
# it in the archive, which would leave the person just as unnameable as before.
# 30 s is the top of the "useful segment duration" band.
_MAX_CLIP_SECONDS = 30.0

# Distinct meetings to enrol per person. `list_candidate_speakers` marks a
# profile `strong_eligible` at three active meetings, so three is the point at
# which a backfilled identity is as good as an organically grown one.
_DEFAULT_MAX_MEETINGS = 3


def _load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# Above this, the clip is not merely similar to another identity — it is the same
# voice already present under a different name (an alias like "Emma" vs
# "Emma Thorne", which is what the candidate library's first-name-only entries
# are). That needs a merge, not a rejection, so it is reported separately.
_ALIAS_SIMILARITY = 0.97


def _collision(library: dict, embedding, target: str, scorer: str, threshold: float):
    """The established identity this clip sounds more like, or None.

    The live library is not clean, so a backfill cannot trust its labels blindly.
    Jenny Helland's only live sample came from a 113 s block the library had
    itself flagged "very long segment may be mixed"; the clip inside it was
    James, and enrolling it taught the naming library that James is Jenny — at
    0.857 against his real voice and 0.024 against hers.

    The bar is the same one naming uses to claim an identity: if this clip would
    be *named* as somebody else, it must not be enrolled as this person. Only
    identities already in the naming library are compared, so a clip cannot be
    validated against something this same run just added.

    `kind` separates the two causes, because they need opposite remedies: an
    `alias` is the right voice under the wrong name and wants
    `voice_candidate_review.merge_candidate_speakers`; a `contamination` is the
    wrong voice and wants a clean sample from a reviewed transcript.
    """
    ranked = [
        row for row in _rank_library(library, embedding, scorer)
        if str(row.get("name", "")).casefold() != target.casefold()
    ]
    if not ranked:
        return None
    best = ranked[0]
    score = float(best["score"])
    if score < threshold:
        return None
    return {
        "name": best["name"],
        "score": round(score, 3),
        "kind": "alias" if score >= _ALIAS_SIMILARITY else "contamination",
        "alternatives": [
            {"name": row["name"], "score": round(float(row["score"]), 3)}
            for row in ranked[:3]
        ],
    }


def _speaker_names(library: dict) -> set[str]:
    return {
        name for name in (library.get("speakers") or {})
        if str(name).strip()
    }


def _has_active_sample(library: dict, name: str) -> bool:
    """Whether naming can actually reach this identity.

    `_rank_library` skips samples marked `active: False` and drops any identity
    left with none, so being *present* in the library is not the same as being
    nameable — a person whose only exemplar was archived on quality is as
    invisible as one who was never enrolled.
    """
    entry = (library.get("speakers") or {}).get(name)
    if not entry:
        return False
    return any(
        sample.get("active") is not False and isinstance(sample.get("embedding"), list)
        for sample in _samples_of(entry)
    )


def diff_libraries(
    *,
    config_path: str | Path = ACTIVE_CANDIDATE_CONFIG,
    main_path: str | Path = EMBEDDINGS_FILE,
) -> dict:
    """Compare the two libraries by name. Read-only.

    `missing_from_naming` is the consequential list: those people are enrolled
    live but absent from the library naming reads, so they can never be
    auto-named — and nothing distinguishes that from an ordinary non-match.
    """
    config = load_candidate_config(config_path)
    main_file = Path(main_path).expanduser()
    result: dict = {
        "main_path": str(main_file),
        "candidate_path": config.get("library_path"),
        "candidate_model": config.get("model_key"),
        "available": False,
        "reason": None,
        "main_count": 0,
        "candidate_count": 0,
        "shared_count": 0,
        "missing_from_naming": [],
        "candidate_only": [],
        "present_but_unnameable": [],
    }
    if not main_file.exists():
        result["reason"] = f"live library not found: {main_file}"
        return result
    if not config.get("available") or not config.get("library_path"):
        result["reason"] = str(config.get("reason") or "no candidate library configured")
        return result

    main_names = _speaker_names(_load_json(main_file))
    candidate_library = _load_json(Path(config["library_path"]))
    candidate_names = _speaker_names(candidate_library)
    result.update({
        "available": True,
        "main_count": len(main_names),
        "candidate_count": len(candidate_names),
        "shared_count": len(main_names & candidate_names),
        "missing_from_naming": sorted(main_names - candidate_names, key=str.casefold),
        "candidate_only": sorted(candidate_names - main_names, key=str.casefold),
        # Enrolled in the naming library yet still unreachable by it, because
        # every exemplar was archived on quality. Indistinguishable from a
        # non-match in the app, and not visible in any name-only comparison.
        "present_but_unnameable": sorted(
            (name for name in candidate_names
             if not _has_active_sample(candidate_library, name)),
            key=str.casefold,
        ),
    })
    return result


def _clip_window(start: float, end: float, max_seconds: float = _MAX_CLIP_SECONDS):
    """Centred window of at most `max_seconds`, or None when unusable.

    Centred rather than leading: a long attributed stretch usually starts on a
    handover, so the middle is the safer sample of one voice.
    """
    try:
        start = max(0.0, float(start))
        end = float(end)
    except (TypeError, ValueError):
        return None
    if not end > start:
        return None
    span = end - start
    if span <= max_seconds:
        return start, end
    middle = start + span / 2.0
    return middle - max_seconds / 2.0, middle + max_seconds / 2.0


def _usable_samples(entry: dict, max_meetings: int) -> list[dict]:
    """Best human-labelled samples with decodable audio, one per meeting."""
    scored = []
    for sample in _samples_of(entry):
        audio = str(sample.get("audio_file") or "")
        if not audio or not Path(audio).exists():
            continue
        window = _clip_window(sample.get("segment_start"), sample.get("segment_end"))
        if window is None:
            continue
        scored.append((
            # Active first, then quality, then the longer attributed stretch.
            0 if sample.get("active") is not False else 1,
            -float(sample.get("quality_score") or 0.0),
            -float(sample.get("total_talk_seconds") or 0.0),
            sample,
            window,
        ))
    scored.sort(key=lambda row: row[:3])

    chosen: list[dict] = []
    seen_meetings: set[str] = set()
    for _, _, _, sample, window in scored:
        meeting = _meeting_key(str(sample.get("source_file") or sample.get("audio_file") or ""))
        if meeting in seen_meetings:
            continue
        seen_meetings.add(meeting)
        chosen.append({"sample": sample, "clip": window, "meeting": meeting})
        if len(chosen) >= max_meetings:
            break
    return chosen


def backfill_candidate_from_main(
    names: list[str] | None = None,
    *,
    apply: bool = False,
    max_meetings: int = _DEFAULT_MAX_MEETINGS,
    config_path: str | Path = ACTIVE_CANDIDATE_CONFIG,
    main_path: str | Path = EMBEDDINGS_FILE,
) -> dict:
    """Enrol live-library identities into the naming library.

    Defaults to everyone in `missing_from_naming`. Nothing is written unless
    `apply` is true. The plan embeds every clip regardless, because embedding is
    what the contamination check needs — a dry run that skipped it would report a
    clip as usable and then reject it on apply.
    """
    diff = diff_libraries(config_path=config_path, main_path=main_path)
    if not diff["available"]:
        return {"status": "unavailable", "reason": diff["reason"], "people": []}

    config = load_candidate_config(config_path)
    targets = list(names) if names else list(diff["missing_from_naming"])
    main_library = _load_json(Path(main_path).expanduser())
    speakers = main_library.get("speakers") or {}

    library_path = Path(config["library_path"])
    library = _load_json(library_path)
    # Independent snapshot: `_enroll_into` mutates `library`, and a clip must be
    # validated only against identities that were already established.
    baseline = _load_json(library_path)
    scorer = str(config.get("scorer", "top3_median"))
    threshold = float(config.get("threshold", 0.5))

    model_file = Path(config["model_path"])
    expected = str(config.get("model_sha256") or "").lower()
    if expected and _sha256(model_file) != expected:
        raise ValueError("candidate model hash mismatch")
    session = _get_speaker_embed_session(config["model_key"], model_file)
    if session is None:
        raise ValueError("candidate model unavailable")

    people: list[dict] = []
    enrolled_total = 0
    rejected_total = 0
    for name in targets:
        entry = speakers.get(name)
        row: dict = {"name": name, "clips": [], "usable": 0, "enrolled": 0, "rejected": 0}
        if entry is None:
            row["skipped"] = "not in the live library"
            people.append(row)
            continue
        picks = _usable_samples(entry, max_meetings)
        if not picks:
            row["skipped"] = "no live sample has decodable audio with usable timing"
            people.append(row)
            continue

        for pick in picks:
            sample, (clip_start, clip_end) = pick["sample"], pick["clip"]
            audio = str(sample.get("audio_file"))
            clip: dict = {
                "meeting": pick["meeting"],
                "audio_file": audio,
                "clip_start": round(clip_start, 3),
                "clip_end": round(clip_end, 3),
                "live_quality": sample.get("quality_score"),
            }

            embedding, dimension, model = _extract_audio_embedding(
                audio,
                segment_start=clip_start,
                segment_end=clip_end,
                session=session,
                neural_model_version=config["model_key"],
            )
            collision = _collision(baseline, embedding, name, scorer, threshold)
            quality = _audio_quality_from_path(audio, clip_start, clip_end)
            clip["acoustic_quality"] = quality.get("acoustic_quality")
            if collision is not None:
                clip["rejected"] = (
                    f"already enrolled as {collision['name']} ({collision['score']}) — "
                    "same voice under another name; merge them"
                    if collision["kind"] == "alias"
                    else f"sounds like {collision['name']} ({collision['score']}) — "
                    "the live label is not trustworthy for this clip"
                )
                clip["collides_with"] = collision
                row["clips"].append(clip)
                row["rejected"] += 1
                rejected_total += 1
                continue
            row["usable"] += 1
            if not apply:
                row["clips"].append(clip)
                continue

            provenance = {
                "source_file": str(sample.get("source_file") or ""),
                "audio_file": audio,
                "speaker_id": str(sample.get("speaker_id") or ""),
                "segment_start": clip_start,
                "segment_end": clip_end,
                "turn_count": sample.get("turn_count"),
                "total_talk_seconds": sample.get("total_talk_seconds"),
                # The live sample was labelled by a human; `backfill` + `user`
                # is the trust pairing `_assess_sample_quality` already grants
                # full weight, and it is the honest description of the source.
                "label_source": "user",
                "observed_name": sample.get("observed_name") or name,
                "backfilled_from": str(Path(main_path).expanduser()),
                "backfilled_live_sample_id": sample.get("id"),
                **quality,
            }
            result_entry = _enroll_into(
                library,
                name,
                embedding,
                embed_dim=dimension,
                model=model,
                source="backfill",
                provenance=provenance,
                max_samples=int(config.get("max_active_samples", _MAX_SAMPLES)),
            )
            stored = _samples_of(result_entry)
            match = next(
                (s for s in stored if s.get("backfilled_live_sample_id") == sample.get("id")),
                None,
            )
            clip.update({
                "acoustic_quality": quality.get("acoustic_quality"),
                "quality_score": (match or {}).get("quality_score"),
                "quality_state": (match or {}).get("quality_state"),
                "active": (match or {}).get("active"),
            })
            row["clips"].append(clip)
            row["enrolled"] += 1
            enrolled_total += 1
        people.append(row)

    if apply and enrolled_total:
        _atomic_write(library_path, library)

    # Report against what naming can actually reach, not what merely got written.
    # An enrolled clip whose quality landed it in the archive leaves the person
    # exactly as unnameable as before, and calling that a fix would be wrong.
    for row in people:
        row["nameable"] = _has_active_sample(library, row["name"])
    return {
        "status": "applied" if apply else "planned",
        "library_path": str(library_path),
        "candidate_model": config.get("model_key"),
        "requested": len(targets),
        "enrolled_samples": enrolled_total,
        "rejected_clips": rejected_total,
        "now_nameable": sorted(
            (row["name"] for row in people if row["nameable"]), key=str.casefold,
        ),
        # Still unreachable by naming: every clip rejected, none available, or the
        # only clip enrolled was archived on quality.
        "still_unnameable": sorted(
            (row["name"] for row in people if not row["nameable"]), key=str.casefold,
        ),
        "people": people,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(ACTIVE_CANDIDATE_CONFIG))
    parser.add_argument("--main", default=str(EMBEDDINGS_FILE))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("diff", help="Report names present in one library and not the other")

    p_backfill = sub.add_parser(
        "backfill",
        help="Enrol live-library identities into the naming library (plan unless --apply)",
    )
    p_backfill.add_argument("--name", action="append", dest="names",
                            help="Only this person (repeatable); defaults to everyone missing")
    p_backfill.add_argument("--max-meetings", type=int, default=_DEFAULT_MAX_MEETINGS)
    p_backfill.add_argument("--apply", action="store_true",
                            help="Write the naming library; omit to print the plan only")

    args = parser.parse_args()
    if args.command == "diff":
        print(json.dumps(
            diff_libraries(config_path=args.config, main_path=args.main), indent=2,
        ))
        return
    print(json.dumps(
        backfill_candidate_from_main(
            args.names,
            apply=args.apply,
            max_meetings=args.max_meetings,
            config_path=args.config,
            main_path=args.main,
        ),
        indent=2,
    ))


if __name__ == "__main__":
    main()
