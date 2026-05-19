"""System health check — diagnose configuration, storage, and runtime issues.

Checks:
1. Directory structure (HIDOCK_ROOT, recordings, transcripts, models)
2. Database integrity (exists, schema valid, WAL mode)
3. Stale locks (transcription lock file)
4. Orphan detection (transcripts without audio, audio without transcripts)
5. LLM engine availability
6. Model status (installed, missing, required)
7. Transcription state (hung in-progress jobs)
8. Disk space
9. Recent errors from event log

Usage:
    from shared.health_check import run_health_check

    report = run_health_check()
    for check in report["checks"]:
        print(f"[{check['status']}] {check['name']}: {check['message']}")

CLI:
    python -m shared.health_check          # full report
    python -m shared.health_check --json   # JSON output
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class CheckResult:
    """Result of a single health check."""

    name: str
    status: str  # "ok", "warning", "error"
    message: str
    details: dict = field(default_factory=dict)


def run_health_check(hidock_root: Path | None = None) -> dict:
    """Run all health checks and return a structured report.

    Args:
        hidock_root: Override the HiDock root directory.

    Returns:
        Dict with 'checks' list, 'summary' counts, and 'overall' status.
    """
    root = hidock_root or Path.home() / "HiDock"
    checks: list[CheckResult] = []

    checks.append(_check_directories(root))
    checks.append(_check_database(root))
    checks.append(_check_stale_locks(root))
    checks.extend(_check_orphans(root))
    checks.append(_check_llm_engines())
    checks.append(_check_models(root))
    checks.append(_check_transcription_state(root))
    checks.append(_check_disk_space(root))
    checks.append(_check_recent_errors(root))

    # Summarize
    ok_count = sum(1 for c in checks if c.status == "ok")
    warn_count = sum(1 for c in checks if c.status == "warning")
    err_count = sum(1 for c in checks if c.status == "error")

    if err_count > 0:
        overall = "unhealthy"
    elif warn_count > 0:
        overall = "degraded"
    else:
        overall = "healthy"

    return {
        "overall": overall,
        "summary": {"ok": ok_count, "warnings": warn_count, "errors": err_count},
        "checks": [asdict(c) for c in checks],
    }


# ── Individual Checks ─────────────────────────────────────────────────────────


def _check_directories(root: Path) -> CheckResult:
    """Check that required directories exist."""
    dirs = {
        "HiDock root": root,
        "Recordings": root / "Recordings",
        "Raw Transcripts": root / "Raw Transcripts",
        "Speech-to-Text": root / "Speech-to-Text",
    }
    missing = [name for name, path in dirs.items() if not path.exists()]
    if missing:
        return CheckResult(
            "directories", "error",
            f"Missing directories: {', '.join(missing)}",
            {"missing": missing},
        )
    return CheckResult("directories", "ok", "All required directories exist")


def _check_database(root: Path) -> CheckResult:
    """Check database exists, is valid, and has correct schema."""
    db_path = root / "Raw Transcripts" / "knowledge.db"
    if not db_path.exists():
        return CheckResult(
            "database", "warning",
            "Knowledge database not found (will be created on first use)",
            {"path": str(db_path)},
        )

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        # Check integrity
        result = conn.execute("PRAGMA integrity_check").fetchone()
        if result[0] != "ok":
            conn.close()
            return CheckResult(
                "database", "error",
                f"Database integrity check failed: {result[0]}",
                {"path": str(db_path)},
            )

        # Check WAL mode
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]

        # Check tables exist
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        required = {"meetings", "people", "action_items", "decisions", "key_points", "tags"}
        missing = required - tables

        # Count rows
        meeting_count = conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0] if "meetings" in tables else 0

        conn.close()

        if missing:
            return CheckResult(
                "database", "warning",
                f"Missing tables: {', '.join(missing)} (run rebuild to fix)",
                {"missing_tables": list(missing), "journal_mode": mode},
            )

        return CheckResult(
            "database", "ok",
            f"Database OK — {meeting_count} meetings indexed, journal_mode={mode}",
            {"meetings": meeting_count, "journal_mode": mode, "path": str(db_path)},
        )
    except Exception as e:
        return CheckResult(
            "database", "error",
            f"Database error: {e}",
            {"path": str(db_path)},
        )


def _check_stale_locks(root: Path) -> CheckResult:
    """Check for stale transcription lock files."""
    lock_path = root / "transcription-pipeline" / ".transcribe.lock"
    if not lock_path.exists():
        return CheckResult("locks", "ok", "No lock files found")

    age_s = time.time() - lock_path.stat().st_mtime
    age_min = age_s / 60

    if age_min > 60:
        return CheckResult(
            "locks", "warning",
            f"Stale lock file found ({age_min:.0f} min old) — may need manual removal",
            {"lock_path": str(lock_path), "age_minutes": round(age_min, 1)},
        )

    return CheckResult(
        "locks", "ok",
        f"Lock file exists ({age_min:.0f} min old, likely active transcription)",
        {"lock_path": str(lock_path), "age_minutes": round(age_min, 1)},
    )


def _check_orphans(root: Path) -> list[CheckResult]:
    """Check for orphaned files (transcripts without audio, audio without transcripts)."""
    results = []
    recordings_dir = root / "Recordings"
    transcripts_dir = root / "Raw Transcripts"
    audio_exts = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}

    if not recordings_dir.exists() or not transcripts_dir.exists():
        return [CheckResult("orphans", "ok", "Directories not yet created")]

    audio_stems = {f.stem for f in recordings_dir.iterdir() if f.suffix.lower() in audio_exts}
    transcript_stems = {f.stem for f in transcripts_dir.glob("*.md")}

    untranscribed = audio_stems - transcript_stems
    orphan_transcripts = transcript_stems - audio_stems

    if untranscribed:
        results.append(CheckResult(
            "untranscribed_audio", "warning",
            f"{len(untranscribed)} audio file(s) without transcripts",
            {"count": len(untranscribed), "files": sorted(untranscribed)[:10]},
        ))
    else:
        results.append(CheckResult(
            "untranscribed_audio", "ok",
            "All audio files have transcripts",
        ))

    if orphan_transcripts:
        results.append(CheckResult(
            "orphan_transcripts", "ok",
            f"{len(orphan_transcripts)} transcripts without matching audio (may be normal)",
            {"count": len(orphan_transcripts)},
        ))

    return results


def _check_llm_engines() -> CheckResult:
    """Check which LLM engines are available."""
    try:
        from shared.llm_cli import detect_engines
        engines = detect_engines()
        if not engines:
            return CheckResult(
                "llm_engines", "warning",
                "No LLM engines detected — summarization will be unavailable",
                {"engines": []},
            )
        names = [e.name for e in engines]
        return CheckResult(
            "llm_engines", "ok",
            f"Available engines: {', '.join(names)}",
            {"engines": names},
        )
    except Exception as e:
        return CheckResult(
            "llm_engines", "error",
            f"Failed to detect engines: {e}",
        )


def _check_models(root: Path) -> CheckResult:
    """Check status of ML models."""
    try:
        from shared.models import get_model_status
        statuses = get_model_status()
        installed = [k for k, v in statuses.items() if v["installed"]]
        missing_required = [
            k for k, v in statuses.items()
            if not v["installed"] and v.get("required")
        ]
        missing_optional = [
            k for k, v in statuses.items()
            if not v["installed"] and not v.get("required")
        ]

        if missing_required:
            return CheckResult(
                "models", "error",
                f"Missing required models: {', '.join(missing_required)}",
                {"installed": installed, "missing_required": missing_required,
                 "missing_optional": missing_optional},
            )

        msg = f"Installed: {', '.join(installed) or 'none'}"
        if missing_optional:
            msg += f" | Optional missing: {', '.join(missing_optional)}"

        return CheckResult(
            "models", "ok", msg,
            {"installed": installed, "missing_optional": missing_optional},
        )
    except Exception as e:
        return CheckResult("models", "warning", f"Could not check models: {e}")


def _check_transcription_state(root: Path) -> CheckResult:
    """Check for hung transcriptions in state.json."""
    state_path = root / "transcription-pipeline" / "state.json"
    if not state_path.exists():
        return CheckResult("transcription_state", "ok", "No state file (first run)")

    try:
        state = json.loads(state_path.read_text())
        transcriptions = state.get("transcriptions", {})
        in_progress = [
            k for k, v in transcriptions.items()
            if v.get("status") == "in_progress"
        ]
        failed = [
            k for k, v in transcriptions.items()
            if v.get("status") == "failed"
        ]
        completed = sum(
            1 for v in transcriptions.values()
            if v.get("status") == "completed"
        )

        if in_progress:
            return CheckResult(
                "transcription_state", "warning",
                f"{len(in_progress)} transcription(s) stuck in_progress: {', '.join(in_progress[:3])}",
                {"in_progress": in_progress, "failed_count": len(failed),
                 "completed_count": completed},
            )

        msg = f"{completed} completed"
        if failed:
            msg += f", {len(failed)} failed"
        return CheckResult(
            "transcription_state", "ok", msg,
            {"completed_count": completed, "failed_count": len(failed)},
        )
    except Exception as e:
        return CheckResult("transcription_state", "error", f"State file error: {e}")


def _check_disk_space(root: Path) -> CheckResult:
    """Check available disk space."""
    try:
        usage = shutil.disk_usage(str(root) if root.exists() else str(Path.home()))
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        pct_free = (usage.free / usage.total) * 100

        if free_gb < 1:
            return CheckResult(
                "disk_space", "error",
                f"Critically low disk space: {free_gb:.1f} GB free ({pct_free:.0f}%)",
                {"free_gb": round(free_gb, 2), "total_gb": round(total_gb, 2)},
            )
        elif free_gb < 5:
            return CheckResult(
                "disk_space", "warning",
                f"Low disk space: {free_gb:.1f} GB free ({pct_free:.0f}%)",
                {"free_gb": round(free_gb, 2), "total_gb": round(total_gb, 2)},
            )
        return CheckResult(
            "disk_space", "ok",
            f"{free_gb:.1f} GB free ({pct_free:.0f}%)",
            {"free_gb": round(free_gb, 2), "total_gb": round(total_gb, 2)},
        )
    except Exception as e:
        return CheckResult("disk_space", "warning", f"Could not check disk space: {e}")


def _check_recent_errors(root: Path) -> CheckResult:
    """Check event log for recent errors."""
    try:
        from shared.event_log import errors_since
        errors = errors_since(hours=24)
        if not errors:
            return CheckResult("recent_errors", "ok", "No errors in last 24h")

        error_types = {}
        for ev in errors:
            error_types[ev.event_type] = error_types.get(ev.event_type, 0) + 1

        return CheckResult(
            "recent_errors", "warning",
            f"{len(errors)} error(s) in last 24h: {error_types}",
            {"count": len(errors), "by_type": error_types},
        )
    except Exception:
        return CheckResult("recent_errors", "ok", "Event log not available yet")


# ── CLI ───────────────────────────────────────────────────────────────────────


def _cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="HiDock Health Check")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    report = run_health_check()

    if args.json:
        print(json.dumps(report, indent=2))
        return

    status_icons = {"ok": "✓", "warning": "!", "error": "✗"}
    print(f"\nSystem Status: {report['overall'].upper()}")
    print(f"  {report['summary']['ok']} ok, "
          f"{report['summary']['warnings']} warnings, "
          f"{report['summary']['errors']} errors\n")

    for check in report["checks"]:
        icon = status_icons.get(check["status"], "?")
        print(f"  [{icon}] {check['name']}: {check['message']}")

    print()


if __name__ == "__main__":
    _cli()
