"""Tests for shared.model_capability — resource preflight for planned models.

All hardware/software probes are monkeypatched at the module seams so the
tests never depend on the host machine.
"""
from __future__ import annotations

from unittest.mock import patch

from shared.model_capability import CAPABILITY_REQUIREMENTS, check_model_capability

_GB = 1024 ** 3

_ALL_MODULES = {"torch", "transformers", "peft", "wespeaker"}


def _run(arch="arm64", ram_bytes=32 * _GB, disk_free=100 * _GB, modules=None):
    """Run the wespeaker_w2vbert2 preflight with every seam stubbed."""
    available = _ALL_MODULES if modules is None else set(modules)
    with patch("shared.model_capability._machine_arch", return_value=arch), \
         patch("shared.model_capability._total_ram_bytes", return_value=ram_bytes), \
         patch("shared.model_capability._free_disk_bytes", return_value=disk_free), \
         patch("shared.model_capability._module_available",
               side_effect=lambda name: name in available):
        return check_model_capability("wespeaker_w2vbert2")


def _find(result, name):
    return next(c for c in result["checks"] if c["name"] == name)


# ── Happy path ──────────────────────────────────────────────────────────────


def test_all_green_can_run():
    result = _run()

    assert result["model_key"] == "wespeaker_w2vbert2"
    assert result["can_run"] is True
    assert "checked_at" in result
    blocking = [c for c in result["checks"] if c["status"] != "info"]
    assert blocking, "expected hardware/software checks"
    assert all(c["status"] == "pass" for c in blocking)
    # Every check carries the full four-field shape the UI renders.
    for check in result["checks"]:
        assert {"name", "status", "detail", "fix"} <= set(check.keys())


# ── RAM thresholds ──────────────────────────────────────────────────────────


def test_8gb_ram_warns_but_can_run():
    result = _run(ram_bytes=8 * _GB)

    mem = _find(result, "Memory")
    assert mem["status"] == "warn"
    assert "will be slow; 16 GB+ recommended" in mem["detail"]
    assert result["can_run"] is True


def test_under_8gb_ram_fails():
    result = _run(ram_bytes=4 * _GB)

    assert _find(result, "Memory")["status"] == "fail"
    assert result["can_run"] is False


# ── Disk thresholds ─────────────────────────────────────────────────────────


def test_tight_disk_warns_but_can_run():
    result = _run(disk_free=5 * _GB)

    assert _find(result, "Disk space")["status"] == "warn"
    assert result["can_run"] is True


def test_low_disk_fails():
    result = _run(disk_free=2 * _GB)

    assert _find(result, "Disk space")["status"] == "fail"
    assert result["can_run"] is False


# ── Architecture ────────────────────────────────────────────────────────────


def test_non_arm64_fails():
    result = _run(arch="x86_64")

    assert _find(result, "CPU architecture")["status"] == "fail"
    assert result["can_run"] is False


# ── Python packages ─────────────────────────────────────────────────────────


def test_missing_torch_fails_with_fix():
    result = _run(modules=_ALL_MODULES - {"torch"})

    torch_check = _find(result, "Python package: torch")
    assert torch_check["status"] == "fail"
    assert torch_check["fix"] == "pip install torch"
    assert result["can_run"] is False


def test_missing_wespeaker_fails_with_clone_fix():
    result = _run(modules=_ALL_MODULES - {"wespeaker"})

    check = _find(result, "Python package: wespeaker")
    assert check["status"] == "fail"
    assert "github.com/wenet-e2e/wespeaker" in check["fix"]
    assert result["can_run"] is False


# ── Info checks & unknown keys ──────────────────────────────────────────────


def test_info_check_never_affects_can_run():
    result = _run()

    info_checks = [c for c in result["checks"] if c["status"] == "info"]
    assert info_checks, "expected a non-blocking info check"
    assert any("CC BY-NC-SA 4.0" in c["detail"] for c in info_checks)
    assert result["can_run"] is True


def test_unknown_key_returns_error():
    result = check_model_capability("nonexistent_model")

    assert "error" in result
    assert "checks" not in result


# ── Registry wiring ─────────────────────────────────────────────────────────


def test_registry_capability_keys_have_requirements():
    """Every registry entry pointing at a capability preflight must have a
    matching CAPABILITY_REQUIREMENTS entry."""
    from shared.models import MODEL_REGISTRY

    pointed = {
        info["capability"]
        for info in MODEL_REGISTRY.values()
        if info.get("capability")
    }
    assert pointed, "expected at least one registry entry with a capability"
    assert pointed <= set(CAPABILITY_REQUIREMENTS)
