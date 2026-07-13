#!/usr/bin/env python3
"""Benchmark script: Whisper vs Parakeet on real HiDock recordings.

Runs both backends on a configurable list of recordings, measures wall-clock,
and extracts the first ~500 chars of each transcript for manual WER spot-check.
Dumps a markdown comparison table to stdout.

Usage:
    .venv/bin/python bench_backends.py [--recordings PATH PATH ...]

Default recordings span short (<1 min), medium (5–30 min), and long (>1 hr)
to cover the latency curve that matters in practice.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TRANSCRIBE_PY = REPO_ROOT / "transcription-pipeline" / "transcribe.py"
PARAKEET_PY = REPO_ROOT / "transcription-pipeline" / "transcribe_parakeet.py"
PY = REPO_ROOT / "transcription-pipeline" / ".venv" / "bin" / "python3"

# Where recordings live. Override with HIDOCK_RECORDINGS_DIR; defaults to the
# standard HiDock folder under the current user's home.
RECORDINGS_DIR = Path(
    os.environ.get("HIDOCK_RECORDINGS_DIR", Path.home() / "HiDock" / "Recordings")
)

DEFAULT_RECORDINGS = [
    # Pick a spread: short / medium / long so both backends are stressed
    # across the full latency curve. Set HIDOCK_RECORDINGS_DIR (and adjust the
    # filenames) to point at recordings that exist on your machine.
    str(RECORDINGS_DIR / "2026Apr15-135239-Rec54.mp3"),  # 32s
    str(RECORDINGS_DIR / "2026Apr15-141813-Rec55.mp3"),  # 6.7 min
    str(RECORDINGS_DIR / "2026Apr17-130532-Rec59.mp3"),  # 27 min
]


def run_backend(script: Path, mp3: Path, diarize: bool = False) -> dict:
    """Invoke a transcribe_* script on one file and time it.

    Returns {seconds, transcribed, transcript_path, excerpt} or an error
    dict with {seconds, error}.
    """
    args = [str(PY), str(script), "transcribe", str(mp3)]
    if diarize:
        args.append("--diarize")

    t0 = time.monotonic()
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=7200)
    except subprocess.TimeoutExpired:
        return {"seconds": time.monotonic() - t0, "error": "timeout after 2h"}
    dt = time.monotonic() - t0

    # transcribe.py / transcribe_parakeet.py both print a JSON result on stdout,
    # possibly after some PROGRESS/STAGE lines. Grab the last JSON object.
    stdout = proc.stdout or ""
    result: dict = {}
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                result = json.loads(line)
                break
            except json.JSONDecodeError:
                continue

    excerpt = ""
    tp = result.get("transcript_path")
    if tp and Path(tp).exists():
        md = Path(tp).read_text(errors="replace")
        # Strip YAML frontmatter so we're comparing body text
        if md.startswith("---"):
            end = md.find("---", 3)
            if end != -1:
                md = md[end + 3:]
        excerpt = md.strip()[:500]

    return {
        "seconds": dt,
        "transcribed": bool(result.get("transcribed")),
        "transcript_path": tp,
        "excerpt": excerpt,
        "stderr_tail": "\n".join((proc.stderr or "").splitlines()[-5:]),
    }


def mp3_duration_seconds(mp3: Path) -> float:
    """Return audio duration in seconds via ffprobe, 0 on failure."""
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json", str(mp3),
            ],
            capture_output=True, text=True, timeout=30,
        )
        return float(json.loads(out.stdout)["format"]["duration"])
    except Exception:
        return 0.0


def format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "-"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--recordings", nargs="+",
        help="Override the default recording list",
    )
    parser.add_argument(
        "--diarize", action="store_true",
        help="Also run diarization (adds ~20–30%% to wall-clock)",
    )
    parser.add_argument(
        "--skip-whisper", action="store_true",
        help="Only run Parakeet (reuse existing Whisper transcripts)",
    )
    parser.add_argument(
        "--skip-parakeet", action="store_true",
        help="Only run Whisper",
    )
    args = parser.parse_args()

    paths = [Path(p) for p in (args.recordings or DEFAULT_RECORDINGS)]
    for p in paths:
        if not p.exists():
            print(f"⚠ Missing: {p}", file=sys.stderr)
    paths = [p for p in paths if p.exists()]
    if not paths:
        print("No valid recordings to benchmark.", file=sys.stderr)
        sys.exit(1)

    results: list[dict] = []
    for mp3 in paths:
        duration = mp3_duration_seconds(mp3)
        print(f"\n=== {mp3.name} ({format_duration(duration)}) ===", file=sys.stderr)

        row = {"mp3": mp3.name, "audio_seconds": duration}

        if not args.skip_whisper:
            print("  running Whisper…", file=sys.stderr, flush=True)
            w = run_backend(TRANSCRIBE_PY, mp3, diarize=args.diarize)
            row["whisper"] = w
            print(f"    {w['seconds']:.1f}s — {'ok' if w.get('transcribed') else 'FAILED'}",
                  file=sys.stderr)

        if not args.skip_parakeet:
            print("  running Parakeet…", file=sys.stderr, flush=True)
            p = run_backend(PARAKEET_PY, mp3, diarize=args.diarize)
            row["parakeet"] = p
            print(f"    {p['seconds']:.1f}s — {'ok' if p.get('transcribed') else 'FAILED'}",
                  file=sys.stderr)

        results.append(row)

    # Markdown report
    print("\n# Whisper vs Parakeet benchmark")
    print()
    print("| Recording | Audio | Whisper (s) | Parakeet (s) | Whisper RTF | Parakeet RTF | Speed-up |")
    print("|---|---|---|---|---|---|---|")
    for r in results:
        w = r.get("whisper") or {}
        p = r.get("parakeet") or {}
        audio = r["audio_seconds"]
        wsec = w.get("seconds", 0)
        psec = p.get("seconds", 0)
        wrtf = f"{audio / wsec:.1f}×" if wsec > 0 and audio > 0 else "-"
        prtf = f"{audio / psec:.1f}×" if psec > 0 and audio > 0 else "-"
        speedup = f"{wsec / psec:.1f}×" if psec > 0 and wsec > 0 else "-"
        print(f"| {r['mp3']} | {format_duration(audio)} | {wsec:.1f} | {psec:.1f} | {wrtf} | {prtf} | {speedup} |")

    print("\n## Text excerpts (first 500 chars)")
    for r in results:
        print(f"\n### {r['mp3']}")
        w = r.get("whisper") or {}
        p = r.get("parakeet") or {}
        if w.get("excerpt"):
            print(f"\n**Whisper:**\n\n> {w['excerpt'][:400]}")
        if p.get("excerpt"):
            print(f"\n**Parakeet:**\n\n> {p['excerpt'][:400]}")


if __name__ == "__main__":
    main()
