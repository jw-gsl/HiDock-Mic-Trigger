"""Tests for shared/transcript_publish.py against a local file:// remote."""
from __future__ import annotations

import json
import os
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


@pytest.fixture(autouse=True)
def _no_real_merge_groups(tmp_path, monkeypatch):
    """Never read the user's real ~/HiDock/merge_groups.json in tests."""
    monkeypatch.setattr(transcript_publish, "MERGE_GROUPS_FILE", tmp_path / "no-merges.json")


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


def test_commit_body_lists_edit_summary(tmp_path, remote_repo, transcripts):
    clone = tmp_path / "clone"
    (transcripts / "Rec1.md").write_text("v1\n", encoding="utf-8")
    result = _sync(transcripts, remote_repo, clone, md_paths=[transcripts / "Rec1.md"],
                   reason="Rec1: renamed speaker", body="- Rec1: Renamed 1 → Jeff\n- Rec1: Merged 2 into 1")
    assert result["ok"]
    body = _git(remote_repo, "log", "-1", "--pretty=%b", "main")
    assert "Renamed 1 → Jeff" in body and "Merged 2 into 1" in body


def test_missing_named_source_deletes_published_copy(tmp_path, remote_repo, transcripts):
    clone = tmp_path / "clone"
    (transcripts / "Rec1.md").write_text("one\n", encoding="utf-8")
    (transcripts / "Rec2.md").write_text("two\n", encoding="utf-8")
    _sync(transcripts, remote_repo, clone,
          md_paths=[transcripts / "Rec1.md", transcripts / "Rec2.md"])
    (transcripts / "Rec2.md").unlink()
    result = _sync(transcripts, remote_repo, clone, md_paths=[transcripts / "Rec2.md"],
                   reason="Removed Rec2")
    assert result["ok"] and result["pushed"]
    files = _git(remote_repo, "ls-tree", "--name-only", "main").splitlines()
    assert files == ["Rec1.md"]


def test_all_sync_prunes_a_few_stale_files(tmp_path, remote_repo, transcripts):
    clone = tmp_path / "clone"
    for index in range(10):
        (transcripts / f"Rec{index}.md").write_text(f"{index}\n", encoding="utf-8")
    _sync(transcripts, remote_repo, clone, all_md=True)
    (transcripts / "Rec3.md").unlink()
    result = _sync(transcripts, remote_repo, clone, all_md=True, reason="Manual sync")
    assert result["ok"]
    files = _git(remote_repo, "ls-tree", "--name-only", "main").splitlines()
    assert "Rec3.md" not in files and len(files) == 9


def test_all_sync_refuses_mass_prune(tmp_path, remote_repo, transcripts):
    clone = tmp_path / "clone"
    for index in range(10):
        (transcripts / f"Rec{index}.md").write_text(f"{index}\n", encoding="utf-8")
    _sync(transcripts, remote_repo, clone, all_md=True)
    for index in range(1, 10):
        (transcripts / f"Rec{index}.md").unlink()
    result = _sync(transcripts, remote_repo, clone, all_md=True)
    assert "skipped removing 9 of 10" in result["detail"]
    assert len(_git(remote_repo, "ls-tree", "--name-only", "main").splitlines()) == 10


def test_clone_failure_is_visible_in_status(tmp_path, transcripts):
    clone = tmp_path / "clone"
    (transcripts / "Rec1.md").write_text("x\n", encoding="utf-8")
    result = _sync(transcripts, tmp_path / "does-not-exist.git", clone,
                   md_paths=[transcripts / "Rec1.md"])
    assert not result["ok"]
    state = transcript_publish.status(clone)
    assert state["clone_ok"] is False
    assert "clone failed" in (state.get("last_error") or "")


def test_public_github_remote_is_refused(tmp_path, monkeypatch):
    clone = tmp_path / "clone"
    clone.mkdir()
    monkeypatch.setattr(transcript_publish, "check_remote_visibility",
                        lambda remote: {"verified": True, "visibility": "PUBLIC"})
    refusal = transcript_publish._private_or_refuse(
        clone, "https://github.com/jw-gsl/Transcripts.git")
    assert "PUBLIC" in refusal


def test_private_check_skips_non_github_remotes(tmp_path, monkeypatch):
    def boom(remote):
        raise AssertionError("must not query gh for a file:// remote")
    monkeypatch.setattr(transcript_publish, "check_remote_visibility", boom)
    assert transcript_publish._private_or_refuse(tmp_path, "file:///tmp/x.git") == ""


def test_concurrent_sync_waits_for_lock(tmp_path, remote_repo, transcripts, monkeypatch):
    clone = tmp_path / "clone"
    (transcripts / "Rec1.md").write_text("x\n", encoding="utf-8")
    with transcript_publish._publish_lock(clone) as held:
        assert held
        monkeypatch.setattr(transcript_publish, "_publish_lock",
                            _short_lock(transcript_publish._publish_lock))
        result = _sync(transcripts, remote_repo, clone, md_paths=[transcripts / "Rec1.md"])
    assert not result["ok"] and "another publish" in result["detail"]


def _short_lock(original):
    def wrapper(clone, timeout=300.0):
        return original(clone, timeout=0.2)
    return wrapper


def _merge_file(tmp_path, children):
    path = tmp_path / "merge_groups.json"
    path.write_text(json.dumps([{"outputName": "Merged-x.mp3", "childNames": children}]),
                    encoding="utf-8")
    return path


def test_merge_pieces_are_not_published_and_are_removed(tmp_path, remote_repo, transcripts):
    clone = tmp_path / "clone"
    for name in ("Rec1", "Rec2", "Merged-Rec1-to-Rec2"):
        (transcripts / f"{name}.md").write_text(f"{name}\n", encoding="utf-8")
    _sync(transcripts, remote_repo, clone, all_md=True)  # before the merge existed
    groups = _merge_file(tmp_path, ["Rec1.hda", "Rec2.hda"])
    result = _sync(transcripts, remote_repo, clone, all_md=True, merge_groups=groups)
    assert result["ok"]
    files = _git(remote_repo, "ls-tree", "--name-only", "main").splitlines()
    assert files == ["Merged-Rec1-to-Rec2.md"]


def test_scan_reports_states_counts_and_work(tmp_path, remote_repo, transcripts):
    clone = tmp_path / "clone"
    (transcripts / "Synced.md").write_text("a\n", encoding="utf-8")
    (transcripts / "Edited.md").write_text("b\n", encoding="utf-8")
    (transcripts / "Gone.md").write_text("c\n", encoding="utf-8")
    _sync(transcripts, remote_repo, clone, all_md=True)
    (transcripts / "Edited.md").write_text("b2\n", encoding="utf-8")
    _sync(transcripts, remote_repo, clone, md_paths=[transcripts / "Edited.md"])
    (transcripts / "Edited.md").write_text("b3 edited in Obsidian\n", encoding="utf-8")
    (transcripts / "Gone.md").unlink()
    (transcripts / "Fresh.md").write_text("d\n", encoding="utf-8")
    old = transcripts / "Old.md"
    old.write_text("e\n", encoding="utf-8")
    os.utime(old, (1_000_000, 1_000_000))
    (transcripts / "Piece.md").write_text("f\n", encoding="utf-8")

    report = transcript_publish.scan(
        transcripts_dir=transcripts, clone=clone, remote=f"file://{remote_repo}",
        since=2_000_000, merge_groups=_merge_file(tmp_path, ["Piece.hda"]))
    files = report["files"]
    assert files["Synced"] == {"commits": 1, "state": "synced"}
    assert files["Edited"] == {"commits": 2, "state": "changed"}
    assert files["Fresh"]["state"] == "new"
    assert files["Old"]["state"] == "unpublished"
    assert files["Piece"]["state"] == "excluded"
    assert sorted(Path(p).name for p in report["changed"]) == ["Edited.md", "Fresh.md"]
    assert [Path(p).name for p in report["deleted"]] == ["Gone.md"]


def test_scan_flags_unpushed_commits(tmp_path, remote_repo, transcripts):
    clone = tmp_path / "clone"
    (transcripts / "Rec1.md").write_text("x\n", encoding="utf-8")
    _sync(transcripts, remote_repo, clone, md_paths=[transcripts / "Rec1.md"])
    (transcripts / "Rec1.md").write_text("y\n", encoding="utf-8")
    _sync(transcripts, remote_repo, clone, md_paths=[transcripts / "Rec1.md"], push=False)
    report = transcript_publish.scan(transcripts_dir=transcripts, clone=clone,
                                     remote=f"file://{remote_repo}")
    assert report["files"]["Rec1"] == {"commits": 2, "state": "unpushed"}


@pytest.mark.parametrize("value, expected", [
    ("someone/Transcripts", "https://github.com/someone/Transcripts.git"),
    ("someone/Transcripts.git", "https://github.com/someone/Transcripts.git"),
    ("https://github.com/someone/Transcripts", "https://github.com/someone/Transcripts.git"),
    ("https://github.com/someone/Transcripts/", "https://github.com/someone/Transcripts.git"),
    ("git@github.com:someone/Transcripts.git", "git@github.com:someone/Transcripts.git"),
    ("", None),
    ("not a repo", None),
])
def test_normalize_remote(value, expected):
    assert transcript_publish.normalize_remote(value) == expected


def test_sync_without_a_repository_refuses(tmp_path, transcripts, monkeypatch):
    monkeypatch.setattr(transcript_publish, "configured_remote", lambda: None)
    (transcripts / "Rec1.md").write_text("x\n", encoding="utf-8")
    result = transcript_publish.sync([transcripts / "Rec1.md"], clone=tmp_path / "clone",
                                     transcripts_dir=transcripts)
    assert not result["ok"] and "no repository set" in result["detail"]


def test_switching_repo_starts_a_fresh_clone(tmp_path, remote_repo, transcripts):
    clone = tmp_path / "clone"
    (transcripts / "Rec1.md").write_text("old repo\n", encoding="utf-8")
    _sync(transcripts, remote_repo, clone, md_paths=[transcripts / "Rec1.md"])

    other = tmp_path / "Other.git"
    other.mkdir()
    _git(other, "init", "--bare", "--quiet", "--initial-branch=main")
    (transcripts / "Rec2.md").write_text("new repo\n", encoding="utf-8")
    result = _sync(transcripts, other, clone, md_paths=[transcripts / "Rec2.md"])
    assert result["ok"] and result["pushed"]
    # Only what was published *to the new repo* is there — no old history.
    assert _git(other, "ls-tree", "--name-only", "main").splitlines() == ["Rec2.md"]
    assert _git(other, "rev-list", "--count", "main") == "1"
    assert list(tmp_path.glob("clone.previous-*")), "old clone kept for recovery"


def test_visibility_cache_is_per_repo(tmp_path, monkeypatch):
    clone = tmp_path / "clone"
    clone.mkdir()
    transcript_publish._write_state(clone, visibility="PRIVATE",
                                    visibility_checked_epoch=9e12, visibility_repo="a/One")
    monkeypatch.setattr(transcript_publish, "check_remote_visibility",
                        lambda remote: {"verified": True, "visibility": "PUBLIC"})
    assert transcript_publish._private_or_refuse(clone, "https://github.com/a/One.git") == ""
    assert "PUBLIC" in transcript_publish._private_or_refuse(clone, "https://github.com/b/Two.git")
