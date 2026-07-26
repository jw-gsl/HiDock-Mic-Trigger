"""Leakage-free benchmarks for HiDock speaker-identity libraries.

Each benchmark case holds out an entire meeting, then asks whether the held-out
speaker embedding can be identified from that person's other meetings.  This
keeps recording conditions and meeting participants from leaking into the
gallery and makes model/scorer comparisons repeatable before live deployment.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


_SCORERS = ("max", "top3_median", "centroid")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _samples(entry: dict) -> list[dict]:
    samples = entry.get("samples")
    if isinstance(samples, list):
        return samples
    embedding = entry.get("embedding")
    return [{"embedding": embedding, "source_file": "legacy-profile"}] if embedding else []


def _vector(sample: dict) -> np.ndarray | None:
    try:
        vector = np.asarray(sample.get("embedding"), dtype=np.float32).reshape(-1)
    except (TypeError, ValueError):
        return None
    if not vector.size or not np.isfinite(vector).all():
        return None
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 1e-10 else None


def _source(sample: dict) -> str:
    return str(sample.get("source_file") or sample.get("audio_file") or sample.get("id") or "unknown")


def _quality(sample: dict) -> float:
    try:
        return float(sample.get("quality_score", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _representatives_by_meeting(entry: dict, *, active_only: bool) -> list[dict]:
    """Return the best-quality compatible sample from each source meeting."""
    best: dict[str, tuple[float, dict, np.ndarray]] = {}
    for sample in _samples(entry):
        if active_only and sample.get("active") is False:
            continue
        vector = _vector(sample)
        if vector is None:
            continue
        source = _source(sample)
        candidate = (_quality(sample), sample, vector)
        if source not in best or candidate[0] > best[source][0]:
            best[source] = candidate
    return [
        {"source": source, "sample": item[1], "vector": item[2]}
        for source, item in sorted(best.items())
    ]


def _score_person(test: np.ndarray, gallery: list[np.ndarray], mode: str) -> float:
    matrix = np.stack(gallery)
    similarities = matrix @ test
    if mode == "max":
        return float(np.max(similarities))
    if mode == "top3_median":
        top = np.sort(similarities)[-3:]
        return float(np.median(top))
    if mode == "centroid":
        centroid = matrix.mean(axis=0)
        norm = float(np.linalg.norm(centroid))
        return float(np.dot(centroid / norm, test)) if norm > 1e-10 else -1.0
    raise ValueError(f"Unsupported scorer: {mode}")


def _gate_metrics(cases: list[dict], threshold: float, margin: float) -> dict:
    """Count gate-accepted cases; a missing runner-up never clears the margin."""
    auto = [
        case
        for case in cases
        if case["best_score"] >= threshold
        and case["margin"] >= margin
        and case.get("_has_runner_up", True)
    ]
    correct = [case for case in auto if case["best_name"] == case["actual"]]
    return {
        "threshold": round(threshold, 3),
        "margin": round(margin, 3),
        "auto_decisions": len(auto),
        "correct_auto_decisions": len(correct),
        "incorrect_auto_decisions": len(auto) - len(correct),
        "coverage": round(len(auto) / len(cases), 4) if cases else 0.0,
        "precision": round(len(correct) / len(auto), 4) if auto else None,
    }


def _zero_error_gate(cases: list[dict]) -> dict:
    """Find the highest-coverage zero-observed-error gate on this benchmark."""
    candidates = []
    for threshold in np.arange(0.50, 1.001, 0.01):
        for margin in np.arange(0.0, 0.301, 0.01):
            metrics = _gate_metrics(cases, float(threshold), float(margin))
            if metrics["incorrect_auto_decisions"] == 0:
                candidates.append(metrics)
    if not candidates:
        return _gate_metrics(cases, 1.001, 1.001)
    return max(
        candidates,
        key=lambda item: (
            item["correct_auto_decisions"],
            item["coverage"],
            -item["threshold"],
            -item["margin"],
        ),
    )


def _public_case(case: dict) -> dict:
    """Strip internal-only keys (prefixed ``_``) before serialising a case."""
    return {key: value for key, value in case.items() if not key.startswith("_")}


def _summarise_cases(cases: list[dict]) -> dict:
    by_speaker: dict[str, list[dict]] = defaultdict(list)
    for case in cases:
        by_speaker[case["actual"]].append(case)
    per_speaker = {
        name: {
            "cases": len(items),
            "top1_correct": sum(item["best_name"] == name for item in items),
            "top1_accuracy": round(sum(item["best_name"] == name for item in items) / len(items), 4),
        }
        for name, items in sorted(by_speaker.items())
    }
    correct = sum(case["best_name"] == case["actual"] for case in cases)
    return {
        "cases": len(cases),
        "speakers": len(per_speaker),
        "top1_correct": correct,
        "top1_accuracy": round(correct / len(cases), 4) if cases else 0.0,
        "macro_top1_accuracy": round(
            sum(item["top1_accuracy"] for item in per_speaker.values()) / len(per_speaker), 4,
        ) if per_speaker else 0.0,
        "production_gate": _gate_metrics(cases, 0.70, 0.04),
        "best_observed_zero_error_gate": _zero_error_gate(cases),
        "per_speaker": per_speaker,
        "case_results": [_public_case(case) for case in cases],
    }


def _load_library(
    library: dict | str | Path,
    excluded_sources: list[str] | tuple[str, ...],
) -> tuple[dict, set[str], dict, dict]:
    if not isinstance(library, dict):
        library = json.loads(Path(library).read_text(encoding="utf-8"))
    speakers = library.get("speakers") or {}
    excluded = {str(Path(source).expanduser().resolve()) for source in excluded_sources}
    all_representatives = {
        name: [
            item for item in _representatives_by_meeting(entry, active_only=False)
            if str(Path(item["source"]).expanduser().resolve()) not in excluded
        ]
        for name, entry in speakers.items()
    }
    active_representatives = {
        name: [
            item for item in _representatives_by_meeting(entry, active_only=True)
            if str(Path(item["source"]).expanduser().resolve()) not in excluded
        ]
        for name, entry in speakers.items()
    }
    return speakers, excluded, all_representatives, active_representatives


def _build_targets(
    all_representatives: dict,
    *,
    min_gallery_meetings: int,
    max_cases_per_speaker: int,
    case_selection: str = "first",
    seed: int = 0,
) -> list[dict]:
    if case_selection not in ("first", "stratified"):
        raise ValueError(f"Unsupported case selection: {case_selection}")
    rng = np.random.default_rng(seed) if case_selection == "stratified" else None
    targets = []
    for name, representatives in sorted(all_representatives.items()):
        # A target needs enough *other* meetings to form the robust gallery.
        if len(representatives) < min_gallery_meetings + 1:
            continue
        if max_cases_per_speaker > 0 and len(representatives) > max_cases_per_speaker:
            if case_selection == "stratified":
                # Seeded per-person sampling keeps large libraries comparable
                # across runs instead of always taking the first sources.
                indices = sorted(
                    rng.choice(len(representatives), size=max_cases_per_speaker, replace=False).tolist()
                )
                selected = [representatives[index] for index in indices]
            else:
                selected = representatives[:max_cases_per_speaker]
        else:
            selected = representatives
        targets.extend({"actual": name, **representative} for representative in selected)
    return targets


def _rank_galleries(
    target: dict,
    active_representatives: dict,
    min_gallery_meetings: int,
    *,
    skip_name: str | None = None,
) -> dict[str, list]:
    """Rank identities for one held-out case, per scorer.

    The held-out source meeting is always excluded from every gallery.
    ``skip_name`` additionally removes one identity entirely, producing the
    impostor gallery used by the open-set evaluation.
    """
    galleries: dict[str, list[np.ndarray]] = {}
    for name, representatives in active_representatives.items():
        if name == skip_name:
            continue
        vectors = [item["vector"] for item in representatives if item["source"] != target["source"]]
        if vectors:
            galleries[name] = vectors
    ranked_by_mode: dict[str, list] = {}
    for mode in _SCORERS:
        required = 1 if mode == "max" else min_gallery_meetings
        ranked = sorted(
            (
                (_score_person(target["vector"], vectors, mode), name, len(vectors))
                for name, vectors in galleries.items()
                if len(vectors) >= required
            ),
            reverse=True,
        )
        if ranked:
            ranked_by_mode[mode] = ranked
    return ranked_by_mode


def benchmark_voice_library(
    library: dict | str | Path,
    *,
    min_gallery_meetings: int = 3,
    max_cases_per_speaker: int = 20,
    excluded_sources: list[str] | tuple[str, ...] = (),
    case_selection: str = "first",
    seed: int = 0,
) -> dict:
    """Benchmark max, robust top-3, and centroid matching by held-out meeting."""
    speakers, excluded, all_representatives, active_representatives = _load_library(
        library, excluded_sources,
    )
    targets = _build_targets(
        all_representatives,
        min_gallery_meetings=min_gallery_meetings,
        max_cases_per_speaker=max_cases_per_speaker,
        case_selection=case_selection,
        seed=seed,
    )

    scorer_cases: dict[str, list[dict]] = {mode: [] for mode in _SCORERS}
    for target in targets:
        held_source = target["source"]
        for mode, ranked in _rank_galleries(
            target, active_representatives, min_gallery_meetings,
        ).items():
            best_score, best_name, best_support = ranked[0]
            runner_up = ranked[1][0] if len(ranked) > 1 else -1.0
            scorer_cases[mode].append({
                "actual": target["actual"],
                "held_out_source": held_source,
                "best_name": best_name,
                "best_score": round(best_score, 6),
                "runner_up_score": round(runner_up, 6),
                "margin": round(best_score - runner_up, 6),
                "best_supporting_meetings": best_support,
                "correct": best_name == target["actual"],
                "_has_runner_up": len(ranked) > 1,
            })

    return {
        "generated_at": _now(),
        "kind": "leave_one_meeting_out",
        "leakage_control": "all samples from the held-out source meeting are excluded from every gallery",
        "min_gallery_meetings": min_gallery_meetings,
        "max_cases_per_speaker": max_cases_per_speaker,
        "excluded_sources": sorted(excluded),
        "library_speakers": len(speakers),
        "library_samples": sum(len(_samples(entry)) for entry in speakers.values()),
        "eligible_target_speakers": len({target["actual"] for target in targets}),
        "target_cases": len(targets),
        "scorers": {mode: _summarise_cases(cases) for mode, cases in scorer_cases.items()},
    }


def _score_distribution(values: list[float]) -> dict:
    if not values:
        return {"median": None, "p90": None, "max": None}
    array = np.asarray(values, dtype=np.float64)
    return {
        "median": round(float(np.median(array)), 6),
        "p90": round(float(np.percentile(array, 90)), 6),
        "max": round(float(np.max(array)), 6),
    }


def _summarise_open_set_cases(cases: list[dict]) -> dict:
    accepted = [case for case in cases if case["accepted"]]
    accepted_names: dict[str, int] = defaultdict(int)
    for case in accepted:
        accepted_names[case["impostor_name"]] += 1
    by_speaker: dict[str, list[dict]] = defaultdict(list)
    for case in cases:
        by_speaker[case["actual"]].append(case)
    per_speaker = {
        name: {
            "cases": len(items),
            "false_passes": sum(item["accepted"] for item in items),
            "false_pass_rate": round(sum(item["accepted"] for item in items) / len(items), 4),
        }
        for name, items in sorted(by_speaker.items())
    }
    worst = sorted(
        accepted,
        key=lambda case: (case["impostor_score"], case["margin"]),
        reverse=True,
    )[:10]
    return {
        "impostor_cases": len(cases),
        "false_passes": len(accepted),
        "false_pass_rate": round(len(accepted) / len(cases), 4) if cases else 0.0,
        "accepted_names": dict(
            sorted(accepted_names.items(), key=lambda item: (-item[1], item[0]))
        ),
        "impostor_score": _score_distribution([case["impostor_score"] for case in cases]),
        "margin": _score_distribution([case["margin"] for case in cases]),
        "worst_cases": [_public_case(case) for case in worst],
        "per_speaker": per_speaker,
        "case_results": [_public_case(case) for case in cases],
    }


def benchmark_open_set(
    library: dict | str | Path,
    *,
    min_gallery_meetings: int = 3,
    max_cases_per_speaker: int = 20,
    excluded_sources: list[str] | tuple[str, ...] = (),
    threshold: float = 0.71,
    min_margin: float = 0.21,
    case_selection: str = "first",
    seed: int = 0,
) -> dict:
    """Open-set evaluation: score every held-out case against impostors only.

    Uses the same held-out cases as ``benchmark_voice_library``, but removes
    the query identity from the gallery entirely, simulating a genuinely
    unknown speaker.  A case is a false gate-pass when the best impostor
    clears ``threshold``/``min_margin`` with a real runner-up margin.
    """
    speakers, excluded, all_representatives, active_representatives = _load_library(
        library, excluded_sources,
    )
    targets = _build_targets(
        all_representatives,
        min_gallery_meetings=min_gallery_meetings,
        max_cases_per_speaker=max_cases_per_speaker,
        case_selection=case_selection,
        seed=seed,
    )

    scorer_cases: dict[str, list[dict]] = {mode: [] for mode in _SCORERS}
    for target in targets:
        held_source = target["source"]
        for mode, ranked in _rank_galleries(
            target, active_representatives, min_gallery_meetings, skip_name=target["actual"],
        ).items():
            best_score, best_name, best_support = ranked[0]
            has_runner_up = len(ranked) > 1
            runner_up = ranked[1][0] if has_runner_up else -1.0
            case = {
                "actual": target["actual"],
                "held_out_source": held_source,
                "impostor_name": best_name,
                "impostor_score": round(best_score, 6),
                "runner_up_name": ranked[1][1] if has_runner_up else None,
                "runner_up_score": round(runner_up, 6),
                "margin": round(best_score - runner_up, 6),
                "best_supporting_meetings": best_support,
                "_has_runner_up": has_runner_up,
            }
            case["accepted"] = bool(
                has_runner_up
                and case["impostor_score"] >= threshold
                and case["margin"] >= min_margin
            )
            scorer_cases[mode].append(case)

    return {
        "generated_at": _now(),
        "kind": "leave_one_identity_out",
        "leakage_control": (
            "the query identity is removed from every gallery; all samples "
            "from the held-out source meeting are excluded as well"
        ),
        "gate": {"threshold": threshold, "min_margin": min_margin},
        "min_gallery_meetings": min_gallery_meetings,
        "max_cases_per_speaker": max_cases_per_speaker,
        "case_selection": case_selection,
        "seed": seed if case_selection == "stratified" else None,
        "excluded_sources": sorted(excluded),
        "library_speakers": len(speakers),
        "library_samples": sum(len(_samples(entry)) for entry in speakers.values()),
        "eligible_target_speakers": len({target["actual"] for target in targets}),
        "target_cases": len(targets),
        "scorers": {mode: _summarise_open_set_cases(cases) for mode, cases in scorer_cases.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--library", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--min-gallery-meetings", type=int, default=3)
    parser.add_argument("--max-cases-per-speaker", type=int, default=20)
    parser.add_argument("--exclude-source", action="append", default=[])
    parser.add_argument(
        "--case-selection",
        choices=("first", "stratified"),
        default="first",
        help="Per-person case capping: first N sources, or a seeded random sample",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Seed used by --case-selection stratified (default: %(default)s)",
    )
    parser.add_argument(
        "--open-set-report",
        help="Also write a leave-one-identity-out (impostor) report to this path",
    )
    parser.add_argument("--open-set-threshold", type=float, default=0.71)
    parser.add_argument("--open-set-margin", type=float, default=0.21)
    args = parser.parse_args()
    report = benchmark_voice_library(
        args.library,
        min_gallery_meetings=max(1, args.min_gallery_meetings),
        max_cases_per_speaker=max(0, args.max_cases_per_speaker),
        excluded_sources=args.exclude_source,
        case_selection=args.case_selection,
        seed=args.seed,
    )
    path = Path(args.report).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = {
        "report": str(path),
        "target_cases": report["target_cases"],
        "eligible_target_speakers": report["eligible_target_speakers"],
        "scorers": {
            name: {
                "top1_accuracy": result["top1_accuracy"],
                "macro_top1_accuracy": result["macro_top1_accuracy"],
                "production_gate": result["production_gate"],
                "best_observed_zero_error_gate": result["best_observed_zero_error_gate"],
            }
            for name, result in report["scorers"].items()
        },
    }
    if args.open_set_report:
        open_set = benchmark_open_set(
            args.library,
            min_gallery_meetings=max(1, args.min_gallery_meetings),
            max_cases_per_speaker=max(0, args.max_cases_per_speaker),
            excluded_sources=args.exclude_source,
            threshold=args.open_set_threshold,
            min_margin=args.open_set_margin,
            case_selection=args.case_selection,
            seed=args.seed,
        )
        open_set_path = Path(args.open_set_report).expanduser().resolve()
        open_set_path.parent.mkdir(parents=True, exist_ok=True)
        open_set_path.write_text(
            json.dumps(open_set, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
        )
        summary["open_set"] = {
            "report": str(open_set_path),
            "gate": {"threshold": args.open_set_threshold, "min_margin": args.open_set_margin},
            "scorers": {
                name: {
                    "impostor_cases": result["impostor_cases"],
                    "false_passes": result["false_passes"],
                    "false_pass_rate": result["false_pass_rate"],
                }
                for name, result in open_set["scorers"].items()
            },
        }
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
