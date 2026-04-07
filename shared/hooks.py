"""Post-transcription hooks — run user-configured commands after processing.

Supports running arbitrary shell commands after transcription and/or
summarization completes. Useful for:
- Syncing to cloud storage
- Sending notifications (Slack, email)
- Appending to daily notes
- Pushing to external services (Notion, etc.)
- Triggering Obsidian vault sync

The hook receives transcript metadata as environment variables.

Usage:
    from shared.hooks import run_post_transcription_hook

    run_post_transcription_hook(
        transcript_path=Path("~/HiDock/Raw Transcripts/meeting.md"),
        source_path=Path("~/HiDock/Recordings/meeting.mp3"),
        summary={"title": "Meeting", "action_items": [...]},
    )
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def run_post_transcription_hook(
    command: str,
    transcript_path: Path,
    source_path: Path | None = None,
    summary: dict | None = None,
    timeout: int = 30,
) -> bool:
    """Run a post-transcription hook command.

    The command is executed as a shell command with transcript metadata
    available as environment variables:

    - TRANSCRIPT_PATH: Full path to the .md transcript
    - TRANSCRIPT_TITLE: Meeting title (from summary or auto-generated)
    - SOURCE_PATH: Full path to the original audio file
    - SPEAKERS: Comma-separated list of speaker names
    - ACTION_ITEMS_COUNT: Number of action items extracted
    - HAS_SUMMARY: "true" or "false"

    Args:
        command: Shell command to execute.
        transcript_path: Path to the transcript .md file.
        source_path: Path to the original audio file.
        summary: Optional summary dict from LLM extraction.
        timeout: Maximum seconds to wait for the command.

    Returns:
        True if the command succeeded (exit code 0), False otherwise.
    """
    if not command or not command.strip():
        return False

    # Build environment with transcript metadata
    env = dict(os.environ)
    env["TRANSCRIPT_PATH"] = str(transcript_path)

    if source_path:
        env["SOURCE_PATH"] = str(source_path)

    summary = summary or {}
    env["TRANSCRIPT_TITLE"] = summary.get("title", transcript_path.stem)
    env["HAS_SUMMARY"] = "true" if summary.get("summary_text") else "false"

    speakers = summary.get("speakers", [])
    if isinstance(speakers, list):
        env["SPEAKERS"] = ",".join(str(s) for s in speakers)

    action_items = summary.get("action_items", [])
    env["ACTION_ITEMS_COUNT"] = str(len(action_items) if isinstance(action_items, list) else 0)

    # Summary as JSON for advanced hooks
    env["SUMMARY_JSON"] = json.dumps(summary, ensure_ascii=False)

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        if result.returncode != 0:
            print(
                f"Post-transcription hook failed (exit {result.returncode}): "
                f"{result.stderr[:200]}",
                file=sys.stderr,
            )
            return False
        return True

    except subprocess.TimeoutExpired:
        print(f"Post-transcription hook timed out after {timeout}s", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Post-transcription hook error: {e}", file=sys.stderr)
        return False


def run_hooks_pipeline(
    transcript_path: Path,
    source_path: Path | None = None,
    summary: dict | None = None,
    config: Any | None = None,
) -> dict:
    """Run the full post-transcription hooks pipeline.

    Reads hook configuration and runs:
    1. Post-transcription shell command (if configured)
    2. Obsidian vault sync (if configured)

    Args:
        transcript_path: Path to the transcript .md file.
        source_path: Path to the original audio file.
        summary: Optional summary dict.
        config: Optional ConfigStore instance.

    Returns:
        Dict with results of each hook.
    """
    results = {"hook_command": None, "obsidian_sync": None}

    # Load config if not provided
    if config is None:
        try:
            from shared.config_store import get_config
            config = get_config()
        except Exception:
            return results

    # Run post-transcription command
    hook_cmd = config.get("hooks", "post_transcription", "")
    if hook_cmd:
        results["hook_command"] = run_post_transcription_hook(
            hook_cmd, transcript_path, source_path, summary
        )

    # Obsidian sync
    obsidian_enabled = config.get("obsidian", "enabled", False)
    vault_path = config.get("obsidian", "vault_path", "")
    if obsidian_enabled and vault_path:
        try:
            from shared.obsidian import VaultSync
            sync = VaultSync(
                vault_path=vault_path,
                strategy=config.get("obsidian", "sync_strategy", "copy"),
                wikilinks=config.get("obsidian", "wikilinks", True),
                daily_notes=config.get("obsidian", "daily_notes", False),
            )
            vault_dest = sync.sync_transcript(transcript_path)
            results["obsidian_sync"] = vault_dest is not None

            if config.get("obsidian", "daily_notes", False):
                sync.append_to_daily_note(transcript_path)
        except Exception as e:
            print(f"Obsidian sync failed: {e}", file=sys.stderr)
            results["obsidian_sync"] = False

    return results
