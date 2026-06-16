#!/usr/bin/env python3
"""Tiny word-error-rate harness for A/B-testing transcription changes.

Usage:
    python3 wer.py reference.txt hypothesis.txt
    python3 wer.py reference.txt hyp_a.txt hyp_b.txt ...   # compare several

WER = (substitutions + insertions + deletions) / reference_words, after light
normalization (lowercase, strip punctuation, collapse whitespace, digits→words
left as-is). Lower is better. Also prints word accuracy = 1 - WER.

Reference is a hand-corrected transcript of a short audio slice; hypotheses are
the pipeline's output for the same slice under different settings.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


def normalize(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[^a-z0-9'\s]", " ", text)   # keep apostrophes (don't, it's)
    return text.split()


def wer(ref: list[str], hyp: list[str]) -> tuple[float, int, int, int]:
    """Levenshtein on word lists. Returns (wer, substitutions, insertions, deletions)."""
    n, m = len(ref), len(hyp)
    # dp[i][j] = edits to turn ref[:i] into hyp[:j]
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        dp[i][0] = i
    for j in range(m + 1):
        dp[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if ref[i - 1] == hyp[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j - 1], dp[i - 1][j], dp[i][j - 1])
    # backtrack to classify S/I/D
    i, j, s, ins, d = n, m, 0, 0, 0
    while i > 0 or j > 0:
        if i > 0 and j > 0 and ref[i - 1] == hyp[j - 1]:
            i, j = i - 1, j - 1
        elif i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + 1:
            s += 1
            i, j = i - 1, j - 1
        elif j > 0 and dp[i][j] == dp[i][j - 1] + 1:
            ins += 1
            j -= 1
        else:
            d += 1
            i -= 1
    total = dp[n][m]
    return (total / n if n else 0.0), s, ins, d


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 1
    ref = normalize(Path(argv[1]).read_text(encoding="utf-8"))
    print(f"reference: {len(ref)} words ({argv[1]})\n")
    print(f"{'hypothesis':<40} {'WER':>7} {'acc':>7}   S/I/D")
    rows = []
    for h in argv[2:]:
        hyp = normalize(Path(h).read_text(encoding="utf-8"))
        w, s, ins, d = wer(ref, hyp)
        rows.append((w, Path(h).name, s, ins, d, len(hyp)))
        print(f"{Path(h).name:<40} {w*100:6.1f}% {(1-w)*100:6.1f}%   {s}/{ins}/{d}")
    if len(rows) > 1:
        best = min(rows)
        print(f"\nbest: {best[1]}  ({best[0]*100:.1f}% WER)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
