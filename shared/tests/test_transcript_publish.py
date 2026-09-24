"""Tests for shared/transcript_publish.py against a local file:// remote."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from shared import transcript_publish


def _git(cwd: Path, *args: str) -> str:
    git = transcript_publish.git_path()
    assert git, "tests require a working git"
    completed = subprocess.run(
        [git, *args], cwd=str(cwd), capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


@pytest.fixture()
def remote_repo(tmp_path):
    """A bare repo standing in for github.com/jw-gsl/Transcripts."""
    remote = tmp_path / "Transcripts.git"
    remote.mkdir()
    _git(remote, "init", "--bare", "--quiet", "--initial-branch=main")
    return remote


@pytest.fixture()
def transcripts(tmp_path):
    directory = tmp_path / "Raw Transcripts"
    directory.mkdir()
    return directory


def _sync(transcripts, remote, clone, **kwargs):
    defaults = dict(
        transcripts_dir=transcripts,
        clone=clone,
        remote=f"file://{remote}",
    )
    defaults.update(kwargs)
    return transcript_publish.sync(**defaults)


def test_sync_creates_clone_and_pushes_first_md(tmp_path, remote_repo, transcripts):
    (transcripts / "Rec1.md").write_text("# Rec1\n\nAlice: hello\n", encoding="utf-8")
    clone = tmp_path / "clone"
    result = _sync(transcripts, remote_repo, clone,
                   md_paths=[transcripts / "Rec1.md"], reason="Initial import")
    assert result["ok"] and result["pushed"] and result["committed"] == 1

    pushed = _git(remote_repo, "show", "--textconv", "main:Rec1.md")
    assert "Alice: hello" in pushed
    subject = _git(remote_repo, "log", "-1", "--pretty=%s", "main")
    assert subject == "Initial import"
    author = _git(remote_repo, "log", "-1", "--pretty=%ae", "main")
    assert author == "transcripts@hidock.local"


def test_second_sync_pushes_only_changed_md(tmp_path, remote_repo, transcripts):
    clone = tmp_path / "clone"
    (transcripts / "Rec1.md").write_text("v1\n", encoding="utf-8")
    (transcripts / "Rec2.md").write_text("keep\n", encoding="utf-8")
    assert _sync(transcripts, remote_repo, clone,
                 md_paths=[transcripts / "Rec1.md", transcripts / "Rec2.md"],
                 reason="Initial import")["ok"]

    (transcripts / "Rec1.md").write_text("v2 renamed\n", encoding="utf-8")
    result = _sync(transcripts, remote_repo, clone,
                   md_paths=[transcripts / "Rec1.md"], reason="Renamed speaker A->B")
    assert result["ok"] and result["pushed"]
    assert _git(remote_repo, "show", "main:Rec1.md") == "v2 renamed"
    assert _git(remote_repo, "show", "main:Rec2.md") == "keep"


def test_no_changes_is_clean_noop(tmp_path, remote_repo, transcripts):
    clone = tmp_path / "clone"
    (transcripts / "Rec1.md").write_text("same\n", encoding="utf-8")
    _sync(transcripts, remote_repo, clone, md_paths=[transcripts / "Rec1.md"])
    result = _sync(transcripts, remote_repo, clone,
                   md_paths=[transcripts / "Rec1.md"])
    assert result["ok"] and not result["pushed"] and result["committed"] == 0
    assert "no changes" in result["detail"]


def test_no_push_commits_locally_and_pending_syncs_later(tmp_path, remote_repo, transcripts):
    clone = tmp_path / "clone"
    (transcripts / "Rec1.md").write_text("offline\n", encoding="utf-8")
    first = _sync(transcripts, remote_repo, clone,
                  md_paths=[transcripts / "Rec1.md"], push=False)
    assert first["ok"] and not first["pushed"] and first["pending"] >= 1

    second = _sync(transcripts, remote_repo, clone,
                   md_paths=[transcripts / "Rec1.md"])
    assert second["ok"] and second["pushed"]
    assert _git(remote_repo, "show", "main:Rec1.md") == "offline"


def test_remote_divergence_keeps_local_commit_no_force(tmp_path, remote_repo, transcripts):
    clone = tmp_path / "clone"
    (transcripts / "Rec1.md").write_text("base\n", encoding="utf-8")
    _sync(transcripts, remote_repo, clone, md_paths=[transcripts / "Rec1.md"])

    # Someone else pushes a different change to the remote via a second clone.
    other = tmp_path / "other"
    _git(tmp_path, "clone", "--quiet", f"file://{remote_repo}", str(other))
    (other / "Rec1.md").write_text("web edit\n", encoding="utf-8")
    _git(other, "add", "-A")
    _git(other, "-c", "user.name=Web", "-c", "user.email=web@example.com",
         "commit", "-q", "-m", "web edit")
    _git(other, "push", "--quiet", "origin", "main")

    # Local clone then edits the same file -> genuine conflict.
    (transcripts / "Rec1.md").write_text("local edit\n", encoding="utf-8")
    result = _sync(transcripts, remote_repo, clone,
                   md_paths=[transcripts / "Rec1.md"])
    assert not result["ok"]
    assert "pull --rebase failed" in result["detail"]
    assert result["pending"] >= 1
    # Remote keeps the web edit — we never force-push over it.
    assert _git(remote_repo, "show", "main:Rec1.md") == "web edit"


def test_non_markdown_input_never_copied(tmp_path, remote_repo, transcripts):
    clone = tmp_path / "clone"
    (transcripts / "Rec1_diarized.json").write_text("{}", encoding="utf-8")
    result = _sync(transcripts, remote_repo, clone,
                   md_paths=[transcripts / "Rec1_diarized.json"])
    assert result["ok"] and "no changes" in result["detail"]
    listing = _git(remote_repo, "ls-tree", "--name-only", "main") if _has_main(
        remote_repo) else ""
    assert "Rec1_diarized.json" not in listing


def _has_main(remote_repo: Path) -> bool:
    git = transcript_publish.git_path()
    completed = subprocess.run(
        [git, "--git-dir", str(remote_repo), "rev-parse", "--verify", "main"],
        capture_output=True, text=True,
    )
    return completed.returncode == 0


def test_all_md_sweep_picks_up_external_edits(tmp_path, remote_repo, transcripts):
    clone = tmp_path / "clone"
    (transcripts / "Rec1.md").write_text("old\n", encoding="utf-8")
    _sync(transcripts, remote_repo, clone, md_paths=[transcripts / "Rec1.md"])
    (transcripts / "Rec9.md").write_text("edited elsewhere\n", encoding="utf-8")
    (transcripts / "Rec9_diarized.json").write_text("{}", encoding="utf-8")
    result = _sync(transcripts, remote_repo, clone, all_md=True,
                   reason="Backfill sweep")
    assert result["ok"] and result["pushed"]
    assert _git(remote_repo, "show", "main:Rec9.md") == "edited elsewhere"
    files = _git(remote_repo, "ls-tree", "--name-only", "main").splitlines()
    assert "Rec9_diarized.json" not in files


def test_status_reports_pending(tmp_path, remote_repo, transcripts):
    clone = tmp_path / "clone"
    (transcripts / "Rec1.md").write_text("x\n", encoding="utf-8")
    _sync(transcripts, remote_repo, clone, md_paths=[transcripts / "Rec1.md"])
    state = transcript_publish.status(clone)
    assert state["clone_ok"] is True
    assert state.get("pending") == 0
    assert state.get("last_success")


def test_unreachable_remote_records_error_never_raises(tmp_path, transcripts):
    clone = tmp_path / "clone"
    (transcripts / "Rec1.md").write_text("x\n", encoding="utf-8")
    result = transcript_publish.sync(
        md_paths=[transcripts / "Rec1.md"],
        reason="boom",
        transcripts_dir=transcripts,
        clone=clone,
        remote=f"file://{tmp_path}/does-not-exist.git",
    )
    assert not result["ok"]
    assert "clone failed" in result["detail"] or result["pending"] == 0
