"""Snapshot transcript artifacts into a local git repo before rewriting them.

The macOS app snapshots before every speaker-mutating operation, but it does so
in Swift, at the *caller* layer — so protection depended on which caller you
used. Anything driven from the CLI (`transcribe.py rediarize`,
`recluster-with-anchors`, `merge-rediarize`, `split-artifacts`) rewrote a
reviewed transcript with no rollback point at all.

That is not hypothetical. On 2026-07-28 a `rediarize` run invoked directly from
the CLI overwrote Rec79 Part 2's sidecar; only a hand-made copy allowed it to be
put back. The lesson is that the snapshot belongs to the *operation*, not to the
GUI that happens to trigger it.

This is deliberately the same repository, path, and artifact list the Swift side
uses (`.hidock-transcript-history` beside the transcript, holding only
`_diarized.json` / `.md` / `.srt`), so snapshots taken here appear in the app's
History list and can be restored from it. Never audio, never voice-library data,
never a remote.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

HISTORY_DIR_NAME = ".hidock-transcript-history"

# Resolving git is not as simple as picking a path. On macOS `/usr/bin/git` is
# Apple's shim: the file always exists, but running it only works when Xcode
# Command Line Tools are installed — otherwise it exits non-zero with
# "invalid active developer path". A fresh Mac therefore has a git that is
# present and broken, which is the worst case for a best-effort snapshot: the
# failure is swallowed and the user silently has no rollback points.
#
# So probe by *running* each candidate, and prefer a real git on PATH.
_GIT_CANDIDATES = ("/opt/homebrew/bin/git", "/usr/local/bin/git", "/usr/bin/git")
_resolved_git: str | None | object = None  # None = unavailable; object() = unprobed
_UNPROBED = object()
_resolved_git = _UNPROBED


def _probe(path: str) -> bool:
    try:
        completed = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and "git version" in (completed.stdout or "")


def git_path() -> str | None:
    """A git executable that actually runs, or None. Probed once per process."""
    global _resolved_git
    if _resolved_git is not _UNPROBED:
        return _resolved_git  # type: ignore[return-value]
    found: str | None = None
    for candidate in _GIT_CANDIDATES:
        if Path(candidate).exists() and _probe(candidate):
            found = candidate
            break
    if found is None:
        from shutil import which
        discovered = which("git")
        if discovered and _probe(discovered):
            found = discovered
    _resolved_git = found
    return found


def git_availability() -> dict:
    """Whether transcript rollback can work, and what to tell the user if not.

    The app surfaces this at onboarding and before any destructive edit: without
    git there are no rollback points, and the user should learn that up front
    rather than discover it when they need to undo something.
    """
    path = git_path()
    if path:
        return {"available": True, "git_path": path, "reason": None, "remedy": None}
    return {
        "available": False,
        "git_path": None,
        "reason": "No working git found. Transcript edits cannot be versioned, "
                  "so there will be no rollback points.",
        "remedy": "Install Apple's Command Line Tools with `xcode-select --install`, "
                  "or install git (e.g. `brew install git`).",
    }


def reset_git_probe() -> None:
    """Forget the cached probe. For tests, and for after a user installs git."""
    global _resolved_git
    _resolved_git = _UNPROBED


def _recording_stem(diarized_path: Path) -> str:
    return diarized_path.with_suffix("").name.replace("_diarized", "")


def history_artifacts(diarized_path: str | Path) -> list[str]:
    """Filenames this snapshot covers, relative to the transcript directory."""
    path = Path(diarized_path)
    stem = _recording_stem(path)
    return [f"{stem}_diarized.json", f"{stem}.md", f"{stem}.srt"]


def _run_git(root: Path, arguments: list[str]) -> tuple[int, str]:
    repository = root / HISTORY_DIR_NAME
    git = git_path()
    if git is None:
        return -1, "no working git"
    try:
        completed = subprocess.run(
            [git, "--git-dir", str(repository), "--work-tree", str(root), *arguments],
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return -1, str(exc)
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def ensure_repository(diarized_path: str | Path) -> Path | None:
    """Create the history repo if absent. Returns the transcript directory."""
    path = Path(diarized_path)
    root = path.parent
    repository = root / HISTORY_DIR_NAME
    if not repository.exists():
        try:
            # MUST be --bare. `git init <path>` creates <path>/.git, so every
            # later `--git-dir <path>` call fails with "not in a git directory".
            # That is not theoretical: it is exactly how the Swift original
            # silently never took a single snapshot.
            git = git_path()
            if git is None:
                return None
            completed = subprocess.run(
                [git, "init", "--bare", "--quiet", str(repository)],
                capture_output=True, text=True,
            )
        except OSError:
            return None
        if completed.returncode != 0:
            return None
    # A bare-ish history repo needs an identity or `commit` refuses to run.
    if _run_git(root, ["config", "user.name", "HiDock local history"])[0] != 0:
        return None
    if _run_git(root, ["config", "user.email", "history@hidock.local"])[0] != 0:
        return None
    return root


def snapshot(diarized_path: str | Path, reason: str) -> bool:
    """Commit the current artifacts before a caller changes them.

    Returns True when a snapshot was created. Best-effort by design: a failure
    to version must not stop the user's operation, but it is reported so a silent
    loss of protection is visible.
    """
    path = Path(diarized_path)
    if git_path() is None:
        # Say so once, loudly. A missing rollback point is exactly the failure
        # that must not be silent — that is how Rec79 Part 2 was lost.
        print(
            "transcript-history: WARNING no working git, so this edit has no "
            "rollback point. " + (git_availability().get("remedy") or "")
        )
        return False
    root = ensure_repository(path)
    if root is None:
        return False
    artifacts = [name for name in history_artifacts(path) if (root / name).exists()]
    if not artifacts:
        return False
    if _run_git(root, ["add", "--", *artifacts])[0] != 0:
        return False
    # Nothing staged means nothing has changed since the last snapshot.
    if _run_git(root, ["diff", "--cached", "--quiet", "--", *artifacts])[0] == 0:
        return False
    title = " ".join(str(reason).split()) or "Before change"
    status, output = _run_git(root, ["commit", "--quiet", "-m", title, "--", *artifacts])
    if status != 0:
        print(f"transcript-history: snapshot failed: {output.strip()[:200]}")
        return False
    return True


def versions(diarized_path: str | Path) -> list[tuple[str, str]]:
    """(revision, "date · reason") for a transcript, newest first."""
    path = Path(diarized_path)
    root = ensure_repository(path)
    if root is None:
        return []
    status, output = _run_git(
        root,
        [
            "log", "--pretty=format:%H%x1f%ad%x1f%s",
            "--date=format:%d %b %H:%M", "--", history_artifacts(path)[0],
        ],
    )
    if status != 0:
        return []
    rows: list[tuple[str, str]] = []
    for line in output.splitlines():
        fields = line.split("\x1f", 2)
        if len(fields) == 3:
            rows.append((fields[0], f"{fields[1]} · {fields[2]}"))
    return rows
