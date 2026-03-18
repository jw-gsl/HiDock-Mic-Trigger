"""USB sync — wraps the Windows-Script extractor via subprocess.

This mirrors how the macOS app shells out to usb-extractor/extractor.py.
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from core.config import EXTRACTOR_DIR, EXTRACTOR_PYTHON, EXTRACTOR_SCRIPT


@dataclass
class SyncRecording:
    name: str
    create_date: str = ""
    create_time: str = ""
    length: int = 0
    duration: float = 0.0
    version: int = 0
    mode: str = ""
    signature: str = ""
    output_path: str = ""
    output_name: str = ""
    downloaded: bool = False
    local_exists: bool = False
    downloaded_at: str | None = None
    last_error: str | None = None
    status: str = ""
    human_length: str = ""
    # Transcription state (merged separately)
    transcribed: bool = False
    transcript_path: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "SyncRecording":
        return cls(
            name=d.get("name", ""),
            create_date=d.get("createDate", ""),
            create_time=d.get("createTime", ""),
            length=d.get("length", 0),
            duration=d.get("duration", 0.0),
            version=d.get("version", 0),
            mode=d.get("mode", ""),
            signature=d.get("signature", ""),
            output_path=d.get("outputPath", ""),
            output_name=d.get("outputName", ""),
            downloaded=d.get("downloaded", False),
            local_exists=d.get("localExists", False),
            downloaded_at=d.get("downloadedAt"),
            last_error=d.get("lastError"),
            status=d.get("status", ""),
            human_length=d.get("humanLength", ""),
        )


@dataclass
class SyncRecordingEntry:
    recording: SyncRecording
    device_product_id: int = 0
    device_name: str = ""


def extractor_ready() -> tuple[bool, str]:
    """Check if the Windows-Script extractor is available."""
    if not EXTRACTOR_SCRIPT.exists():
        return False, f"Extractor not found: {EXTRACTOR_SCRIPT}"
    if not EXTRACTOR_PYTHON.exists():
        return False, f"Extractor venv not found: {EXTRACTOR_PYTHON}\nRun: cd Windows-Script && setup.bat"
    return True, ""


def run_extractor(
    arguments: list[str],
    product_id: int | None = None,
    timeout: float = 30,
) -> dict[str, Any]:
    """Run the extractor and return parsed JSON output.

    Raises RuntimeError on failure.
    """
    cmd = [str(EXTRACTOR_PYTHON), str(EXTRACTOR_SCRIPT)]
    if product_id is not None:
        cmd.extend(["--product-id", str(product_id)])
    cmd.extend(arguments)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(EXTRACTOR_DIR),
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Extractor timed out after {timeout}s")
    except FileNotFoundError:
        raise RuntimeError(f"Python not found: {EXTRACTOR_PYTHON}")

    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(f"Extractor failed (exit {result.returncode}): {stderr}")

    stdout = result.stdout.strip()
    if not stdout:
        return {}

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"Invalid JSON from extractor: {stdout[:200]}")


def run_extractor_async(
    arguments: list[str],
    product_id: int | None = None,
    timeout: float = 30,
    on_complete: Callable[[dict | None, str | None], None] | None = None,
) -> subprocess.Popen:
    """Run the extractor in a subprocess (non-blocking).

    Call on_complete(result_dict, error_string) when done.
    Returns the Popen object for cancellation.
    """
    import threading

    cmd = [str(EXTRACTOR_PYTHON), str(EXTRACTOR_SCRIPT)]
    if product_id is not None:
        cmd.extend(["--product-id", str(product_id)])
    cmd.extend(arguments)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(EXTRACTOR_DIR),
    )

    def _wait():
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            if proc.returncode != 0:
                if on_complete:
                    on_complete(None, stderr.strip() or f"Exit code {proc.returncode}")
                return
            if stdout.strip():
                data = json.loads(stdout.strip())
            else:
                data = {}
            if on_complete:
                on_complete(data, None)
        except subprocess.TimeoutExpired:
            proc.kill()
            if on_complete:
                on_complete(None, f"Timed out after {timeout}s")
        except json.JSONDecodeError:
            if on_complete:
                on_complete(None, f"Invalid JSON: {stdout[:200]}")
        except Exception as e:
            if on_complete:
                on_complete(None, str(e))

    t = threading.Thread(target=_wait, daemon=True)
    t.start()
    return proc
