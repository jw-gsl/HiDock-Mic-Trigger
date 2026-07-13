"""Obsidian vault sync — syncs transcripts into an Obsidian/Logseq vault.

Supports three sync strategies:
- symlink: Zero duplication, instant sync (won't work with cloud sync)
- copy: Works with iCloud/Dropbox/OneDrive, duplicates files
- direct: Write transcripts directly into the vault (no separate copy)

Obsidian-specific features:
- [[wikilinks]] for speaker names in transcripts
- Auto-generated person notes with meeting history
- Daily notes integration (append meeting summaries)

Usage:
    from shared.obsidian import VaultSync

    sync = VaultSync(vault_path="/Users/me/Obsidian/Vault")
    sync.sync_transcript(Path("~/HiDock/Raw Transcripts/meeting.md"))
    sync.sync_all()
    sync.generate_person_notes()
"""
from __future__ import annotations

import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

from shared.transcript_writer import parse_frontmatter


class VaultSync:
    """Syncs transcript files into an Obsidian vault."""

    def __init__(
        self,
        vault_path: str | Path,
        transcripts_dir: Path | None = None,
        subfolder: str = "Meetings",
        strategy: str = "copy",
        wikilinks: bool = True,
        daily_notes: bool = False,
        daily_notes_folder: str = "Daily Notes",
    ):
        """Initialize vault sync.

        Args:
            vault_path: Root path of the Obsidian vault.
            transcripts_dir: Source transcript directory.
                Defaults to ``~/HiDock/Raw Transcripts/``.
            subfolder: Subfolder within the vault for meetings.
            strategy: Sync strategy: "symlink", "copy", or "direct".
            wikilinks: If True, convert speaker names to [[wikilinks]].
            daily_notes: If True, append summaries to daily notes.
            daily_notes_folder: Folder name for daily notes in vault.
        """
        self.vault_path = Path(vault_path)
        self.transcripts_dir = transcripts_dir or (Path.home() / "HiDock" / "Raw Transcripts")
        self.subfolder = subfolder
        self.strategy = strategy
        self.wikilinks = wikilinks
        self.daily_notes = daily_notes
        self.daily_notes_folder = daily_notes_folder

    @property
    def meetings_dir(self) -> Path:
        """Target directory for meeting files in the vault."""
        return self.vault_path / self.subfolder

    @property
    def people_dir(self) -> Path:
        """Directory for auto-generated person notes."""
        return self.vault_path / self.subfolder / "People"

    def sync_transcript(self, source_path: Path) -> Path | None:
        """Sync a single transcript into the vault.

        Args:
            source_path: Path to the .md transcript file.

        Returns:
            Path to the vault copy, or None on failure.
        """
        source_path = Path(source_path).resolve()
        if not source_path.exists():
            return None

        self.meetings_dir.mkdir(parents=True, exist_ok=True)
        dest = self.meetings_dir / source_path.name

        try:
            if self.strategy == "symlink":
                if dest.exists() or dest.is_symlink():
                    dest.unlink()
                dest.symlink_to(source_path)

            elif self.strategy == "copy":
                content = source_path.read_text(encoding="utf-8")
                if self.wikilinks:
                    content = self._add_wikilinks(content)
                dest.write_text(content, encoding="utf-8")

            elif self.strategy == "direct":
                # For direct strategy, the transcript is already in the vault
                # Just add wikilinks if needed
                if self.wikilinks and source_path != dest:
                    content = source_path.read_text(encoding="utf-8")
                    content = self._add_wikilinks(content)
                    dest.write_text(content, encoding="utf-8")
                elif source_path != dest:
                    shutil.copy2(source_path, dest)

            else:
                print(f"Unknown sync strategy: {self.strategy}", file=sys.stderr)
                return None

            return dest

        except OSError as e:
            print(f"Vault sync failed for {source_path.name}: {e}", file=sys.stderr)
            return None

    def sync_all(self) -> list[Path]:
        """Sync all transcripts from the source directory.

        Returns:
            List of successfully synced vault paths.
        """
        if not self.transcripts_dir.exists():
            return []

        synced = []
        for md_file in sorted(self.transcripts_dir.glob("*.md")):
            result = self.sync_transcript(md_file)
            if result:
                synced.append(result)

        return synced

    def _add_wikilinks(self, content: str) -> str:
        """Convert speaker names in frontmatter to [[wikilinks]] in the body.

        Parses frontmatter to find speaker names, then replaces
        **Speaker Name:** patterns with **[[Speaker Name]]:** in the body.
        """
        meta, body = parse_frontmatter(content)
        speakers = meta.get("speakers", [])
        if not isinstance(speakers, list) or not speakers:
            return content

        # Replace speaker names in body with wikilinks. Longest names first,
        # so "James" doesn't rewrite inside "James Whiting" before the longer
        # name gets its turn.
        modified_body = body
        for name in sorted(speakers, key=lambda n: len(n) if isinstance(n, str) else 0,
                           reverse=True):
            if not name or not isinstance(name, str):
                continue
            # Skip generic speaker labels
            if re.match(r"^Speaker[\s_]\d+$", name):
                continue
            # Replace **Name:** with **[[Name]]:**
            modified_body = modified_body.replace(
                f"**{name}:**", f"**[[{name}]]:**"
            )
            # Also link bare mentions (whole word)
            # Avoid double-linking already-linked names
            modified_body = re.sub(
                rf"(?<!\[\[)\b{re.escape(name)}\b(?!\]\])",
                f"[[{name}]]",
                modified_body,
            )

        # Reconstruct full content
        if content.startswith("---"):
            end_idx = content.find("\n---", 3)
            if end_idx != -1:
                frontmatter_part = content[:end_idx + 4]
                return frontmatter_part + "\n" + modified_body

        return modified_body

    def generate_person_notes(self, knowledge_graph=None) -> list[Path]:
        """Generate or update person notes in the vault.

        Creates a note for each person with their meeting history,
        open action items, and topics.

        Args:
            knowledge_graph: Optional KnowledgeGraph instance. If not
                provided, builds one from the transcripts directory.

        Returns:
            List of generated person note paths.
        """
        _close_kg = False
        if knowledge_graph is None:
            from shared.knowledge import KnowledgeGraph
            knowledge_graph = KnowledgeGraph(transcripts_dir=self.transcripts_dir)
            knowledge_graph.rebuild()
            _close_kg = True

        people = knowledge_graph.list_people()
        if not people:
            return []

        self.people_dir.mkdir(parents=True, exist_ok=True)
        generated = []

        for person in people:
            name = person["name"]
            profile = knowledge_graph.get_person_profile(name)
            if not profile:
                continue

            # Sanitize name to prevent path traversal
            safe_name = re.sub(r'[<>:"/\\|?*\.\.]', '_', name).strip('_. ')
            if not safe_name:
                continue
            note_path = self.people_dir / f"{safe_name}.md"
            content = self._build_person_note(profile)
            note_path.write_text(content, encoding="utf-8")
            generated.append(note_path)

        if _close_kg:
            knowledge_graph.close()

        return generated

    def _build_person_note(self, profile: dict) -> str:
        """Build markdown content for a person note."""
        name = profile["name"]
        lines = [
            f"# {name}",
            "",
            f"**Meetings:** {profile['meeting_count']}",
        ]

        if profile.get("last_meeting_date"):
            lines.append(f"**Last meeting:** {profile['last_meeting_date']}")

        # Topics
        if profile.get("topics"):
            lines.append("")
            lines.append("## Topics")
            for topic in profile["topics"]:
                lines.append(f"- {topic['tag']} ({topic['count']} meetings)")

        # Open action items
        if profile.get("open_action_items"):
            lines.append("")
            lines.append("## Open Action Items")
            for item in profile["open_action_items"]:
                due = f" (due: {item['due']})" if item.get("due") else ""
                meeting = f" — from [[{item['meeting_title']}]]" if item.get("meeting_title") else ""
                lines.append(f"- [ ] {item['task']}{due}{meeting}")

        # Meeting history
        if profile.get("meetings"):
            lines.append("")
            lines.append("## Meeting History")
            for meeting in profile["meetings"]:
                title = meeting.get("title", "Untitled")
                date = meeting.get("date", "")
                lines.append(f"- [[{title}]] — {date}")

        lines.append("")
        return "\n".join(lines)

    def append_to_daily_note(self, transcript_path: Path) -> bool:
        """Append a meeting summary to today's daily note.

        Args:
            transcript_path: Path to the transcript .md file.

        Returns:
            True if appended, False on failure.
        """
        if not self.daily_notes:
            return False

        try:
            text = transcript_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return False

        meta, _ = parse_frontmatter(text)
        title = meta.get("title", transcript_path.stem)
        summary_text = meta.get("summary_text", "")

        today = datetime.now().strftime("%Y-%m-%d")
        daily_note_path = self.vault_path / self.daily_notes_folder / f"{today}.md"
        daily_note_path.parent.mkdir(parents=True, exist_ok=True)

        # Build entry
        entry_lines = [
            "",
            f"## Meeting: [[{title}]]",
        ]
        if summary_text:
            entry_lines.append(f"{summary_text}")

        # Action items from frontmatter
        action_items = meta.get("action_items", [])
        if isinstance(action_items, list) and action_items:
            entry_lines.append("")
            entry_lines.append("**Action Items:**")
            for item in action_items:
                if isinstance(item, dict) and item.get("task"):
                    assignee = f" (@{item['assignee']})" if item.get("assignee") else ""
                    entry_lines.append(f"- [ ] {item['task']}{assignee}")

        entry = "\n".join(entry_lines) + "\n"

        # Append to daily note (create if needed)
        if daily_note_path.exists():
            existing = daily_note_path.read_text(encoding="utf-8")
            daily_note_path.write_text(existing + entry, encoding="utf-8")
        else:
            header = f"# {today}\n"
            daily_note_path.write_text(header + entry, encoding="utf-8")

        return True
