"""Tests for shared.migrate module."""
from __future__ import annotations

import json

import pytest

from shared.migrate import (
    add_frontmatter_to_file,
    find_transcripts_without_frontmatter,
    migrate,
)


@pytest.fixture
def transcript_env(tmp_path):
    """Set up a directory with mixed transcripts."""
    transcripts_dir = tmp_path / "transcripts"
    transcripts_dir.mkdir()

    # File without frontmatter (old format)
    (transcripts_dir / "old_recording.md").write_text(
        "Hello world, this is an old transcript without frontmatter.\n",
        encoding="utf-8",
    )

    # File with frontmatter (new format)
    (transcripts_dir / "new_recording.md").write_text(
        "---\ntitle: New Recording\ntype: meeting\n---\n\n## Transcript\n\nHello.\n",
        encoding="utf-8",
    )

    # Another old file
    (transcripts_dir / "voice_memo.md").write_text(
        "Quick voice memo about project ideas.\n",
        encoding="utf-8",
    )

    # State file with metadata
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    state_path = state_dir / "state.json"
    state_path.write_text(json.dumps({
        "transcriptions": {
            "old_recording.mp3": {
                "status": "completed",
                "source_path": "/recordings/old_recording.mp3",
                "model": "large-v3-turbo",
                "completed_at": "2026-03-15T10:30:00+00:00",
                "duration_s": 45.2,
            },
        }
    }), encoding="utf-8")

    return transcripts_dir, state_path


class TestFindTranscriptsWithoutFrontmatter:
    def test_finds_old_files(self, transcript_env):
        transcripts_dir, _ = transcript_env
        files = find_transcripts_without_frontmatter(transcripts_dir)
        names = [f.name for f in files]
        assert "old_recording.md" in names
        assert "voice_memo.md" in names
        assert "new_recording.md" not in names

    def test_empty_dir(self, tmp_path):
        files = find_transcripts_without_frontmatter(tmp_path / "nonexistent")
        assert files == []


class TestAddFrontmatter:
    def test_dry_run(self, transcript_env):
        transcripts_dir, _ = transcript_env
        result = add_frontmatter_to_file(
            transcripts_dir / "old_recording.md", dry_run=True
        )
        assert result["action"] == "would_migrate"
        # File should not be modified
        text = (transcripts_dir / "old_recording.md").read_text()
        assert not text.startswith("---")

    def test_apply(self, transcript_env):
        transcripts_dir, _ = transcript_env
        result = add_frontmatter_to_file(
            transcripts_dir / "old_recording.md", dry_run=False
        )
        assert result["action"] == "migrated"
        text = (transcripts_dir / "old_recording.md").read_text()
        assert text.startswith("---")
        assert "## Transcript" in text
        assert "Hello world" in text

    def test_with_state_metadata(self, transcript_env):
        transcripts_dir, state_path = transcript_env
        state_meta = json.loads(state_path.read_text())["transcriptions"]
        add_frontmatter_to_file(
            transcripts_dir / "old_recording.md",
            state_meta=state_meta,
            dry_run=False,
        )
        text = (transcripts_dir / "old_recording.md").read_text()
        assert "large-v3-turbo" in text
        assert "old_recording.mp3" in text

    def test_skip_already_migrated(self, transcript_env):
        transcripts_dir, _ = transcript_env
        result = add_frontmatter_to_file(
            transcripts_dir / "new_recording.md", dry_run=False
        )
        assert result["action"] == "skipped"


class TestMigrate:
    def test_dry_run(self, transcript_env):
        transcripts_dir, state_path = transcript_env
        results = migrate(
            transcripts_dir=transcripts_dir,
            state_path=state_path,
            dry_run=True,
        )
        assert len(results) == 2
        assert all(r["action"] == "would_migrate" for r in results)

    def test_apply(self, transcript_env):
        transcripts_dir, state_path = transcript_env
        results = migrate(
            transcripts_dir=transcripts_dir,
            state_path=state_path,
            dry_run=False,
        )
        assert len(results) == 2
        assert all(r["action"] == "migrated" for r in results)

        # Verify files are migrated
        for md_file in transcripts_dir.glob("*.md"):
            text = md_file.read_text()
            assert text.startswith("---"), f"{md_file.name} should have frontmatter"

    def test_no_files_to_migrate(self, tmp_path):
        results = migrate(transcripts_dir=tmp_path / "empty", dry_run=True)
        assert results == []
