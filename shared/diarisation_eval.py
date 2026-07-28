"""Measure diarisation quality against human-confirmed transcripts.

Every graph, merge, and threshold change so far has been argued from reasoning
and synthetic unit tests. Neither tells you whether a change makes real meetings
better, so "improve the diarisation" has been unfalsifiable. This module closes
that gap using ground truth that already exists on disk: the reviewed sidecars
in ~/HiDock/Raw Transcripts, where a human explicitly confirmed each speaker.

What is measured, precisely:

* **Speaker-count error** — predicted distinct speakers minus confirmed
  speakers. The single most user-visible failure ("it said 3 people, there were
  4").
* **Confusion rate** — share of confirmed speech time attributed to the wrong
  person, after mapping predicted speakers to confirmed names optimally. This is
  *speaker confusion only*, not full DER: the reviewed segment boundaries are
  reused as ASR input, so there are no miss/false-alarm terms.
* **Name recall** — share of confirmed people the pipeline auto-matched to the
  right name from the voice library. Measures auto-tagging, not clustering.

Two honest caveats, both consequences of reusing reviewed sidecars:

1. Reusing the reviewed segments as ASR input leaks *boundary* information —
   those boundaries already fall on true speaker changes. The task is therefore
   easier than production, where boundaries come from Whisper. Scores are
   comparable between runs (the point), not comparable to published DER.
2. Only speakers a human explicitly confirmed are ground truth. Time belonging
   to unconfirmed speakers is excluded rather than guessed at, so a meeting where
   two of four people were confirmed is scored on those two.

The metric functions are pure and unit-tested; only `run_eval` needs audio.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TRANSCRIPTS_DIR = Path.home() / "HiDock" / "Raw Transcripts"


# --- ground truth ------------------------------------------------------------

def verified_speaker_ids(data: dict) -> set[str]:
    """Speaker ids a human explicitly confirmed."""
    meta = data.get("speaker_meta") or {}
    return {
        str(sid) for sid, entry in meta.items()
        if (entry or {}).get("verified") is True
    }


def verified_truth(
    data: dict, window: tuple[float, float] | None = None
) -> list[tuple[float, float, str]]:
    """Confirmed-speaker intervals as (start, end, name), clipped to `window`.

    Segments owned by unconfirmed speakers are dropped: they are unknown, not
    negative examples, and scoring against them would measure agreement with a
    guess.
    """
    confirmed = verified_speaker_ids(data)
    if not confirmed:
        return []
    names = data.get("speaker_names") or {}
    out: list[tuple[float, float, str]] = []
    for segment in data.get("segments") or []:
        sid = str(segment.get("speaker_id", ""))
        if sid not in confirmed:
            continue
        name = names.get(sid) or segment.get("speaker")
        if not name:
            continue
        try:
            start, end = float(segment.get("start", 0.0)), float(segment.get("end", 0.0))
        except (TypeError, ValueError):
            continue
        if window is not None:
            start, end = max(start, window[0]), min(end, window[1])
        if end > start:
            out.append((start, end, str(name)))
    return out


def asr_input_segments(
    data: dict, window: tuple[float, float] | None = None
) -> list[dict]:
    """The reviewed segments stripped of every speaker attribution.

    Feeding these back in is what makes an offline re-run possible without
    re-transcribing. Speaker fields are removed deliberately — leaving
    `speaker_id` in place would hand the diarizer its own answer.
    """
    out: list[dict] = []
    for segment in data.get("segments") or []:
        try:
            start, end = float(segment.get("start", 0.0)), float(segment.get("end", 0.0))
        except (TypeError, ValueError):
            continue
        if window is not None:
            if end <= window[0] or start >= window[1]:
                continue
            start, end = max(start, window[0]), min(end, window[1])
        if end <= start:
            continue
        clean = {"start": start, "end": end, "text": segment.get("text", "")}
        words = [
            word for word in (segment.get("words") or [])
            if window is None
            or (float(word.get("start", 0)) >= window[0] and float(word.get("end", 0)) <= window[1])
        ]
        if words:
            clean["words"] = words
        out.append(clean)
    return out


# --- mapping and scoring -----------------------------------------------------

def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def overlap_seconds(
    truth: list[tuple[float, float, str]], predicted: list[dict]
) -> dict[tuple[str, str], float]:
    """Overlapping speech seconds per (confirmed name, predicted speaker id)."""
    table: dict[tuple[str, str], float] = {}
    for t_start, t_end, name in truth:
        for segment in predicted:
            try:
                p_start = float(segment.get("start", 0.0))
                p_end = float(segment.get("end", 0.0))
            except (TypeError, ValueError):
                continue
            shared = _overlap(t_start, t_end, p_start, p_end)
            if shared <= 0:
                continue
            key = (name, str(segment.get("speaker_id", "")))
            table[key] = table.get(key, 0.0) + shared
    return table


def optimal_mapping(
    truth: list[tuple[float, float, str]], predicted: list[dict]
) -> dict[str, str]:
    """Best one-to-one predicted-id → confirmed-name assignment.

    One-to-one is the right constraint: it refuses to credit a pipeline that
    split one person across two speakers (only one gets the name) or merged two
    people into one (only one is satisfied). A greedy pass would over-credit
    both. Falls back to greedy if SciPy is unavailable.
    """
    table = overlap_seconds(truth, predicted)
    if not table:
        return {}
    names = sorted({name for name, _ in table})
    ids = sorted({sid for _, sid in table})
    try:
        import numpy as np
        from scipy.optimize import linear_sum_assignment

        cost = np.zeros((len(names), len(ids)), dtype=float)
        for row, name in enumerate(names):
            for col, sid in enumerate(ids):
                cost[row, col] = -table.get((name, sid), 0.0)
        rows, cols = linear_sum_assignment(cost)
        return {
            ids[col]: names[row]
            for row, col in zip(rows, cols)
            if table.get((names[row], ids[col]), 0.0) > 0
        }
    except Exception:  # noqa: BLE001 - greedy is a fair approximation
        mapping: dict[str, str] = {}
        claimed: set[str] = set()
        for (name, sid), _ in sorted(table.items(), key=lambda kv: kv[1], reverse=True):
            if sid in mapping or name in claimed:
                continue
            mapping[sid] = name
            claimed.add(name)
        return mapping


def score_case(
    truth: list[tuple[float, float, str]],
    predicted_segments: list[dict],
    predicted_names: dict | None = None,
    predicted_meta: dict | None = None,
) -> dict:
    """Score one meeting. Returns per-case metrics plus the raw seconds."""
    truth_seconds = sum(end - start for start, end, _ in truth)
    truth_names = {name for _, _, name in truth}
    predicted_ids = {
        str(segment.get("speaker_id", "")) for segment in predicted_segments
    }
    mapping = optimal_mapping(truth, predicted_segments)
    table = overlap_seconds(truth, predicted_segments)
    correct = sum(
        seconds for (name, sid), seconds in table.items()
        if mapping.get(sid) == name
    )
    attributed = sum(table.values())

    names = predicted_names or {}
    meta = predicted_meta or {}
    auto_correct = 0
    for sid, name in mapping.items():
        predicted_name = names.get(str(sid))
        source = (meta.get(str(sid)) or {}).get("source")
        if predicted_name and predicted_name == name and source == "auto":
            auto_correct += 1

    return {
        "truth_speakers": len(truth_names),
        "predicted_speakers": len(predicted_ids),
        "count_error": len(predicted_ids) - len(truth_names),
        "truth_seconds": round(truth_seconds, 2),
        "attributed_seconds": round(attributed, 2),
        "correct_seconds": round(correct, 2),
        # Of the confirmed speech the pipeline placed somewhere, how much landed
        # on the wrong person. Undefined (None) when nothing overlapped at all.
        "confusion_rate": (
            round(1.0 - correct / attributed, 4) if attributed > 0 else None
        ),
        "name_recall": (
            round(auto_correct / len(truth_names), 4) if truth_names else None
        ),
    }


def aggregate(cases: list[dict]) -> dict:
    """Corpus-level summary. Rates are averaged over cases, not over seconds, so
    one very long meeting cannot dominate the headline number."""
    scored = [case for case in cases if case.get("confusion_rate") is not None]
    counted = [case for case in cases if "count_error" in case]

    def mean(values):
        values = [v for v in values if v is not None]
        return round(sum(values) / len(values), 4) if values else None

    exact = [1 if case["count_error"] == 0 else 0 for case in counted]
    return {
        "cases": len(cases),
        "scored_cases": len(scored),
        "count_exact_rate": mean(exact),
        "count_mae": mean([abs(case["count_error"]) for case in counted]),
        "count_bias": mean([case["count_error"] for case in counted]),
        "confusion_rate": mean([case["confusion_rate"] for case in scored]),
        "name_recall": mean([case.get("name_recall") for case in cases]),
        "total_truth_seconds": round(
            sum(case.get("truth_seconds", 0.0) for case in cases), 1
        ),
    }


# --- corpus discovery and running -------------------------------------------

@dataclass
class EvalCase:
    sidecar: Path
    audio: Path
    truth_speakers: int
    duration: float
    data: dict = field(repr=False, default_factory=dict)


def discover_cases(
    transcripts_dir: Path | str = DEFAULT_TRANSCRIPTS_DIR,
    *,
    min_verified: int = 2,
) -> list[EvalCase]:
    """Reviewed sidecars usable as ground truth, with their audio still present."""
    directory = Path(transcripts_dir)
    cases: list[EvalCase] = []
    for sidecar in sorted(directory.glob("*_diarized.json")):
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if len(verified_speaker_ids(data)) < min_verified:
            continue
        audio = Path(data.get("audio_file") or "")
        if not audio.exists():
            continue
        segments = data.get("segments") or []
        duration = float(segments[-1].get("end", 0.0)) if segments else 0.0
        cases.append(
            EvalCase(
                sidecar=sidecar,
                audio=audio,
                truth_speakers=len(verified_speaker_ids(data)),
                duration=duration,
                data=data,
            )
        )
    return cases


def select_sample(
    cases: list[EvalCase], sample: int, seed: int = 20260727
) -> list[EvalCase]:
    """Deterministic sample stratified by confirmed speaker count.

    Stratifying matters: two-speaker meetings are the majority, and a uniform
    sample would barely exercise the many-speaker cases where count errors
    actually happen.
    """
    if sample <= 0 or sample >= len(cases):
        return list(cases)
    strata: dict[int, list[EvalCase]] = {}
    for case in cases:
        strata.setdefault(case.truth_speakers, []).append(case)
    rng = random.Random(seed)
    for group in strata.values():
        rng.shuffle(group)
    chosen: list[EvalCase] = []
    # Round-robin across strata so every speaker count is represented before any
    # is sampled twice.
    while len(chosen) < sample:
        progressed = False
        for count in sorted(strata):
            if len(chosen) >= sample:
                break
            if strata[count]:
                chosen.append(strata[count].pop())
                progressed = True
        if not progressed:
            break
    return chosen


def run_eval(
    cases: list[EvalCase],
    *,
    max_seconds: float | None = 600.0,
    n_speakers_from_truth: bool = False,
    backend_options: dict | None = None,
    progress=None,
) -> dict:
    """Re-diarise each case and score it. Requires audio and a diarisation backend.

    `n_speakers_from_truth` feeds the confirmed count in as an explicit hint,
    which isolates *assignment* quality from *count-selection* quality — useful
    for telling which half of a regression moved.
    """
    from shared.pipeline_dispatch import diarize

    results: list[dict] = []
    for index, case in enumerate(cases, 1):
        window = (0.0, max_seconds) if max_seconds else None
        truth = verified_truth(case.data, window)
        asr = asr_input_segments(case.data, window)
        if not truth or not asr:
            continue
        hint = case.truth_speakers if n_speakers_from_truth else None
        try:
            predicted = diarize(
                str(case.audio), asr, n_speakers=hint, **(backend_options or {}),
            )
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the run
            results.append({"file": case.sidecar.name, "error": str(exc)})
            continue
        score = score_case(
            truth,
            predicted.get("segments") or [],
            predicted.get("speaker_names"),
            predicted.get("speaker_meta"),
        )
        score["file"] = case.sidecar.name
        results.append(score)
        if progress:
            progress(index, len(cases), score)
    scored = [row for row in results if "error" not in row]
    return {
        "config": {
            "max_seconds": max_seconds,
            "n_speakers_from_truth": n_speakers_from_truth,
            "backend_options": dict(backend_options or {}),
            "cases_requested": len(cases),
        },
        "summary": aggregate(scored),
        "failures": [row for row in results if "error" in row],
        "cases": results,
    }


def compare(baseline: dict, current: dict) -> dict:
    """Metric deltas between two reports. Negative confusion/count deltas are
    improvements; positive name_recall is an improvement."""
    before, after = baseline.get("summary", {}), current.get("summary", {})
    deltas = {}
    for key in ("count_exact_rate", "count_mae", "count_bias", "confusion_rate", "name_recall"):
        old, new = before.get(key), after.get(key)
        if old is None or new is None:
            deltas[key] = None
        else:
            deltas[key] = round(new - old, 4)
    return {"before": before, "after": after, "delta": deltas}


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Score diarisation against human-confirmed transcripts.",
    )
    parser.add_argument("--transcripts-dir", default=str(DEFAULT_TRANSCRIPTS_DIR))
    parser.add_argument("--sample", type=int, default=12,
                        help="0 evaluates every usable meeting (slow)")
    parser.add_argument("--seed", type=int, default=20260727)
    parser.add_argument("--max-seconds", type=float, default=600.0,
                        help="Evaluate only the first N seconds of each meeting; "
                             "0 for the whole thing")
    parser.add_argument("--n-speakers-from-truth", action="store_true",
                        help="Pass the confirmed count as a hint, isolating "
                             "assignment quality from count selection")
    parser.add_argument("--out", help="Write the JSON report here")
    parser.add_argument("--baseline", help="Compare against a previous report")
    # Both refinements now default to on in the backend, so an A/B needs to be
    # able to force either state explicitly rather than only opting in.
    parser.add_argument("--two-sided-partition", action="store_true", default=None,
                        help="Force the two-sided turn-graph partition search on")
    parser.add_argument("--no-two-sided-partition", dest="two_sided_partition",
                        action="store_false", help="Force it off")
    parser.add_argument("--refine-assignments", action="store_true", default=None,
                        help="Force the bounded turn-reassignment loop on")
    parser.add_argument("--no-refine-assignments", dest="refine_assignments",
                        action="store_false", help="Force it off")
    parser.add_argument("--list", action="store_true",
                        help="Only list the corpus; do not diarise")
    args = parser.parse_args(argv)

    cases = discover_cases(args.transcripts_dir)
    if not cases:
        print(f"No reviewed transcripts with confirmed speakers in {args.transcripts_dir}")
        return 1
    by_count: dict[int, int] = {}
    for case in cases:
        by_count[case.truth_speakers] = by_count.get(case.truth_speakers, 0) + 1
    print(f"corpus: {len(cases)} reviewed meetings, "
          f"{sum(c.duration for c in cases) / 3600:.1f}h audio")
    print("confirmed speakers per meeting: "
          + ", ".join(f"{k}→{v}" for k, v in sorted(by_count.items())))
    if args.list:
        return 0

    chosen = select_sample(cases, args.sample, args.seed)
    budget = sum(min(c.duration, args.max_seconds or c.duration) for c in chosen)
    print(f"evaluating {len(chosen)} meetings ({budget / 60:.0f} min of audio)")

    def progress(index, total, score):
        print(f"  [{index}/{total}] {score['file']}: "
              f"truth={score['truth_speakers']} pred={score['predicted_speakers']} "
              f"confusion={score['confusion_rate']}")

    backend_options = {}
    if args.two_sided_partition is not None:
        backend_options["two_sided_partition"] = args.two_sided_partition
    if args.refine_assignments is not None:
        backend_options["refine_assignments"] = args.refine_assignments
    if backend_options:
        print(f"backend options: {backend_options}")

    report = run_eval(
        chosen,
        max_seconds=args.max_seconds or None,
        n_speakers_from_truth=args.n_speakers_from_truth,
        backend_options=backend_options,
        progress=progress,
    )
    print("\nsummary:", json.dumps(report["summary"], indent=2))
    if report["failures"]:
        print(f"{len(report['failures'])} case(s) failed")
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {args.out}")
    if args.baseline:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        print("\ncomparison:", json.dumps(compare(baseline, report), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
