#!/usr/bin/env python3
"""Extract HiDock device-side .hda filenames from exported HiNotes state."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


FILENAME_RE = re.compile(r"\b\d{4}[A-Z][a-z]{2}\d{2}-\d{6}-Rec\d+\.hda\b")


def extract_names(text: str) -> list[str]:
    return sorted(set(FILENAME_RE.findall(text)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", help="Path to a JSON/text export containing HiNotes state")
    args = parser.parse_args()

    data = Path(args.source).read_text(encoding="utf-8")
    try:
        parsed = json.loads(data)
        data = json.dumps(parsed, ensure_ascii=False)
    except json.JSONDecodeError:
        pass

    for name in extract_names(data):
        print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
