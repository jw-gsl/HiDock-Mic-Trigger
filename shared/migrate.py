"""Migration utility — adds YAML frontmatter to existing transcripts.

Scans the transcripts directory for .md files without frontmatter and
adds basic metadata (date, source file, model) from state.json.

Safe to run multiple times — only modifies files that lack frontmatter.

Usage:
    python -m shared.migrate                    # dry-run
    python -m shared.migrate --apply            # apply changes
    python -m shared.migrate --rebuild-index    # also rebuild knowledge graph
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from shared.transcript_writer import (
    auto_title,
    build_frontmatter,
)


def find_transcripts_without_frontmatter(transcripts_dir: Path) -> list[Path]:
    """Find .md files that don't have YAML frontmatter."""
    results = []
    if not transcripts_dir.exists():
        return results

    for md_file in sorted(transcripts_dir.glob("*.md")):
        try:
            text = md_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        if not text.startswith("---"):
            results.append(md_file)

    return results


def load_state_metadata(state_path: Path) -> dict[str, dict]:
    """Load transcription metadata from state.json.

    Returns:
        Dict keyed by MP3 filename, with metadata values.
    """
    if not state_path.exists():
        return {}

    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return data.get("transcriptions", {})
    except (json.JSONDecodeError, OSError):
        return {}


def add_frontmatter_to_file(
    md_path: Path,
    state_meta: dict[str, dict] | None = None,
    dry_run: bool = True,
) -> dict:
    """Add YAML frontmatter to a transcript file that lacks it.

    Args:
        md_path: Path to the .md file.
        state_meta: Optional state.json metadata (keyed by mp3 filename).
        dry_run: If True, don't modify the file.

    Returns:
        Dict describing what was/would be done.
    """
    text = md_path.read_text(encoding="utf-8")

    # Already has frontmatter
    if text.startswith("---"):
        return {"file": str(md_path), "action": "skipped", "reason": "already has frontmatter"}

    # Try to find matching state entry
    state_meta = state_meta or {}
    basename = md_path.stem

    # Look for matching MP3 in state (try common extensions)
    meta = None
    for ext in (".mp3", ".wav", ".m4a", ".ogg", ".flac"):
        key = f"{basename}{ext}"
        if key in state_meta:
            meta = state_meta[key]
            break

    # Build frontmatter from available metadata
    title = auto_title(text)
    date = None
    duration = None
    model = ""
    source_file = ""

    if meta:
        date = meta.get("completed_at") or meta.get("started_at")
        duration = meta.get("duration_s")
        model = meta.get("model", "")
        source_path = meta.get("source_path", "")
        if source_path:
            source_file = Path(source_path).name

    if date is None:
        # Fall back to file modification time
        try:
            mtime = md_path.stat().st_mtime
            date = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
        except OSError:
            date = datetime.now(timezone.utc).isoformat()

    frontmatter = build_frontmatter(
        title=title,
        date=date,
        duration=duration,
        model=model,
        source_file=source_file,
    )

    # Avoid duplicate ## Transcript heading if the original already has one
    if text.lstrip().startswith("## Transcript"):
        new_content = f"{frontmatter}\n\n{text}"
    else:
        new_content = f"{frontmatter}\n\n## Transcript\n\n{text}"

    if not dry_run:
        md_path.write_text(new_content, encoding="utf-8")

    return {
        "file": str(md_path),
        "action": "migrated" if not dry_run else "would_migrate",
        "title": title,
        "date": date,
    }


def migrate(
    transcripts_dir: Path | None = None,
    state_path: Path | None = None,
    dry_run: bool = True,
    rebuild_index: bool = False,
) -> list[dict]:
    """Migrate all transcripts without frontmatter.

    Args:
        transcripts_dir: Transcript directory. Defaults to ~/HiDock/Raw Transcripts/.
        state_path: Path to state.json. Auto-detected if not provided.
        dry_run: If True, report what would change without modifying files.
        rebuild_index: If True, rebuild knowledge graph after migration.

    Returns:
        List of migration result dicts.
    """
    if transcripts_dir is None:
        transcripts_dir = Path.home() / "HiDock" / "Raw Transcripts"
    if state_path is None:
        state_path = Path.home() / "HiDock" / "transcription-pipeline" / "state.json"

    files = find_transcripts_without_frontmatter(transcripts_dir)
    if not files:
        return []

    state_meta = load_state_metadata(state_path)
    results = []

    for md_path in files:
        result = add_frontmatter_to_file(md_path, state_meta, dry_run=dry_run)
        results.append(result)

    if rebuild_index and not dry_run:
        try:
            from shared.knowledge import KnowledgeGraph
            kg = KnowledgeGraph(transcripts_dir=transcripts_dir)
            kg.rebuild()
            kg.close()
        except Exception as e:
            print(f"Index rebuild failed: {e}", file=sys.stderr)

    return results


def _cli():
    """Command-line interface for migration."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Add YAML frontmatter to existing transcripts"
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Apply changes (default is dry-run)"
    )
    parser.add_argument(
        "--rebuild-index", action="store_true",
        help="Rebuild knowledge graph after migration"
    )
    parser.add_argument(
        "--transcripts-dir", type=Path, default=None,
        help="Transcripts directory (default: ~/HiDock/Raw Transcripts/)"
    )
    args = parser.parse_args()

    dry_run = not args.apply
    results = migrate(
        transcripts_dir=args.transcripts_dir,
        dry_run=dry_run,
        rebuild_index=args.rebuild_index,
    )

    if not results:
        print("No transcripts need migration.")
        return

    for r in results:
        print(f"  {r['action']}: {r['file']}")
        if r.get("title"):
            print(f"    title: {r['title']}")

    migrated = sum(1 for r in results if r["action"] in ("migrated", "would_migrate"))
    verb = "Migrated" if not dry_run else "Would migrate"
    print(f"\n{verb} {migrated} file(s).")
    if dry_run:
        print("Run with --apply to make changes.")


if __name__ == "__main__":
    _cli()
