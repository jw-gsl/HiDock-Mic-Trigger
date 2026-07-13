"""Per-recording stats for the meeting heatmap's Tier-2 tooltip.

Two distinct sources, deliberately:
- **speakers** come from each transcript's frontmatter (``~/HiDock/Raw
  Transcripts/*.md`` → ``speakers:``) — a factual diarization output.
- **action_items** come ONLY from the curated typed summaries
  (``~/HiDock/Summaries/*.md`` → checkbox items under "Next Steps"). Raw
  transcripts also carry an auto-extracted ``action_items:`` block, but that's
  un-curated (the AI hasn't reviewed/summarised it), so to-do counts use the
  summary instead. This makes action-item counts sparse (one per summarised
  meeting) but trustworthy.

Returns ``{source_mp3_name: {"speakers": int, "action_items": int}}``. Local
files only, no network.
"""
from __future__ import annotations

import re
from pathlib import Path

from shared.transcript_writer import parse_frontmatter

TRANSCRIPTS_DIR = Path.home() / "HiDock" / "Raw Transcripts"

# Markdown checkbox items ("[ ]" / "[x]") — how summary to-dos are written.
_CHECKBOX_RE = re.compile(r"\[[ xX]\]")


def _summary_recording_stem(summary: dict) -> str:
    """Recording stem a summary maps to: prefer its `transcript:` path stem,
    else the leading "<stem> - " portion of the summary filename."""
    src = summary.get("source") or ""
    if src:
        return Path(src).stem
    filename = summary.get("filename", "")
    return filename.split(" - ")[0].strip()


def transcript_stats(transcripts_dir: Path | None = None) -> dict:
    directory = transcripts_dir or TRANSCRIPTS_DIR
    out: dict[str, dict] = {}

    # Speakers — from transcript frontmatter.
    if directory.exists():
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
            out.setdefault(source, {"speakers": 0, "action_items": 0})
            out[source]["speakers"] = len(speakers) if isinstance(speakers, list) else 0

    # Action items — from curated summaries only (checkbox count), mapped to the
    # recording via the summary's transcript stem. Recordings aren't always
    # .mp3 (volume imports can be .wav etc.), so match an existing key by
    # stem first and only fall back to "<stem>.mp3" for new entries.
    by_stem = {Path(key).stem: key for key in out}
    try:
        from shared import summaries_index as si
        for summary in si.all_summaries():
            stem = _summary_recording_stem(summary)
            if not stem:
                continue
            key = by_stem.get(stem, stem + ".mp3")
            count = len(_CHECKBOX_RE.findall(summary.get("body", "")))
            out.setdefault(key, {"speakers": 0, "action_items": 0})
            out[key]["action_items"] = count
    except Exception:
        pass

    return out
