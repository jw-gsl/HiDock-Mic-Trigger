"""Check that this Mac has everything the pipeline needs, and say what to fix.

Written after a migration (2026-10-14) where every one of these failed
silently on the new Mac and the first sign was a worse transcript:

* Keychain secrets don't migrate: the Nemotron token was missing, so speaker
  detection got HTTP 401 and a whole meeting landed under one speaker.
* The voice-naming model's code (a git clone beside the weights) and the
  weights themselves weren't copied, so naming and Refine stopped.
* ``onnxruntime`` was missing from the new venv, so TitaNet quietly fell back
  to MFCC embeddings.
* ``gh`` wasn't signed in, so transcript publishing stopped.
* The old Mac was still running and published the same recording, so the two
  clashed on GitHub.

Each check returns ``{"id", "ok", "title", "detail", "fix"}``. Checks never
raise: a check that can't run reports itself as failing, with the reason.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
from typing import Any, Callable

MACHINE_TRAILER = "HiDock-Machine"


def machine_id() -> str:
    """A short stable id for this Mac, from its hardware UUID.

    Not the hostname: Migration Assistant copies the computer name, so the
    old and new Macs usually share one.
    """
    try:
        out = subprocess.run(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                             capture_output=True, text=True, timeout=10).stdout
        uuid = next(line.split('"')[-2] for line in out.splitlines() if "IOPlatformUUID" in line)
    except Exception:  # noqa: BLE001 - fall back to something stable per user
        uuid = os.uname().nodename
    return hashlib.sha256(uuid.encode()).hexdigest()[:10]


def _check(id_: str, title: str, fn: Callable[[], tuple[bool, str, str]]) -> dict:
    try:
        ok, detail, fix = fn()
    except Exception as exc:  # noqa: BLE001
        ok, detail, fix = False, f"check failed: {exc}", ""
    return {"id": id_, "ok": ok, "title": title, "detail": detail, "fix": "" if ok else fix}


def _backends() -> dict:
    from shared.models import load_pipeline_backends
    return load_pipeline_backends()


def check_python_packages() -> tuple[bool, str, str]:
    backends = _backends()
    needed = {"onnxruntime": "onnxruntime", "torch": "torch", "numpy": "numpy"}
    if backends.get("transcription") == "parakeet":
        needed["parakeet_mlx"] = "parakeet-mlx"
    if backends.get("diarization") == "sortformer":
        needed["nemo"] = "nemo-toolkit"
    if backends.get("vad") == "ten":
        needed["ten_vad"] = "ten-vad"
    missing = [pip for module, pip in needed.items() if importlib.util.find_spec(module) is None]
    if missing:
        return (False, "missing: " + ", ".join(missing),
                "Run transcription-pipeline/setup-venv.sh, or install them into "
                "transcription-pipeline/.venv with pip.")
    return True, f"{len(needed)} required packages installed", ""


def check_speaker_embedding() -> tuple[bool, str, str]:
    from shared.voice_library_lite import _get_speaker_embed_session
    if importlib.util.find_spec("onnxruntime") is None:
        return False, "onnxruntime isn't installed, so voices fall back to crude MFCCs", \
            "Install onnxruntime into the pipeline venv."
    if _get_speaker_embed_session() is None:
        return False, "the TitaNet model file isn't installed", \
            "Open Models in the app and download TitaNet (Speaker Embeddings)."
    return True, "TitaNet loads", ""


def check_voice_naming() -> tuple[bool, str, str]:
    from shared.voice_candidate_review import load_candidate_config
    config = load_candidate_config()
    if not config.get("available"):
        reason = str(config.get("reason") or "not configured")
        if "model" in reason:
            return False, f"the voice-naming model file is missing ({config.get('model_path')})", \
                "Open Models in the app and download ReDimNet2-B6."
        if "library" in reason:
            return False, "the voice library file is missing", \
                "Copy ~/HiDock/Voice Library Candidates from your old Mac."
        if reason == "candidate_not_configured":
            return True, "no voice-naming library set up (optional)", ""
        return False, reason, "Check ~/HiDock/Voice Library Candidates/active.json."
    if config.get("model_key") == "redimnet2_b6":
        from shared.voice_library_lite import _ReDimNet2Session
        repo = _ReDimNet2Session._DEFAULT_REPO
        if not (repo / "redimnet2" / "redimnet2.py").is_file():
            return False, f"ReDimNet2's code is missing ({repo})", \
                "Open Models in the app and download ReDimNet2-B6 again; it fetches the code too."
    return True, f"{config.get('model_key')} library ready", ""


def check_nemotron() -> tuple[bool, str, str]:
    if _backends().get("diarization") != "nemotron":
        return True, "not in use", ""
    from shared.diarize_nemotron import _auth_token, available
    if not _auth_token() and os.environ.get("HIDOCK_FROM_APP") != "1":
        return True, "token lives in the app's Keychain; the app checks it", ""
    ok, why = available()
    fix = ("Paste the token from ~/.nemo-diar-token on the Spark into Models → Nemotron "
           "(it lives in the Keychain, which doesn't move with a migration).")
    if "reach" in why:
        fix = "Turn the Spark on and check this Mac is on the same network (Tailscale)."
    return ok, why, fix


def _publishing_on() -> bool:
    env = {k: v for k, v in os.environ.items() if k != "HIDOCK_PUBLISH_TO_GITHUB"}
    from shared.transcript_publish import APP_BUNDLE_ID, ENABLED_DEFAULT_KEY
    out = subprocess.run(["defaults", "read", APP_BUNDLE_ID, ENABLED_DEFAULT_KEY],
                         capture_output=True, text=True, timeout=15, env=env)
    return out.returncode == 0 and out.stdout.strip() == "1"


def check_github_publishing() -> tuple[bool, str, str]:
    if not _publishing_on():
        return True, "publishing is off", ""
    from shared.transcript_publish import configured_remote
    remote = configured_remote()
    if not remote:
        return False, "no repository chosen", "Settings → Transcripts on GitHub → Repository."
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    out = subprocess.run(["git", "ls-remote", "--heads", remote], capture_output=True,
                         text=True, timeout=30, env=env)
    if out.returncode != 0:
        return False, "can't sign in to GitHub from this Mac", \
            "Run `gh auth login -h github.com` then `gh auth setup-git` in Terminal."
    return True, "GitHub reachable", ""


def check_other_mac_publishing(days: int = 14) -> tuple[bool, str, str]:
    """Whether another Mac has published transcripts recently.

    Two Macs running HiDock both download, transcribe and publish the same
    recordings and then clash on GitHub. Commits carry this Mac's id in a
    ``HiDock-Machine:`` trailer; recent ones from a different id mean the other
    Mac is still running. (Commits made before the trailer existed carry none,
    so this only spots Macs running an updated app.)
    """
    from shared.transcript_publish import PUBLISH_CLONE_DIR
    if not (PUBLISH_CLONE_DIR / ".git").exists() or not _publishing_on():
        return True, "not publishing", ""
    out = subprocess.run(
        ["git", "-C", str(PUBLISH_CLONE_DIR), "log", f"--since={days}.days", "origin/HEAD",
         f"--format=%(trailers:key={MACHINE_TRAILER},valueonly)"],
        capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        out = subprocess.run(
            ["git", "-C", str(PUBLISH_CLONE_DIR), "log", f"--since={days}.days", "origin/main",
             f"--format=%(trailers:key={MACHINE_TRAILER},valueonly)"],
            capture_output=True, text=True, timeout=30)
    me = machine_id()
    others = {line.split()[0] for line in out.stdout.splitlines() if line.strip()} - {me}
    if others:
        return False, f"{len(others)} other Mac(s) published in the last {days} days", \
            "Quit HiDock on the old Mac, or turn off its auto-download and publishing."
    return True, "only this Mac is publishing", ""


CHECKS: list[tuple[str, str, Callable[[], tuple[bool, str, str]]]] = [
    ("python", "Pipeline Python packages", check_python_packages),
    ("embedding", "Speaker embeddings (TitaNet)", check_speaker_embedding),
    ("naming", "Voice naming library", check_voice_naming),
    ("nemotron", "Nemotron speaker detection", check_nemotron),
    ("github", "Transcripts on GitHub", check_github_publishing),
    ("other_mac", "Only this Mac publishing", check_other_mac_publishing),
]


def run() -> dict[str, Any]:
    checks = [_check(id_, title, fn) for id_, title, fn in CHECKS]
    return {"ok": all(c["ok"] for c in checks), "machine": machine_id(), "checks": checks}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
