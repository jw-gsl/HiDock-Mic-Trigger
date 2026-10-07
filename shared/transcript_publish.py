"""Publish transcript ``.md`` files to the private Transcripts GitHub repo.

Separate by design from ``transcript_history.py``: that repo is a bare,
local-only *rollback* store holding pre-change states of json/md/srt with
"Before …" messages. This one publishes only the reviewed ``.md`` files to a
remote, with edit-log messages ("Renamed speaker X→Y"). Same hardening
philosophy though — best-effort, never block the user's edit, never let a
network or auth failure surface as an error in the middle of a rename.

Design doc: docs/PLAN-transcripts-github-sync-2026-09-24.md

Layout:
- Clone:  ``~/HiDock/.transcripts-git/`` — a normal working clone holding
  nothing but ``.md`` files copied (not linked) from Raw Transcripts, so raw
  json, srt, audio and the history repo can never leak into the publish repo.
- State:  ``~/HiDock/.transcripts-git.state.json`` — last success/error/
  pending info the app can surface. Kept *beside* the clone, not in it, so a
  failed first clone can still record why (the clone dir doesn't exist yet).
- Lock:   ``~/HiDock/.transcripts-git.lock`` — the app and a terminal
  ``transcribe.py`` run must never drive git in the same clone at once.
- Sync:   copy md -> add -> commit -> pull --rebase -> push. A failure at any
  step leaves the commit local, records it as pending, and the next trigger
  retries. Never force-push.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):  # direct execution without PYTHONPATH
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.transcript_history import git_path  # noqa: E402

# The target repo is a user setting (Settings → Transcripts on GitHub →
# Repository), stored in the app's UserDefaults so terminal runs publish to
# the same place. HTTPS by default: authentication goes through gh's keyring
# token; an SSH URL also works if one is entered.
REMOTE_DEFAULT_KEY = "transcriptsGitHubRepo"
DEFAULT_TRANSCRIPTS_DIR = Path.home() / "HiDock" / "Raw Transcripts"
PUBLISH_CLONE_DIR = Path.home() / "HiDock" / ".transcripts-git"
MERGE_GROUPS_FILE = Path.home() / "HiDock" / "merge_groups.json"
# Below this many sources, or above this share of the published files, a
# prune is treated as "the transcripts folder looks wrong" and skipped.
PRUNE_MAX_FRACTION = 0.2
VISIBILITY_RECHECK_SECONDS = 24 * 60 * 60
COMMIT_AUTHOR_NAME = "HiDock"
COMMIT_AUTHOR_EMAIL = "transcripts@hidock.local"


APP_BUNDLE_ID = "com.hidock.tools.hidock-mic-trigger"
ENABLED_DEFAULT_KEY = "publishTranscriptsToGitHub"


def publishing_enabled() -> bool:
    """Whether the user has switched publishing on in the app menu.

    The kill-switch lives in the macOS app's UserDefaults (default off).
    Automatic callers (transcribe.py) must check this before syncing; an
    explicit `transcript_publish --all` run from a terminal is consent enough.
    """
    env = os.environ.get("HIDOCK_PUBLISH_TO_GITHUB")
    if env is not None:
        return env.lower() in ("1", "true", "yes")
    try:
        completed = subprocess.run(
            ["defaults", "read", APP_BUNDLE_ID, ENABLED_DEFAULT_KEY],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and completed.stdout.strip() == "1"


def normalize_remote(value: str | None) -> str | None:
    """``owner/repo``, a github.com URL or an SSH URL → a git remote URL."""
    text = (value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", text):
        name = text[:-4] if text.endswith(".git") else text
        return f"https://github.com/{name}.git"
    if re.match(r"https?://github\.com/[^/]+/[^/]+?/?$", text):
        text = text.rstrip("/")
        return text if text.endswith(".git") else text + ".git"
    if text.startswith(("git@", "ssh://", "https://", "http://", "file://")):
        return text
    return None


def configured_remote() -> str | None:
    """The repo the user chose in the app (or $HIDOCK_TRANSCRIPTS_REMOTE)."""
    env = os.environ.get("HIDOCK_TRANSCRIPTS_REMOTE")
    if env:
        return normalize_remote(env)
    try:
        completed = subprocess.run(
            ["defaults", "read", APP_BUNDLE_ID, REMOTE_DEFAULT_KEY],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return normalize_remote(completed.stdout.strip())


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def state_path(clone: Path) -> Path:
    return clone.with_name(clone.name + ".state.json")


def _read_state(clone: Path) -> dict:
    try:
        return json.loads(state_path(clone).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(clone: Path, **fields) -> dict:
    state = _read_state(clone)
    state.update(fields)
    target = state_path(clone)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass
    return state


@contextlib.contextmanager
def _publish_lock(clone: Path, timeout: float = 300.0):
    """Exclusive cross-process lock around one sync. Yields False on timeout."""
    lock_file = clone.with_name(clone.name + ".lock")
    try:
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_file, "w")
    except OSError:
        yield True  # can't lock: same behaviour as before locking existed
        return
    deadline = time.monotonic() + timeout
    acquired = False
    try:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.5)
        yield acquired
    finally:
        if acquired:
            fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def _run_git(cwd: Path, arguments: list[str], timeout: float = 120.0) -> tuple[int, str]:
    git = git_path()
    if git is None:
        return -1, "no working git"
    env = dict(os.environ)
    # Non-interactive by nature of being a subprocess, but make it explicit:
    # a hung password/ssh prompt on a background sync would pin the caller.
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_SSH_COMMAND"] = env.get(
        "GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=10"
    )
    try:
        completed = subprocess.run(
            [git, *arguments],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return -1, f"git {' '.join(arguments)} timed out"
    except OSError as exc:
        return -1, str(exc)
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def _is_clone(clone: Path) -> bool:
    return (clone / ".git").is_dir()


def ensure_clone(clone: Path | None = None, remote: str | None = None) -> Path | None:
    """Clone the publish repo if absent. Returns the clone path or None."""
    clone = clone or PUBLISH_CLONE_DIR
    if git_path() is None or not remote:
        return None
    if _is_clone(clone):
        status, output = _run_git(clone, ["remote", "get-url", "origin"])
        if status != 0:
            return None
        if output.strip() == remote:
            return clone
        # A different repo was chosen. Never repoint this clone: its history
        # belongs to the old repo and would be pushed into the new one. Set it
        # aside (unpushed commits stay recoverable) and start fresh.
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        try:
            clone.rename(clone.with_name(f"{clone.name}.previous-{stamp}"))
        except OSError as exc:
            _record_error(clone, f"could not switch repo: {exc}")
            return None
        _write_state(clone, last_error=None, pending=0, visibility=None,
                     visibility_checked_epoch=0, visibility_repo=None)
    try:
        clone.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    status, output = _run_git(clone.parent, ["clone", "--quiet", remote, str(clone)])
    if status != 0:
        # A brand-new empty GitHub repo still clones fine, so failure here is
        # auth/network/missing-repo. Leave nothing half-made.
        if _is_clone(clone) is False and clone.exists() and not any(clone.iterdir()):
            try:
                clone.rmdir()
            except OSError:
                pass
        _record_error(clone, f"clone failed: {output.strip()[:300]}")
        return None
    _run_git(clone, ["config", "user.name", COMMIT_AUTHOR_NAME])
    _run_git(clone, ["config", "user.email", COMMIT_AUTHOR_EMAIL])
    # An empty GitHub repo clones with HEAD on whatever init.defaultBranch
    # says locally (often "master"). Publish to "main" regardless.
    status, _ = _run_git(clone, ["rev-parse", "--verify", "HEAD"])
    if status != 0:
        _run_git(clone, ["symbolic-ref", "HEAD", "refs/heads/main"])
    return clone


def _record_error(where: Path, message: str) -> None:
    _write_state(where, last_error=message, last_error_at=_now_iso())


def _copy_markdown(md_paths: list[Path], clone: Path) -> list[Path]:
    """Copy each .md into the clone root atomically. Returns copied targets.

    A named source that no longer exists (removed, merged away, renamed) has
    its published copy deleted, so the repo mirrors the transcripts folder
    instead of accumulating stale duplicates.
    """
    copied: list[Path] = []
    for source in md_paths:
        if source.suffix.lower() != ".md":
            continue
        if not source.exists():
            stale = clone / source.name
            if stale.is_file():
                try:
                    stale.unlink()
                except OSError:
                    pass
            continue
        if not source.is_file():
            continue
        target = clone / source.name
        try:
            tmp = target.with_name(target.name + ".hidock-tmp")
            tmp.write_bytes(source.read_bytes())
            os.replace(tmp, target)
            copied.append(target)
        except OSError:
            continue
    return copied


def _prune_missing(sources: list[Path], clone: Path) -> str:
    """Delete published .md files whose source is gone (``--all`` runs only).

    Guarded: an empty or mostly-missing transcripts folder (unmounted disk,
    wrong path) must not wipe the published repo, so a prune that would
    remove more than PRUNE_MAX_FRACTION of the files is skipped.
    """
    wanted = {path.name for path in sources}
    published = sorted(clone.glob("*.md"))
    stale = [path for path in published if path.name not in wanted]
    if not stale:
        return ""
    if not wanted or len(stale) > max(1, int(len(published) * PRUNE_MAX_FRACTION)):
        return (f"skipped removing {len(stale)} of {len(published)} published transcripts: "
                "too many are missing from the transcripts folder")
    for path in stale:
        try:
            path.unlink()
        except OSError:
            pass
    return ""


def superseded_stems(merge_groups: Path | None = None) -> set[str]:
    """Recording stems replaced by a merged recording.

    A merge's pieces keep their own transcripts locally, but the merged
    transcript is the one that matters; publishing both puts the same meeting
    in the repo twice. Read from the app's merge_groups.json so terminal runs
    agree with the app.
    """
    path = merge_groups or MERGE_GROUPS_FILE
    try:
        groups = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    stems: set[str] = set()
    for group in groups if isinstance(groups, list) else []:
        for name in group.get("childNames") or []:
            stems.add(Path(str(name)).stem)
    return stems


def _drop_superseded(clone: Path, superseded: set[str]) -> None:
    for stem in superseded:
        published = clone / f"{stem}.md"
        if published.is_file():
            try:
                published.unlink()
            except OSError:
                pass


def _web_url(remote: str) -> str | None:
    slug = _remote_slug(remote)
    return f"https://github.com/{slug}" if slug else None


def scan(
    transcripts_dir: Path | None = None,
    clone: Path | None = None,
    remote: str | None = None,
    since: float | None = None,
    merge_groups: Path | None = None,
) -> dict:
    """Compare the transcripts folder with the published clone. Never raises.

    Read-only: nothing is copied or committed. Returns per-transcript state
    for the app's file list plus the work a sync would have to do, which is
    how edits made outside HiDock (Obsidian, a text editor, a terminal run
    with publishing off) or a missed trigger still get published.

    States: ``synced`` (published content matches), ``unpushed`` (committed,
    not yet on GitHub), ``changed`` (local differs from published),
    ``new`` (never published and modified after ``since``), ``unpublished``
    (never published, older than ``since``), ``excluded`` (merge piece).
    """
    directory = transcripts_dir or DEFAULT_TRANSCRIPTS_DIR
    clone_dir = clone or PUBLISH_CLONE_DIR
    remote = remote or configured_remote() or ""
    result: dict = {"ok": False, "web_url": _web_url(remote), "branch": "main",
                    "files": {}, "changed": [], "deleted": []}
    try:
        sources = {path.name: path for path in directory.glob("*.md")}
    except OSError:
        return result
    superseded = superseded_stems(merge_groups)
    is_clone = _is_clone(clone_dir)
    commits: dict[str, int] = {}
    unpushed: set[str] = set()
    if is_clone:
        _, branch = _run_git(clone_dir, ["rev-parse", "--abbrev-ref", "HEAD"])
        result["branch"] = branch.strip() or "main"
        status, log = _run_git(clone_dir, ["log", "--pretty=format:", "--name-only", "HEAD"])
        if status == 0:
            for line in log.splitlines():
                if line.endswith(".md"):
                    commits[line] = commits.get(line, 0) + 1
        status, log = _run_git(clone_dir, ["log", "--pretty=format:", "--name-only",
                                           f"origin/{result['branch']}..HEAD"])
        if status == 0:
            unpushed = {line for line in log.splitlines() if line.endswith(".md")}
        elif commits:
            unpushed = set(commits)  # nothing on the remote yet
    for name, source in sorted(sources.items()):
        stem = source.stem
        published = clone_dir / name
        entry = {"commits": commits.get(name, 0)}
        if stem in superseded:
            entry["state"] = "excluded"
        elif published.is_file():
            try:
                same = published.read_bytes() == source.read_bytes()
            except OSError:
                same = False
            if not same:
                entry["state"] = "changed"
                result["changed"].append(str(source))
            elif name in unpushed:
                entry["state"] = "unpushed"
            else:
                entry["state"] = "synced"
        else:
            try:
                modified = source.stat().st_mtime
            except OSError:
                modified = 0.0
            if since is None or modified >= since:
                entry["state"] = "new"
                result["changed"].append(str(source))
            else:
                entry["state"] = "unpublished"
        result["files"][stem] = entry
    if is_clone:
        for published in clone_dir.glob("*.md"):
            if published.name not in sources or published.stem in superseded:
                result["deleted"].append(str(directory / published.name))
    result["ok"] = True
    return result


def _remote_slug(remote: str) -> str | None:
    match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", remote)
    return match.group(1) if match else None


def _private_or_refuse(clone: Path, remote: str) -> str:
    """Return an error message if the GitHub remote is public, else "".

    Checked before every push (cached for a day), not just when publishing is
    switched on: "Sync Now" and terminal runs must not be a way round it, and
    a repo can be made public later. Non-GitHub remotes (tests) are skipped,
    and an unverifiable check doesn't block — gh may simply be missing.
    """
    slug = _remote_slug(remote or "")
    if slug is None:
        return ""
    state = _read_state(clone)
    checked = state.get("visibility_checked_epoch") or 0
    # The cached answer is only good for the repo it was asked about.
    visibility = state.get("visibility") if state.get("visibility_repo") == slug else None
    if visibility != "PRIVATE" or time.time() - checked > VISIBILITY_RECHECK_SECONDS:
        result = check_remote_visibility(remote)
        if result["verified"]:
            visibility = result["visibility"]
            _write_state(clone, visibility=visibility, visibility_checked_epoch=time.time(),
                         visibility_repo=slug)
    if visibility == "PUBLIC":
        return f"refusing to push: github.com/{slug} is PUBLIC — make it private first"
    return ""


def _pending_count(clone: Path) -> int:
    """Commits made locally but not present on the remote branch."""
    status, out = _run_git(clone, ["rev-list", "@{u}..HEAD", "--count"])
    if status == 0 and out.strip().isdigit():
        return int(out.strip())
    # No upstream configured — count against origin/<branch> instead.
    _, branch = _run_git(clone, ["rev-parse", "--abbrev-ref", "HEAD"])
    branch = branch.strip() or "main"
    status, out = _run_git(clone, ["rev-list", f"origin/{branch}..HEAD", "--count"])
    if status == 0:
        try:
            return int(out.strip())
        except ValueError:
            return 0
    # No origin ref at all — everything is pending.
    status, out = _run_git(clone, ["rev-list", "HEAD", "--count"])
    return int(out.strip()) if status == 0 and out.strip().isdigit() else 0


def sync(
    md_paths: list[str | Path] | None = None,
    reason: str = "Transcript update",
    *,
    all_md: bool = False,
    transcripts_dir: Path | None = None,
    clone: Path | None = None,
    remote: str | None = None,
    push: bool = True,
    body: str = "",
    merge_groups: Path | None = None,
) -> dict:
    """Publish transcript markdown to the remote. Never raises.

    Returns ``{"ok": bool, "committed": int, "pushed": bool, "pending": int,
    "detail": str}``. With ``all_md`` every ``*.md`` in ``transcripts_dir``
    is (re)copied, which also picks up edits made outside the app.
    """
    result = {"ok": False, "committed": 0, "pushed": False, "pending": 0, "detail": ""}
    remote = remote or configured_remote()
    if not remote:
        result["detail"] = "no repository set (Settings → Transcripts on GitHub → Repository)"
        return result
    with _publish_lock(clone or PUBLISH_CLONE_DIR) as acquired:
        if not acquired:
            result["detail"] = "another publish is still running; will retry"
            return result
        return _sync_locked(result, md_paths, reason, all_md=all_md,
                            transcripts_dir=transcripts_dir, clone=clone,
                            remote=remote, push=push, body=body,
                            merge_groups=merge_groups)


def _sync_locked(result, md_paths, reason, *, all_md, transcripts_dir, clone,
                 remote, push, body, merge_groups) -> dict:
    clone_dir = ensure_clone(clone, remote)
    if clone_dir is None:
        result["detail"] = "publish clone unavailable (no git, or clone failed)"
        return result

    sources: list[Path] = []
    if all_md:
        directory = transcripts_dir or DEFAULT_TRANSCRIPTS_DIR
        try:
            sources = sorted(directory.glob("*.md"))
        except OSError:
            sources = []
    superseded = superseded_stems(merge_groups)
    prune_warning = ""
    if all_md:
        prune_warning = _prune_missing(sources, clone_dir)
    sources.extend(Path(p) for p in (md_paths or []))
    sources = [path for path in sources if path.stem not in superseded]

    _copy_markdown(sources, clone_dir)
    _drop_superseded(clone_dir, superseded)
    _run_git(clone_dir, ["add", "-A", "--", "*.md"])
    status, output = _run_git(clone_dir, ["status", "--porcelain", "--", "*.md"])
    changed = [line for line in output.splitlines() if line.strip()]
    if status == 0 and not changed:
        # Nothing new — still try to push anything left from a failed run.
        pending = _pending_count(clone_dir)
        result["pending"] = pending
        if pending and push:
            return _do_push(clone_dir, result, remote)
        result["ok"] = True
        result["detail"] = prune_warning or "no changes"
        return result

    title = " ".join(str(reason).split())[:200] or "Transcript update"
    message = ["-m", title]
    if body.strip():
        message += ["-m", body.strip()[:8000]]
    status, output = _run_git(clone_dir, ["commit", "--quiet", *message])
    if status != 0:
        result["detail"] = f"commit failed: {output.strip()[:300]}"
        _record_error(clone_dir, result["detail"])
        return result
    result["committed"] = 1

    if not push:
        result["ok"] = True
        result["pending"] = _pending_count(clone_dir)
        result["detail"] = "committed locally (push disabled)"
        return result
    result = _do_push(clone_dir, result, remote)
    if prune_warning and result["ok"]:
        result["detail"] = prune_warning
    return result


def _do_push(clone_dir: Path, result: dict, remote: str | None = None) -> dict:
    refusal = _private_or_refuse(clone_dir, remote)
    if refusal:
        result["detail"] = refusal
        result["pending"] = _pending_count(clone_dir)
        _record_error(clone_dir, refusal)
        return result
    _, branch = _run_git(clone_dir, ["rev-parse", "--abbrev-ref", "HEAD"])
    branch = branch.strip() or "main"
    # Rebase onto the remote only when it has commits; a brand-new remote
    # branch has nothing to pull and `pull --rebase` would fail.
    status, _ = _run_git(clone_dir, ["rev-parse", "--verify", f"origin/{branch}"])
    if status == 0:
        status, output = _run_git(clone_dir, ["pull", "--rebase", "--quiet", "origin", branch])
        if status != 0:
            # Real conflict (e.g. edited on github.com). Keep the local
            # commit, abort the rebase, surface it — never force-push.
            _run_git(clone_dir, ["rebase", "--abort"])
            result["detail"] = f"pull --rebase failed (remote diverged): {output.strip()[:300]}"
            result["pending"] = _pending_count(clone_dir)
            _record_error(clone_dir, result["detail"])
            return result
    status, output = _run_git(clone_dir, ["push", "--quiet", "origin", branch])
    result["pending"] = _pending_count(clone_dir)
    if status != 0:
        result["detail"] = f"push failed: {output.strip()[:300]}"
        _record_error(clone_dir, result["detail"])
        return result
    result["ok"] = True
    result["pushed"] = True
    _write_state(clone_dir, last_success=_now_iso(), last_error=None,
                 pending=result["pending"])
    return result


def status(clone: Path | None = None) -> dict:
    """Last sync state for the app to surface. Never raises."""
    clone_dir = clone or PUBLISH_CLONE_DIR
    state = _read_state(clone_dir)
    if _is_clone(clone_dir):
        state["pending"] = _pending_count(clone_dir)
        state["clone_ok"] = True
    else:
        state["clone_ok"] = False
    return state


def check_remote_visibility(remote: str | None = None) -> dict:
    """Ask `gh` whether the target repo is private. Refuses public remotes.

    Transcripts are company meeting content: pushing to a public repo is the
    one failure mode this module must never allow. If `gh` is unavailable the
    caller cannot verify — treated as unknown, and sync stays opt-in anyway.
    """
    slug = _remote_slug(remote or "")
    if slug is None:
        return {"verified": False, "visibility": None}
    gh = next((path for path in ("/opt/homebrew/bin/gh", "/usr/local/bin/gh", "/usr/bin/gh")
               if os.access(path, os.X_OK)), "gh")
    try:
        completed = subprocess.run(
            [gh, "repo", "view", slug, "--json", "visibility"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return {"verified": False, "visibility": None}
    if completed.returncode != 0:
        return {"verified": False, "visibility": None}
    try:
        visibility = json.loads(completed.stdout).get("visibility")
    except ValueError:
        return {"verified": False, "visibility": None}
    return {"verified": True, "visibility": visibility}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Publish transcript .md files to the private GitHub repo set in HiDock",
    )
    parser.add_argument("md_paths", nargs="*", help="transcript .md files to publish")
    parser.add_argument("--all", action="store_true",
                        help="sync every .md in the transcripts folder")
    parser.add_argument("--transcripts-dir", default=None,
                        help=f"override transcripts folder (default {DEFAULT_TRANSCRIPTS_DIR})")
    parser.add_argument("--clone", default=None,
                        help=f"override publish clone dir (default {PUBLISH_CLONE_DIR})")
    parser.add_argument("--remote", default=None,
                        help="owner/repo or git URL (default: the repo set in HiDock)")
    parser.add_argument("--reason", default="Transcript update")
    parser.add_argument("--body", default="",
                        help="commit message body (e.g. per-transcript edit summary)")
    parser.add_argument("--no-push", action="store_true",
                        help="commit locally only (offline/dry runs)")
    parser.add_argument("--status", action="store_true",
                        help="print last sync state as JSON and exit")
    parser.add_argument("--scan", action="store_true",
                        help="report per-transcript publish state and pending work as JSON")
    parser.add_argument("--since", type=float, default=None,
                        help="with --scan: never-published transcripts older than this "
                             "epoch are left alone (publishing was enabled after them)")
    parser.add_argument("--check-visibility", action="store_true",
                        help="report whether the remote repo is private, then exit")
    args = parser.parse_args(argv)
    if args.remote and normalize_remote(args.remote) is None:
        print(f"transcript-publish: not a repository: {args.remote}", file=sys.stderr)
        return 2
    args.remote = normalize_remote(args.remote) or configured_remote()

    if args.check_visibility:
        print(json.dumps(check_remote_visibility(args.remote)))
        return 0
    if args.scan:
        print(json.dumps(scan(
            transcripts_dir=Path(args.transcripts_dir) if args.transcripts_dir else None,
            clone=Path(args.clone) if args.clone else None,
            remote=args.remote,
            since=args.since,
        )))
        return 0
    if args.status:
        print(json.dumps(status(Path(args.clone) if args.clone else None)))
        return 0

    for path in args.md_paths:
        if Path(path).suffix.lower() != ".md":
            print(f"transcript-publish: refusing non-markdown input: {path}",
                  file=sys.stderr)
            return 2

    result = sync(
        args.md_paths,
        args.reason,
        all_md=args.all,
        transcripts_dir=Path(args.transcripts_dir) if args.transcripts_dir else None,
        clone=Path(args.clone) if args.clone else None,
        remote=args.remote,
        push=not args.no_push,
        body=args.body,
    )
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
