"""Rollback points must belong to the operation, not to the GUI.

The macOS app snapshots before every speaker-mutating action, but it did so in
Swift — so a `transcribe.py rediarize` run from the CLI rewrote a reviewed
transcript with no rollback point. That happened for real on 2026-07-28 to
Rec79 Part 2; only a hand-made copy recovered it.

These tests pin the properties that make the protection trustworthy: it covers
text artifacts only, it is idempotent, and it writes to the same repository the
app's History list reads.
"""

import json
import subprocess

import pytest

from shared.transcript_history import (
    HISTORY_DIR_NAME,
    history_artifacts,
    snapshot,
    versions,
)


@pytest.fixture
def transcript(tmp_path):
    """A transcript directory with the three versioned artifacts plus noise."""
    base = tmp_path / "2026Jul27-113245-Rec79-Part-2"
    diarized = base.with_name(base.name + "_diarized.json")
    diarized.write_text(json.dumps({"speaker_names": {"0": "Ian Reay"}}), encoding="utf-8")
    base.with_suffix(".md").write_text("# Ian Reay\nhello\n", encoding="utf-8")
    base.with_suffix(".srt").write_text("1\n00:00 --> 00:01\nhello\n", encoding="utf-8")
    # Must never be versioned: audio, and the raw ASR sidecar.
    base.with_suffix(".mp3").write_bytes(b"\0" * 2048)
    base.with_name(base.name + "_asr.json").write_text("{}", encoding="utf-8")
    return diarized


def _tracked_files(root):
    result = subprocess.run(
        ["/usr/bin/git", "--git-dir", str(root / HISTORY_DIR_NAME),
         "--work-tree", str(root), "ls-files"],
        capture_output=True, text=True,
    )
    return sorted(line for line in result.stdout.splitlines() if line)


def test_artifact_list_is_derived_from_the_recording_stem():
    assert history_artifacts("/x/Rec79-Part-2_diarized.json") == [
        "Rec79-Part-2_diarized.json", "Rec79-Part-2.md", "Rec79-Part-2.srt",
    ]


def test_snapshot_versions_only_text_artifacts(transcript):
    assert snapshot(transcript, "Before re-diarisation (CLI)") is True
    tracked = _tracked_files(transcript.parent)
    assert tracked == [
        "2026Jul27-113245-Rec79-Part-2.md",
        "2026Jul27-113245-Rec79-Part-2.srt",
        "2026Jul27-113245-Rec79-Part-2_diarized.json",
    ]
    # Audio and the raw ASR sidecar are deliberately absent.
    assert not any(name.endswith((".mp3", "_asr.json")) for name in tracked)


def test_snapshot_records_the_reason_for_the_history_list(transcript):
    snapshot(transcript, "Before re-diarisation (CLI)")
    entries = versions(transcript)
    assert len(entries) == 1
    revision, label = entries[0]
    assert len(revision) == 40
    assert "Before re-diarisation (CLI)" in label


def test_a_second_snapshot_without_changes_is_a_no_op(transcript):
    assert snapshot(transcript, "first") is True
    # Nothing changed, so there is nothing to record — the history must not fill
    # with identical commits every time an idempotent command runs.
    assert snapshot(transcript, "second") is False
    assert len(versions(transcript)) == 1


def test_each_change_becomes_its_own_rollback_point(transcript):
    snapshot(transcript, "Before re-diarisation")
    transcript.write_text(json.dumps({"speaker_names": {"0": "Speaker 1"}}), encoding="utf-8")
    assert snapshot(transcript, "Before re-clustering") is True
    labels = [label for _revision, label in versions(transcript)]
    assert len(labels) == 2
    # Newest first, so the most recent rollback point is the obvious one to pick.
    assert "Before re-clustering" in labels[0]
    assert "Before re-diarisation" in labels[1]


def test_the_earlier_content_is_actually_recoverable(transcript):
    snapshot(transcript, "Before the damage")
    transcript.write_text(json.dumps({"speaker_names": {"0": "WRONG"}}), encoding="utf-8")
    revision = versions(transcript)[0][0]
    subprocess.run(
        ["/usr/bin/git", "--git-dir", str(transcript.parent / HISTORY_DIR_NAME),
         "--work-tree", str(transcript.parent), "checkout", revision, "--",
         transcript.name],
        capture_output=True, text=True, check=True,
    )
    assert json.loads(transcript.read_text())["speaker_names"]["0"] == "Ian Reay"


def test_snapshot_declines_when_there_is_nothing_to_version(tmp_path):
    missing = tmp_path / "nothing_diarized.json"
    assert snapshot(missing, "reason") is False


def test_history_lives_beside_the_transcript_with_no_remote(transcript):
    snapshot(transcript, "reason")
    repository = transcript.parent / HISTORY_DIR_NAME
    assert repository.is_dir()
    remotes = subprocess.run(
        ["/usr/bin/git", "--git-dir", str(repository), "remote"],
        capture_output=True, text=True,
    )
    # Transcripts are private; a history repo must never be able to push.
    assert remotes.stdout.strip() == ""


def test_versions_is_empty_before_any_snapshot(transcript):
    assert versions(transcript) == []
