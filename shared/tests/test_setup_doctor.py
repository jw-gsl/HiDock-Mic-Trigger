"""Setup checks: every check reports, none raises, and each failure says how to fix it."""
from __future__ import annotations

from shared import setup_doctor


def test_machine_id_is_stable_and_short():
    assert setup_doctor.machine_id() == setup_doctor.machine_id()
    assert len(setup_doctor.machine_id()) == 10


def test_a_crashing_check_reports_as_failed_instead_of_raising(monkeypatch):
    def boom():
        raise OSError("disk gone")

    monkeypatch.setattr(setup_doctor, "CHECKS", [("x", "Broken", boom)])
    result = setup_doctor.run()
    assert result["ok"] is False
    assert result["checks"][0]["detail"].startswith("check failed: disk gone")


def test_failures_carry_a_fix_and_passes_do_not(monkeypatch):
    monkeypatch.setattr(setup_doctor, "CHECKS", [
        ("a", "Good", lambda: (True, "fine", "unused")),
        ("b", "Bad", lambda: (False, "missing token", "Paste the token")),
    ])
    checks = {c["id"]: c for c in setup_doctor.run()["checks"]}
    assert checks["a"]["fix"] == ""
    assert checks["b"]["fix"] == "Paste the token"


def test_nemotron_check_is_skipped_when_another_diarizer_is_active(monkeypatch):
    monkeypatch.setattr(setup_doctor, "_backends", lambda: {"diarization": "sortformer"})
    assert setup_doctor.check_nemotron()[0] is True


def test_a_model_needing_code_is_not_installed_without_it(tmp_path, monkeypatch):
    from shared import models
    monkeypatch.setattr(models, "MODELS_DIR", tmp_path)
    info = {"code_repo": "https://example.invalid/x", "code_dir": "x-repo", "code_check": "x/x.py"}
    assert models._code_present(info) is False
    (tmp_path / "x-repo" / "x").mkdir(parents=True)
    (tmp_path / "x-repo" / "x" / "x.py").write_text("")
    assert models._code_present(info) is True
    assert models._code_present({}) is True


def test_nemotron_token_is_not_reported_missing_from_terminal(monkeypatch):
    monkeypatch.setattr(setup_doctor, "_backends", lambda: {"diarization": "nemotron"})
    monkeypatch.delenv("HIDOCK_FROM_APP", raising=False)
    monkeypatch.delenv("HIDOCK_NEMOTRON_TOKEN", raising=False)
    monkeypatch.setattr("shared.diarize_nemotron._auth_token", lambda: None)
    assert setup_doctor.check_nemotron()[0] is True
