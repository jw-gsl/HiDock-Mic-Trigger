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
- State:  ``~/HiDock/.transcripts-git/.hidock-publish-state.json`` — last
  success/error/pending info the app can surface.
- Sync:   copy md -> add -> commit -> pull --rebase -> push. A failure at any
  step leaves the commit local, records it as pending, and the next trigger
  retries. Never force-push.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):  # direct execution without PYTHONPATH
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.transcript_history import git_path  # noqa: E402

# HTTPS, not SSH: this machine authenticates to GitHub via gh's keyring token
# over HTTPS (the hidock-tools repo itself pushes over HTTPS); no GitHub SSH
# key is provisioned here (only a 1Password-sealed key for `mini`, which also
# prompts per push under BatchMode). The SSH URL still works via --remote if a
# key is ever set up.
DEFAULT_REMOTE = "https://github.com/jw-gsl/Transcripts.git"
DEFAULT_TRANSCRIPTS_DIR = Path.home() / "HiDock" / "Raw Transcripts"
PUBLISH_CLONE_DIR = Path.home() / "HiDock" / ".transcripts-git"
STATE_FILE_NAME = ".hidock-publish-state.json"
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_state(clone: Path) -> dict:
    try:
        return json.loads((clone / STATE_FILE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(clone: Path, **fields) -> dict:
    state = _read_state(clone)
    state.update(fields)
    try:
        tmp = clone / (STATE_FILE_NAME + ".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, clone / STATE_FILE_NAME)
    except OSError:
        pass
    return state


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


def ensure_clone(clone: Path | None = None, remote: str = DEFAULT_REMOTE) -> Path | None:
    """Clone the publish repo if absent. Returns the clone path or None."""
    clone = clone or PUBLISH_CLONE_DIR
    if git_path() is None:
        return None
    if _is_clone(clone):
        status, output = _run_git(clone, ["remote", "get-url", "origin"])
        if status != 0:
            return None
        if output.strip() != remote:
            _run_git(clone, ["remote", "set-url", "origin", remote])
        return clone
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
        _record_error(clone if clone.exists() else clone.parent,
                      f"clone failed: {output.strip()[:300]}")
        return None
    _run_git(clone, ["config", "user.name", COMMIT_AUTHOR_NAME])
    _run_git(clone, ["config", "user.email", COMMIT_AUTHOR_EMAIL])
    return clone


def _record_error(where: Path, message: str) -> None:
    _write_state(where, last_error=message, last_error_at=_now_iso())


def _copy_markdown(md_paths: list[Path], clone: Path) -> list[Path]:
    """Copy each .md into the clone root atomically. Returns copied targets."""
    copied: list[Path] = []
    for source in md_paths:
        if source.suffix.lower() != ".md" or not source.is_file():
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
    remote: str = DEFAULT_REMOTE,
    push: bool = True,
) -> dict:
    """Publish transcript markdown to the remote. Never raises.

    Returns ``{"ok": bool, "committed": int, "pushed": bool, "pending": int,
    "detail": str}``. With ``all_md`` every ``*.md`` in ``transcripts_dir``
    is (re)copied, which also picks up edits made outside the app.
    """
    result = {"ok": False, "committed": 0, "pushed": False, "pending": 0, "detail": ""}
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
    sources.extend(Path(p) for p in (md_paths or []))

    _copy_markdown(sources, clone_dir)
    _run_git(clone_dir, ["add", "-A", "--", "*.md"])
    status, output = _run_git(clone_dir, ["status", "--porcelain", "--", "*.md"])
    changed = [line for line in output.splitlines() if line.strip()]
    if status == 0 and not changed:
        # Nothing new — still try to push anything left from a failed run.
        pending = _pending_count(clone_dir)
        result["pending"] = pending
        if pending and push:
            return _do_push(clone_dir, result)
        result["ok"] = True
        result["detail"] = "no changes"
        return result

    title = " ".join(str(reason).split())[:200] or "Transcript update"
    status, output = _run_git(clone_dir, ["commit", "--quiet", "-m", title])
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
    return _do_push(clone_dir, result)


def _do_push(clone_dir: Path, result: dict) -> dict:
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


def check_remote_visibility(remote: str = DEFAULT_REMOTE) -> dict:
    """Ask `gh` whether the target repo is private. Refuses public remotes.

    Transcripts are company meeting content: pushing to a public repo is the
    one failure mode this module must never allow. If `gh` is unavailable the
    caller cannot verify — treated as unknown, and sync stays opt-in anyway.
    """
    try:
        completed = subprocess.run(
            ["gh", "repo", "view", "jw-gsl/Transcripts", "--json", "visibility"],
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
        description="Publish transcript .md files to github.com/jw-gsl/Transcripts",
    )
    parser.add_argument("md_paths", nargs="*", help="transcript .md files to publish")
    parser.add_argument("--all", action="store_true",
                        help="sync every .md in the transcripts folder")
    parser.add_argument("--transcripts-dir", default=None,
                        help=f"override transcripts folder (default {DEFAULT_TRANSCRIPTS_DIR})")
    parser.add_argument("--clone", default=None,
                        help=f"override publish clone dir (default {PUBLISH_CLONE_DIR})")
    parser.add_argument("--remote", default=DEFAULT_REMOTE)
    parser.add_argument("--reason", default="Transcript update")
    parser.add_argument("--no-push", action="store_true",
                        help="commit locally only (offline/dry runs)")
    parser.add_argument("--status", action="store_true",
                        help="print last sync state as JSON and exit")
    parser.add_argument("--check-visibility", action="store_true",
                        help="report whether the remote repo is private, then exit")
    args = parser.parse_args(argv)

    if args.check_visibility:
        print(json.dumps(check_remote_visibility(args.remote)))
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
    )
    print(json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
