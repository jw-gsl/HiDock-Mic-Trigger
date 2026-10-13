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


def report(transcripts_dir: Path, *, since: str | None = None) -> dict:
    return {"refine": refine_report(), "calendar": calendar_report(),
            "corrections": correction_report(transcripts_dir, since=since)}
