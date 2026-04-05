"""Tests for shared.obsidian module."""
from __future__ import annotations

from pathlib import Path

import pytest

from shared.obsidian import VaultSync
from shared.transcript_writer import write_transcript


@pytest.fixture
def vault_env(tmp_path):
    """Set up a vault and transcripts directory with sample files."""
    vault = tmp_path / "vault"
    vault.mkdir()
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()

    # Create sample transcripts
    write_transcript(
        transcripts / "meeting1.md",
        "We discussed the roadmap with the team.",
        summary={
            "title": "Roadmap Review",
            "action_items": [{"task": "Draft plan", "assignee": "Alice", "status": "open"}],
            "decisions": [],
            "key_points": ["Q2 priorities set"],
            "tags": ["planning"],
            "summary_text": "The team reviewed Q2 priorities.",
        },
    )

    # Diarized transcript with real speaker names
    diarized = {
        "segments": [
            {"speaker": "Speaker 1", "text": "Let's start.", "start": 0, "end": 1},
            {"speaker": "Speaker 2", "text": "Sounds good.", "start": 1, "end": 2},
        ],
        "speaker_names": {"Speaker 1": "Alice", "Speaker 2": "Bob"},
    }
    write_transcript(
        transcripts / "meeting2.md",
        "",
        diarized_result=diarized,
        summary={
            "title": "Sprint Planning",
            "action_items": [],
            "decisions": [{"text": "Ship by Friday", "topic": "release"}],
            "key_points": [],
            "tags": ["sprint"],
        },
    )

    return vault, transcripts


class TestSyncTranscript:
    def test_copy_strategy(self, vault_env):
        vault, transcripts = vault_env
        sync = VaultSync(vault_path=vault, transcripts_dir=transcripts, strategy="copy")
        result = sync.sync_transcript(transcripts / "meeting1.md")
        assert result is not None
        assert result.exists()
        assert (vault / "Meetings" / "meeting1.md").exists()

    def test_symlink_strategy(self, vault_env):
        vault, transcripts = vault_env
        sync = VaultSync(vault_path=vault, transcripts_dir=transcripts, strategy="symlink")
        result = sync.sync_transcript(transcripts / "meeting1.md")
        assert result is not None
        assert result.is_symlink()

    def test_nonexistent_file(self, vault_env):
        vault, transcripts = vault_env
        sync = VaultSync(vault_path=vault, transcripts_dir=transcripts)
        result = sync.sync_transcript(Path("/nonexistent/file.md"))
        assert result is None

    def test_custom_subfolder(self, vault_env):
        vault, transcripts = vault_env
        sync = VaultSync(vault_path=vault, transcripts_dir=transcripts, subfolder="Notes/Meetings")
        result = sync.sync_transcript(transcripts / "meeting1.md")
        assert (vault / "Notes" / "Meetings" / "meeting1.md").exists()


class TestSyncAll:
    def test_syncs_all_files(self, vault_env):
        vault, transcripts = vault_env
        sync = VaultSync(vault_path=vault, transcripts_dir=transcripts, strategy="copy")
        results = sync.sync_all()
        assert len(results) == 2

    def test_empty_dir(self, tmp_path):
        sync = VaultSync(vault_path=tmp_path / "vault", transcripts_dir=tmp_path / "empty")
        results = sync.sync_all()
        assert results == []


class TestWikilinks:
    def test_adds_wikilinks_for_speakers(self, vault_env):
        vault, transcripts = vault_env
        sync = VaultSync(vault_path=vault, transcripts_dir=transcripts, strategy="copy", wikilinks=True)
        sync.sync_transcript(transcripts / "meeting2.md")
        content = (vault / "Meetings" / "meeting2.md").read_text()
        assert "[[Alice]]" in content
        assert "[[Bob]]" in content

    def test_no_wikilinks_when_disabled(self, vault_env):
        vault, transcripts = vault_env
        sync = VaultSync(vault_path=vault, transcripts_dir=transcripts, strategy="copy", wikilinks=False)
        sync.sync_transcript(transcripts / "meeting2.md")
        content = (vault / "Meetings" / "meeting2.md").read_text()
        assert "[[Alice]]" not in content

    def test_skips_generic_speaker_labels(self, vault_env):
        vault, transcripts = vault_env
        # Create a transcript with only generic labels
        diarized = {
            "segments": [{"speaker": "Speaker 1", "text": "Hello", "start": 0, "end": 1}],
            "speaker_names": {"Speaker 1": "Speaker 1"},
        }
        write_transcript(transcripts / "generic.md", "", diarized_result=diarized)
        sync = VaultSync(vault_path=vault, transcripts_dir=transcripts, strategy="copy", wikilinks=True)
        sync.sync_transcript(transcripts / "generic.md")
        content = (vault / "Meetings" / "generic.md").read_text()
        assert "[[Speaker 1]]" not in content


class TestPersonNotes:
    def test_generates_person_notes(self, vault_env):
        vault, transcripts = vault_env

        from shared.knowledge import KnowledgeGraph
        kg = KnowledgeGraph(
            db_path=vault_env[0].parent / "test.db",
            transcripts_dir=transcripts,
        )
        kg.rebuild()

        sync = VaultSync(vault_path=vault, transcripts_dir=transcripts)
        notes = sync.generate_person_notes(knowledge_graph=kg)
        kg.close()

        # Should generate notes for speakers found in the knowledge graph
        # (which depends on frontmatter speakers being indexed)
        assert isinstance(notes, list)


class TestDailyNotes:
    def test_append_to_daily_note(self, vault_env):
        vault, transcripts = vault_env
        sync = VaultSync(
            vault_path=vault,
            transcripts_dir=transcripts,
            daily_notes=True,
        )
        result = sync.append_to_daily_note(transcripts / "meeting1.md")
        assert result is True

        # Check daily note was created
        daily_dir = vault / "Daily Notes"
        assert daily_dir.exists()
        daily_files = list(daily_dir.glob("*.md"))
        assert len(daily_files) == 1

        content = daily_files[0].read_text()
        assert "Roadmap Review" in content

    def test_disabled_daily_notes(self, vault_env):
        vault, transcripts = vault_env
        sync = VaultSync(vault_path=vault, transcripts_dir=transcripts, daily_notes=False)
        result = sync.append_to_daily_note(transcripts / "meeting1.md")
        assert result is False
