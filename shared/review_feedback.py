"""What the reviewer's decisions teach the app, and how well it is doing.

Every Refine suggestion applied or skipped, and every meeting confirmed,
declined or swapped, is appended to a small JSON-lines log under
`~/HiDock/.review-feedback/`. The logs feed three things:

* Refine stops re-suggesting a line the reviewer skipped (unless the evidence
  for it has clearly grown);
* calendar matching prefers a meeting title the reviewer has confirmed before
  when nothing else separates the candidates (recurring meetings);
* `report()` measures, from the reviewer's own decisions, how often Refine and
  calendar suggestions are right — overall and for the pairs of voices Refine
  confuses — and how much each transcript was corrected after diarisation,
  read from the transcript history.
"""
from __future__ import annotations

import json
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

FEEDBACK_DIR = Path.home() / "HiDock" / ".review-feedback"
REFINE_LOG = "refine-events.jsonl"
CALENDAR_LOG = "calendar-decisions.jsonl"
# A skipped suggestion comes back only if its score has risen by this much.
RESUGGEST_MARGIN = 0.05


def _append(name: str, rows: list[dict], directory: Path | None = None) -> None:
    if not rows:
        return
    folder = directory or FEEDBACK_DIR
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()
    with (folder / name).open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps({"at": stamp, **row}, ensure_ascii=False) + "\n")


def _read(name: str, directory: Path | None = None) -> list[dict]:
    path = (directory or FEEDBACK_DIR) / name
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def record_refine(recording: str, action: str, moves: list[dict], directory: Path | None = None) -> None:
    """Log Refine suggestions the reviewer applied or skipped."""
    _append(REFINE_LOG, [{
        "recording": recording, "action": action,
        "start": float(m.get("start", 0)), "from_name": m.get("from_name"),
        "to_name": m.get("to_name"), "score": m.get("score"),
        "current_score": m.get("current_score"), "kind": m.get("kind", "line"),
        # Decisions on lines Refine applied by itself (undo = skipped, keep =
        # applied), so they count towards its accuracy like any other.
        **({"auto": True} if m.get("auto") else {}),
    } for m in moves], directory)


def skipped_suggestions(recording: str, directory: Path | None = None) -> list[dict]:
    """Skips for one recording, unless a later apply overrode them."""
    latest: dict[tuple[float, str], dict] = {}
    for row in _read(REFINE_LOG, directory):
        if row.get("recording") != recording:
            continue
        key = (round(float(row.get("start", 0)), 2), str(row.get("to_name")))
        latest[key] = row
    return [row for row in latest.values() if row.get("action") == "skipped"]


def suppress_skipped(lines: list[dict], skipped: list[dict]) -> tuple[list[dict], int]:
    """Drop line suggestions the reviewer already skipped, unless stronger now."""
    kept, dropped = [], 0
    for line in lines:
        match = next((s for s in skipped
                      if abs(float(s.get("start", 0)) - float(line["start"])) <= 0.05
                      and s.get("to_name") == line["to_name"]), None)
        if match and float(line["score"]) < float(match.get("score") or 0) + RESUGGEST_MARGIN:
            dropped += 1
            continue
        kept.append(line)
    return kept, dropped


def record_calendar(decision: dict, directory: Path | None = None) -> None:
    _append(CALENDAR_LOG, [decision], directory)


def confirmed_meeting_titles(directory: Path | None = None) -> Counter:
    """How often each meeting title has been confirmed — recurring meetings."""
    return Counter(row.get("final_title") for row in _read(CALENDAR_LOG, directory)
                   if row.get("decision") in {"accepted", "chose_other", "linked"} and row.get("final_title"))


def refine_report(directory: Path | None = None) -> dict:
    rows = _read(REFINE_LOG, directory)
    counts = Counter(r.get("action") for r in rows)
    pairs: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        pairs[f"{r.get('from_name')} → {r.get('to_name')}"][r.get("action")] += 1
    def rate(c: Counter) -> float | None:
        total = c["applied"] + c["skipped"]
        return round(c["applied"] / total, 3) if total else None
    return {
        "applied": counts["applied"], "skipped": counts["skipped"], "accept_rate": rate(counts),
        "by_pair": sorted(({"pair": p, "applied": c["applied"], "skipped": c["skipped"], "accept_rate": rate(c)}
                           for p, c in pairs.items()), key=lambda x: -(x["applied"] + x["skipped"])),
    }


def calendar_report(directory: Path | None = None) -> dict:
    rows = _read(CALENDAR_LOG, directory)
    by_how: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_how[str(r.get("chosen_by") or "none")][str(r.get("decision"))] += 1
    return {"decisions": len(rows), "by_how_suggested": {k: dict(v) for k, v in by_how.items()}}


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", "--git-dir", str(repo), *args],
                              capture_output=True, text=True, check=False).stdout
    except OSError:
        return ""


def _names(data: dict) -> dict[float, tuple[str, float]]:
    names = data.get("speaker_names") or {}
    return {round(float(s.get("start", 0)), 2):
            (str(names.get(str(s.get("speaker_id")), f"Speaker {s.get('speaker_id')}")),
             max(0.0, float(s.get("end", 0)) - float(s.get("start", 0))))
            for s in data.get("segments") or []}


def correction_report(transcripts_dir: Path, *, since: str | None = None, limit: int | None = None) -> dict:
    """How much of each transcript the reviewer re-labelled after diarisation.

    Compares the oldest saved version of each transcript (its state before the
    first review action — the pipeline's output) with the current file, by
    line start time, weighted by speech duration. Speaker work done before the
    first snapshot is invisible to this, so it understates corrections.
    """
    repo = transcripts_dir / ".hidock-transcript-history"
    # One pass over the history for each file's oldest version (one git call
    # per transcript took ~5 minutes for 1,800 of them).
    oldest: dict[str, tuple[str, str]] = {}
    commit = None
    log = _git(repo, "log", "--reverse", "--format=@%H %cI", "--name-only",
               *([f"--since={since}"] if since else []))
    for line in log.splitlines():
        if line.startswith("@"):
            commit = tuple(line[1:].split(" ", 1))
        elif line.endswith("_diarized.json") and commit and line not in oldest:
            oldest[line] = commit
    rows = []
    for path in sorted(transcripts_dir.glob("*_diarized.json")):
        if limit is not None and len(rows) >= limit:
            break
        name = path.name
        first = oldest.get(name)
        if not first:
            continue
        try:
            before = json.loads(_git(repo, "show", f"{first[0]}:{name}"))
            after = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        a, b = _names(before), _names(after)
        total = changed = 0.0
        for start, (who, seconds) in a.items():
            if start in b:
                total += seconds
                if b[start][0] != who:
                    changed += seconds
        if total:
            rows.append({"recording": name.replace("_diarized.json", ""), "first_saved": first[1],
                         "backend": before.get("backend") or "unknown",
                         "relabelled_share": round(changed / total, 3)})
    by_backend: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        by_backend[r["backend"]].append(r["relabelled_share"])
    return {
        "transcripts": len(rows),
        "by_backend": {k: {"transcripts": len(v), "mean_relabelled_share": round(sum(v) / len(v), 3)}
                       for k, v in by_backend.items()},
        "most_corrected": sorted(rows, key=lambda r: -r["relabelled_share"])[:10],
    }


def library_cleanup(apply: bool) -> dict | None:
    """Run (or just measure) the voice-library clean-up on the naming library."""
    try:
        from shared import voice_library_audit as audit
        from shared.voice_candidate_review import load_candidate_config
        config = load_candidate_config()
        if not (config.get("available") and config.get("library_path")):
            return None
        path = Path(config["library_path"])
        if apply:
            result = audit.auto_clean(path)
            # The live library is what new naming samples are copied from.
            from shared.voice_library_reconcile import reconcile_file
            live = Path.home() / "HiDock" / "Voice Library" / "embeddings.json"
            if live.exists():
                live_result = reconcile_file(live, apply=True, limit=audit.AUTO_LIMIT)
                result["live_relabelled"] = live_result.get("moved", 0)
                if live_result.get("held_back"):
                    result["live_relabel_held_back"] = live_result["found"]
            return result
        library = json.loads(path.read_text(encoding="utf-8"))
        return {"flagged": 0, "set_aside": 0, "total_set_aside": audit.set_aside_count(library)}
    except Exception as exc:  # never let the clean-up break the report
        return {"error": str(exc)}


def report(transcripts_dir: Path, *, since: str | None = None, retune: bool = False) -> dict:
    full = {"refine": refine_report(), "calendar": calendar_report(),
            "corrections": correction_report(transcripts_dir, since=since),
            "tuning": tune() if retune else load_tuning(),
            "library": library_cleanup(apply=retune)}
    full["summary"] = summary_lines(full)
    return full


# ── Automatic tuning ────────────────────────────────────────────────────────
TUNING_FILE = "tuning.json"
# Decisions needed for a pair before its margin moves, and how far it may move.
MIN_PAIR_DECISIONS = 8
MAX_MARGIN_RAISE = 0.15


def load_tuning(directory: Path | None = None) -> dict:
    path = (directory or FEEDBACK_DIR) / TUNING_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def tune(directory: Path | None = None) -> dict:
    """Adjust matching from the reviewer's own decisions, within bounds.

    Refine: for a pair of voices it keeps getting wrong (from → to), raise the
    margin a suggestion needs; for one the reviewer nearly always accepts,
    lower it a little. Calendar: stop preferring previously confirmed
    ("recurring") meetings if those picks are mostly declined or swapped.
    Written to `tuning.json`, read by voice_check and the app.
    """
    refine = _read(REFINE_LOG, directory)
    pairs: dict[str, Counter] = defaultdict(Counter)
    for r in refine:
        pairs[f"{r.get('from_name')} → {r.get('to_name')}"][r.get("action")] += 1
    adjust: dict[str, float] = {}
    for pair, c in pairs.items():
        n = c["applied"] + c["skipped"]
        if n < MIN_PAIR_DECISIONS:
            continue
        rate = c["applied"] / n
        if rate < 0.5:
            adjust[pair] = 0.10
        elif rate < 0.7:
            adjust[pair] = 0.05
        elif rate >= 0.9 and n >= 15:
            adjust[pair] = -0.05
    adjust = {p: max(-0.05, min(MAX_MARGIN_RAISE, v)) for p, v in adjust.items()}

    recurring = [r for r in _read(CALENDAR_LOG, directory) if r.get("chosen_by") == "recurring"]
    recurring_ok = sum(1 for r in recurring if r.get("decision") == "accepted")
    recurring_enabled = not (len(recurring) >= 4 and recurring_ok / len(recurring) < 0.5)

    tuning = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "refine_margin_adjust": adjust,
        "recurring_meetings": recurring_enabled,
    }
    folder = directory or FEEDBACK_DIR
    folder.mkdir(parents=True, exist_ok=True)
    (folder / TUNING_FILE).write_text(json.dumps(tuning, indent=2, ensure_ascii=False), encoding="utf-8")
    return tuning


def summary_lines(full: dict) -> list[str]:
    """The report in plain sentences, for the app's Settings."""
    lines: list[str] = []
    refine = full.get("refine") or {}
    decided = refine.get("applied", 0) + refine.get("skipped", 0)
    if decided:
        lines.append(f"Refine: you accepted {refine['applied']} of {decided} suggestions "
                     f"({round(100 * refine['applied'] / decided)}%).")
    else:
        lines.append("Refine: no suggestions applied or skipped yet.")
    adjust = (full.get("tuning") or {}).get("refine_margin_adjust") or {}
    for pair, delta in sorted(adjust.items()):
        p = next((x for x in refine.get("by_pair", []) if x["pair"] == pair), None)
        basis = f" (accepted {p['applied']} of {p['applied'] + p['skipped']})" if p else ""
        lines.append(f"  {pair}{basis}: Refine now needs "
                     f"{'stronger' if delta > 0 else 'slightly less'} evidence for this pair.")
    calendar = full.get("calendar") or {}
    counts = Counter()
    for by_how in (calendar.get("by_how_suggested") or {}).values():
        counts.update(by_how)
    suggested = counts["accepted"] + counts["chose_other"] + counts["declined"]
    if suggested:
        lines.append(f"Meetings: you accepted {counts['accepted']} of {suggested} suggested meetings "
                     f"({round(100 * counts['accepted'] / suggested)}%).")
    else:
        lines.append("Meetings: no meeting suggestions confirmed or declined yet.")
    if (full.get("tuning") or {}).get("recurring_meetings") is False:
        lines.append("  Preferring previously confirmed meetings is off — those picks were mostly wrong.")
    library = full.get("library") or {}
    moved = library.get("relabelled", 0) + library.get("live_relabelled", 0)
    if moved:
        lines.append(f"Voice library: moved {moved} sample(s) to the person the transcript says was speaking.")
    held = library.get("relabel_held_back", 0) + library.get("live_relabel_held_back", 0)
    if held:
        lines.append(f"Voice library: {held} samples are filed under someone other than the person the "
                     "transcript says was speaking — unusually many, so they were not moved. "
                     "Ask for the library to be reviewed.")
    if library.get("held_back"):
        lines.append(f"Voice library: {library['flagged']} samples look mislabelled — unusually many, so they "
                     "were not set aside automatically. Ask for the library to be reviewed.")
    elif library.get("set_aside"):
        lines.append(f"Voice library: set aside {library['set_aside']} sample(s) that sounded like someone else "
                     f"({', '.join(library.get('people', [])[:4])}).")
    if library.get("total_set_aside"):
        lines.append(f"  {library['total_set_aside']} sample(s) set aside in total; Undo clean-ups restores them.")
    elif "total_set_aside" in library and not (library.get("set_aside") or moved or held):
        lines.append("Voice library: no mislabelled samples found.")
    for backend, stats in sorted(((full.get("corrections") or {}).get("by_backend") or {}).items()):
        if backend == "unknown":
            continue
        lines.append(f"Speaker detection ({backend}): you re-labelled "
                     f"{round(100 * stats['mean_relabelled_share'])}% of speech on average "
                     f"across {stats['transcripts']} transcripts.")
    return lines
