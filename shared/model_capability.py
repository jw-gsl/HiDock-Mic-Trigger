"""Resource preflight for planned models — can this Mac actually run it?

Data-driven: CAPABILITY_REQUIREMENTS (keyed by model registry key)
declares the hardware/software bar for a planned model, and
check_model_capability() runs the checks and returns a JSON-safe report.
The Model Manager UI surfaces this via the "Check compatibility" button
(`models.py capability <model_key>`).

Checks report one of four statuses:
  - "pass" — requirement met
  - "warn" — runs, but degraded (never blocks)
  - "fail" — hard blocker (flips can_run to False)
  - "info" — context only, never blocks (licensing, download size)

The hardware probes (_machine_arch/_total_ram_bytes/_free_disk_bytes)
and the import probe (_module_available) are module-level seams so
tests can monkeypatch them instead of depending on the host machine.
"""
from __future__ import annotations

import importlib.util
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

_GB = 1024 ** 3

# Per-model requirements. Add an entry when a planned model needs a
# preflight; the registry entry's `capability` field points at the key.
CAPABILITY_REQUIREMENTS: dict[str, dict] = {
    # WeSpeaker's official W2V-BERT 2.0 support: Meta facebook/w2v-bert-2.0
    # SSL frontend (580M params, PyTorch-only — no ONNX export) + 6.2M-param
    # Adapter-MFA backend. Runs under the wespeaker runtime on Apple Silicon.
    "wespeaker_w2vbert2": {
        "arch": "arm64",
        # 8 GB is the floor for a 580M-param PyTorch model + runtime;
        # 16 GB+ keeps it comfortable alongside the rest of the pipeline.
        "ram_pass_gb": 16,
        "ram_warn_gb": 8,
        # Checkpoint is ~2.4 GB; the PyTorch/transformers stack adds more.
        "disk_pass_gb": 8,
        "disk_warn_gb": 4,
        # module name -> fix hint shown when the import probe fails.
        "modules": {
            "torch": "pip install torch",
            "transformers": "pip install transformers peft",
            "peft": "pip install transformers peft",
            "wespeaker": "git clone https://github.com/wenet-e2e/wespeaker && pip install -e ./wespeaker",
        },
        # Non-blocking context (licensing, download size).
        "notes": [
            "Checkpoint is ~2.4 GB, downloads on first use; "
            "CC BY-NC-SA 4.0 — local/non-commercial use only",
        ],
    },
}


# ── Probes (test seams) ─────────────────────────────────────────────────────


def _machine_arch() -> str:
    """CPU architecture, e.g. 'arm64' or 'x86_64'."""
    return platform.machine()


def _total_ram_bytes() -> int | None:
    """Total physical RAM in bytes via sysctl, or None if undeterminable."""
    try:
        result = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        return int(result.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _free_disk_bytes(path: Path) -> int | None:
    """Free bytes on the volume containing `path`, or None on error."""
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def _module_available(module_name: str) -> bool:
    """Whether a Python module is importable, without importing it.

    Same cheap find_spec probe as models._python_module_available.
    """
    try:
        return importlib.util.find_spec(module_name) is not None
    except Exception:
        return False


# ── Individual checks ────────────────────────────────────────────────────────


def _check(name: str, status: str, detail: str, fix: str = "") -> dict:
    return {"name": name, "status": status, "detail": detail, "fix": fix}


def _check_arch(required_arch: str) -> dict:
    actual = _machine_arch()
    if actual == required_arch:
        return _check("CPU architecture", "pass", f"Apple Silicon ({actual})")
    return _check(
        "CPU architecture", "fail",
        f"This Mac is {actual or 'unknown'} — the PyTorch runtime path is "
        f"only supported on {required_arch} (Apple Silicon)",
        fix="Run on an Apple Silicon (arm64) Mac",
    )


def _check_ram(pass_gb: int, warn_gb: int) -> dict:
    total = _total_ram_bytes()
    if total is None:
        return _check(
            "Memory", "warn",
            "Could not determine total RAM (sysctl hw.memsize unavailable)",
        )
    ram_gb = total / _GB
    if ram_gb >= pass_gb:
        return _check("Memory", "pass", f"{ram_gb:.0f} GB RAM installed")
    if ram_gb >= warn_gb:
        return _check(
            "Memory", "warn",
            f"{ram_gb:.0f} GB RAM — will be slow; 16 GB+ recommended",
        )
    return _check(
        "Memory", "fail",
        f"{ram_gb:.0f} GB RAM is below the {warn_gb} GB minimum for a "
        "580M-param PyTorch model",
        fix="This model needs at least 8 GB RAM; 16 GB+ recommended",
    )


def _check_disk(pass_gb: int, warn_gb: int) -> dict:
    free = _free_disk_bytes(Path.home())
    if free is None:
        return _check("Disk space", "warn", "Could not determine free disk space")
    free_gb = free / _GB
    if free_gb >= pass_gb:
        return _check(
            "Disk space", "pass",
            f"{free_gb:.0f} GB free (checkpoint ~2.4 GB + PyTorch stack)",
        )
    if free_gb >= warn_gb:
        return _check(
            "Disk space", "warn",
            f"{free_gb:.1f} GB free — tight for the ~2.4 GB checkpoint plus "
            "the PyTorch stack; 8 GB+ recommended",
        )
    return _check(
        "Disk space", "fail",
        f"Only {free_gb:.1f} GB free — need at least {warn_gb} GB "
        "(checkpoint ~2.4 GB + PyTorch stack)",
        fix="Free up at least 8 GB of disk space",
    )


def _check_module(module_name: str, fix: str) -> dict:
    if _module_available(module_name):
        return _check(
            f"Python package: {module_name}", "pass",
            f"'{module_name}' is installed",
        )
    return _check(
        f"Python package: {module_name}", "fail",
        f"'{module_name}' is not installed",
        fix=fix,
    )


# ── Public API ───────────────────────────────────────────────────────────────


def check_model_capability(model_key: str) -> dict:
    """Run the resource preflight for a registered planned model.

    Returns a JSON-safe report:
        {"model_key", "can_run", "checks": [{"name", "status", "detail",
        "fix"}], "checked_at"}
    `can_run` is True when no check reports "fail" (warn/info never block).
    Unknown model keys return {"error": ...}.
    """
    requirements = CAPABILITY_REQUIREMENTS.get(model_key)
    if requirements is None:
        return {"error": f"No capability requirements registered for model '{model_key}'"}

    checks = [
        _check_arch(requirements["arch"]),
        _check_ram(requirements["ram_pass_gb"], requirements["ram_warn_gb"]),
        _check_disk(requirements["disk_pass_gb"], requirements["disk_warn_gb"]),
    ]
    for module_name, fix in requirements["modules"].items():
        checks.append(_check_module(module_name, fix))
    for note in requirements.get("notes", []):
        checks.append(_check("Note", "info", note))

    return {
        "model_key": model_key,
        "can_run": all(c["status"] != "fail" for c in checks),
        "checks": checks,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
