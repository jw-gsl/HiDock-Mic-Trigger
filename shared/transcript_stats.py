"""Per-transcript speaker / action-item counts for the meeting heatmap (Tier-2).

Scans ``~/HiDock/Raw Transcripts/*.md`` frontmatter and returns, keyed by the
recording's mp3 name (``source_file``), the number of speakers and action items.
The desktop app fetches this once on load and merges it into the heatmap tooltip
so per-day speaker / action-item totals appear when available. Local files only,
no network.
"""
from __future__ import annotations

from pathlib import Path

from shared.transcript_writer import parse_frontmatter

TRANSCRIPTS_DIR = Path.home() / "HiDock" / "Raw Transcripts"


def transcript_stats(transcripts_dir: Path | None = None) -> dict:
    """Return ``{source_mp3_name: {"speakers": int, "action_items": int}}``."""
    directory = transcripts_dir or TRANSCRIPTS_DIR
    out: dict[str, dict] = {}
    if not directory.exists():
        return out
    for path in directory.glob("*.md"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        meta, _ = parse_frontmatter(text)
        if not meta:
            continue
        source = meta.get("source_file") or (path.stem + ".mp3")
        speakers = meta.get("speakers", [])
        actions = meta.get("action_items", [])
        out[source] = {
            "speakers": len(speakers) if isinstance(speakers, list) else 0,
            "action_items": len(actions) if isinstance(actions, list) else 0,
        }
    return out
