"""Tests for core/update_checker.py — install guards."""
import sys

from core.update_checker import _is_newer, find_windows_asset, install_and_restart


class TestInstallAndRestart:
    def test_dev_run_is_a_noop(self, tmp_path, monkeypatch):
        """Not frozen (plain python run): installing would clobber python.exe,
        so install_and_restart must refuse and write no batch script."""
        monkeypatch.delattr(sys, "frozen", raising=False)
        exe = tmp_path / "HiDock-update.exe"
        exe.write_bytes(b"stub")

        assert install_and_restart(exe) is False
        assert not (tmp_path / "update.bat").exists()


class TestVersionCompare:
    def test_newer(self):
        assert _is_newer("1.0.1", "1.0.0") is True

    def test_older_and_equal(self):
        assert _is_newer("0.9.9", "1.0.0") is False
        assert _is_newer("1.0.0", "1.0.0") is False


class TestFindWindowsAsset:
    def test_picks_exe_asset(self):
        release = {
            "assets": [
                {"name": "HiDock.dmg", "browser_download_url": "u1"},
                {"name": "HiDock.exe", "browser_download_url": "u2"},
            ]
        }
        assert find_windows_asset(release) == ("HiDock.exe", "u2")

    def test_no_exe(self):
        assert find_windows_asset({"assets": [{"name": "a.zip", "browser_download_url": "u"}]}) is None
