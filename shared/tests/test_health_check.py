"""Tests for the health check system."""
from __future__ import annotations

import json
import pytest
from pathlib import Path

from shared.health_check import run_health_check, _check_directories, _check_database


@pytest.fixture
def hidock_env(tmp_path):
    """Create a minimal HiDock directory structure."""
    root = tmp_path / "HiDock"
    (root / "Recordings").mkdir(parents=True)
    (root / "Raw Transcripts").mkdir(parents=True)
    (root / "Speech-to-Text").mkdir(parents=True)
    (root / "transcription-pipeline").mkdir(parents=True)
    return root


class TestCheckDirectories:
    def test_all_exist(self, hidock_env):
        result = _check_directories(hidock_env)
        assert result.status == "ok"

    def test_missing_dir(self, tmp_path):
        root = tmp_path / "HiDock"
        root.mkdir()
        result = _check_directories(root)
        assert result.status == "error"
        assert "Missing" in result.message


class TestCheckDatabase:
    def test_no_database(self, hidock_env):
        result = _check_database(hidock_env)
        assert result.status == "warning"

    def test_valid_database(self, hidock_env):
        import sqlite3
        db_path = hidock_env / "Raw Transcripts" / "knowledge.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE meetings (id INTEGER PRIMARY KEY, title TEXT)")
        conn.execute("CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("CREATE TABLE action_items (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE decisions (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE key_points (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE tags (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        result = _check_database(hidock_env)
        assert result.status == "ok"
        assert "0 meetings" in result.message


class TestFullHealthCheck:
    def test_system_reports(self, hidock_env):
        report = run_health_check(hidock_root=hidock_env)
        # May be degraded due to missing models/engines in test env
        assert report["overall"] in ("healthy", "degraded", "unhealthy")
        assert "summary" in report
        assert "checks" in report
        assert len(report["checks"]) > 0

    def test_missing_root(self, tmp_path):
        report = run_health_check(hidock_root=tmp_path / "nonexistent")
        assert report["overall"] in ("unhealthy", "degraded")
        # Should have at least one error
        has_issue = any(
            c["status"] in ("error", "warning")
            for c in report["checks"]
        )
        assert has_issue

    def test_report_structure(self, hidock_env):
        report = run_health_check(hidock_root=hidock_env)
        for check in report["checks"]:
            assert "name" in check
            assert "status" in check
            assert "message" in check
            assert check["status"] in ("ok", "warning", "error")

    def test_disk_space_included(self, hidock_env):
        report = run_health_check(hidock_root=hidock_env)
        names = [c["name"] for c in report["checks"]]
        assert "disk_space" in names

    def test_stale_lock_detection(self, hidock_env):
        lock_path = hidock_env / "transcription-pipeline" / ".transcribe.lock"
        lock_path.write_text("locked")
        report = run_health_check(hidock_root=hidock_env)
        lock_check = next(c for c in report["checks"] if c["name"] == "locks")
        # Fresh lock should be ok
        assert lock_check["status"] == "ok"
