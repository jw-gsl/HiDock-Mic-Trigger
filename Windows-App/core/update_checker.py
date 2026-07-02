"""Auto-update checker and installer for Windows."""
from __future__ import annotations

import json
import ssl
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable

REPO = "jw-gsl/HiDock-Mic-Trigger"
APP_VERSION = "1.0.0"


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def check_for_update() -> dict | None:
    """Check GitHub for a newer release. Returns release dict or None."""
    try:
        ctx = _ssl_context()
        req = urllib.request.Request(
            f"https://api.github.com/repos/{REPO}/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "HiDock/1.0"},
        )
        resp = urllib.request.urlopen(req, timeout=15, context=ctx)
        release = json.loads(resp.read())

        remote = release["tag_name"].lstrip("v")
        if _is_newer(remote, APP_VERSION):
            return release
    except Exception:
        pass
    return None


def _is_newer(remote: str, current: str) -> bool:
    r = [int(x) for x in remote.split(".")]
    c = [int(x) for x in current.split(".")]
    for a, b in zip(r + [0] * 5, c + [0] * 5):
        if a > b:
            return True
        if a < b:
            return False
    return False


def find_windows_asset(release: dict) -> tuple[str, str] | None:
    """Find the Windows exe asset. Returns (name, download_url) or None."""
    for asset in release.get("assets", []):
        if asset["name"].endswith(".exe"):
            return asset["name"], asset["browser_download_url"]
    return None


def download_update(
    url: str,
    on_progress: Callable[[int, int], None] | None = None,
) -> Path | None:
    """Download the update exe to a temp directory. Returns path or None."""
    try:
        ctx = _ssl_context()
        req = urllib.request.Request(url, headers={"User-Agent": "HiDock/1.0"})
        resp = urllib.request.urlopen(req, timeout=30, context=ctx)
        total = int(resp.headers.get("Content-Length", 0))

        tmp_dir = Path(tempfile.mkdtemp(prefix="hidock-update-"))
        dest = tmp_dir / "HiDock-update.exe"
        downloaded = 0

        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if on_progress:
                    on_progress(downloaded, total)

        return dest
    except Exception:
        return None


def install_and_restart(exe_path: Path) -> bool:
    """Replace the running exe and restart. Works for PyInstaller single-file.

    Must be called from the GUI (main) thread: it quits the Qt application so
    the whole process exits inside the batch script's copy window. Calling
    ``sys.exit`` from a worker thread only kills that thread — the exe stays
    locked and the bat's ``copy`` fails.

    Returns False (without installing) when not running as a frozen
    PyInstaller exe — in a dev run ``sys.executable`` is python.exe and the
    bat would clobber the interpreter.
    """
    if not getattr(sys, "frozen", False):
        return False

    current_exe = Path(sys.executable).resolve()

    # Write a batch script that waits for us to exit, replaces the exe, and relaunches
    bat = exe_path.parent / "update.bat"
    bat.write_text(f"""@echo off
timeout /t 2 /nobreak >nul
copy /y "{exe_path}" "{current_exe}"
start "" "{current_exe}"
rmdir /s /q "{exe_path.parent}"
""", encoding="utf-8")

    subprocess.Popen(["cmd", "/c", str(bat)], creationflags=0x00000008)  # DETACHED_PROCESS

    # Quit the Qt event loop so the process exits (releasing the exe) before
    # the bat's 2-second wait elapses. Falls back to sys.exit when no Qt app
    # exists (e.g. called during interpreter-level teardown).
    try:
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
    except ImportError:
        app = None
    if app is not None:
        app.quit()
        return True
    sys.exit(0)


def install_on_quit(exe_path: Path):
    """Save the update path so it's installed when the app quits."""
    global _pending_update_path
    _pending_update_path = exe_path


_pending_update_path: Path | None = None


def apply_pending_update():
    """Called on app quit — install if an update was downloaded."""
    if _pending_update_path is None:
        return
    install_and_restart(_pending_update_path)
