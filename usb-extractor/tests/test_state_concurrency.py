"""Tests for cross-process state.json safety and the CLI JSON error contract.

The Swift app runs `status` polls and `download`/`download-new` as
overlapping separate processes. These tests cover:
- state_lock mutual exclusion (no lost updates),
- the re-load-under-lock ("merge before save") pattern in download_one /
  volume_import_one, so a writer that finishes second doesn't clobber
  records written by a process that finished first,
- unique temp names in save_json_file,
- the top-level JSON-on-stdout guarantee for unexpected exceptions.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

# conftest.py sets up the usb mock before this import
import extractor
from extractor import (
    load_state,
    save_state,
    save_json_file,
    state_lock,
    download_new,
    volume_import_one,
)


# ---------------------------------------------------------------------------
# state_lock — mutual exclusion
# ---------------------------------------------------------------------------
class TestStateLock:
    def test_no_lost_updates_across_lock_holders(self, tmp_path):
        """N read-modify-write cycles under the lock must not lose any."""
        state_path = tmp_path / "state.json"
        save_state({"downloads": {}, "counter": 0}, state_path)

        def bump(times: int) -> None:
            for _ in range(times):
                with state_lock(state_path):
                    state = load_state(state_path)
                    state["counter"] = state.get("counter", 0) + 1
                    save_state(state, state_path)

        threads = [threading.Thread(target=bump, args=(20,)) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert load_state(state_path)["counter"] == 60

    def test_lockfile_created_next_to_state(self, tmp_path):
        state_path = tmp_path / "nested" / "state.json"
        with state_lock(state_path):
            pass
        assert (tmp_path / "nested" / "state.json.lock").exists()


# ---------------------------------------------------------------------------
# save_json_file — unique temp names, no leftovers
# ---------------------------------------------------------------------------
class TestSaveJsonFileTempNames:
    def test_no_shared_tmp_leftover(self, tmp_path):
        path = tmp_path / "state.json"
        save_json_file(path, {"a": 1})
        save_json_file(path, {"a": 2})
        leftovers = [p for p in tmp_path.iterdir() if p.name != "state.json"]
        assert leftovers == []
        assert json.loads(path.read_text()) == {"a": 2}

    def test_failed_write_leaves_original_intact(self, tmp_path, monkeypatch):
        path = tmp_path / "state.json"
        save_json_file(path, {"a": 1})

        class Unserializable:
            pass

        with pytest.raises(TypeError):
            save_json_file(path, {"bad": Unserializable()})
        # Original survives and no temp junk remains
        assert json.loads(path.read_text()) == {"a": 1}
        assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


# ---------------------------------------------------------------------------
# download_one — merge-before-save (two overlapping writers)
# ---------------------------------------------------------------------------
class TestDownloadOneMergesConcurrentState:
    def _inject_concurrent_record(self, state_path: Path) -> None:
        """Simulate another process saving between our load and our save."""
        state = load_state(state_path)
        state["downloads"]["concurrent.hda"] = {"downloaded": True, "removed": True}
        save_state(state, state_path)

    def test_success_path_preserves_concurrent_records(self, tmp_path, monkeypatch):
        state_path = tmp_path / "state.json"
        save_state({"downloads": {}}, state_path)

        monkeypatch.setattr(extractor, "find_device", lambda product_id=None: object())
        monkeypatch.setattr(extractor, "prepare_device", lambda dev: 0)
        monkeypatch.setattr(extractor, "release_device", lambda dev, intf: None)

        def fake_transfer(dev, filename, total_length, out_path, **kwargs):
            # While the (long) transfer runs, a status poll finishes and saves
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(b"x" * total_length)
            self._inject_concurrent_record(state_path)
            return total_length

        monkeypatch.setattr(extractor, "transfer_file_stream_to_path", fake_transfer)

        result = extractor.download_one(
            "rec.hda",
            length=64,
            output_dir=tmp_path / "out",
            config_path=tmp_path / "config.json",
            state_path=state_path,
        )
        assert result["downloaded"] is True

        state = load_state(state_path)
        # Our record landed...
        assert state["downloads"]["rec.hda"]["downloaded"] is True
        # ...and the concurrent writer's record was NOT clobbered
        assert state["downloads"]["concurrent.hda"] == {"downloaded": True, "removed": True}

    def test_error_path_preserves_concurrent_records(self, tmp_path, monkeypatch):
        state_path = tmp_path / "state.json"
        save_state({"downloads": {}}, state_path)

        monkeypatch.setattr(extractor, "find_device", lambda product_id=None: object())
        monkeypatch.setattr(extractor, "prepare_device", lambda dev: 0)
        monkeypatch.setattr(extractor, "release_device", lambda dev, intf: None)

        def failing_transfer(dev, filename, total_length, out_path, **kwargs):
            self._inject_concurrent_record(state_path)
            raise TimeoutError("transfer stalled")

        monkeypatch.setattr(extractor, "transfer_file_stream_to_path", failing_transfer)

        with pytest.raises(TimeoutError):
            extractor.download_one(
                "rec.hda",
                length=64,
                output_dir=tmp_path / "out",
                config_path=tmp_path / "config.json",
                state_path=state_path,
            )

        state = load_state(state_path)
        assert state["downloads"]["rec.hda"]["last_error"] == "transfer stalled"
        assert state["downloads"]["concurrent.hda"] == {"downloaded": True, "removed": True}


# ---------------------------------------------------------------------------
# volume_import_one — merge-before-save
# ---------------------------------------------------------------------------
class TestVolumeImportMergesConcurrentState:
    def test_import_preserves_concurrent_records(self, tmp_path, monkeypatch):
        vroot = tmp_path / "Volumes"
        (vroot / "VOL").mkdir(parents=True)
        (vroot / "VOL" / "rec.mp3").write_bytes(b"m" * 40)
        monkeypatch.setattr(extractor, "VOLUMES_ROOT", vroot)

        state_path = tmp_path / "state.json"
        save_state({"downloads": {}}, state_path)

        real_copy2 = extractor.shutil.copy2

        def slow_copy2(src, dst):
            # Another process saves state while our copy is in flight
            state = load_state(state_path)
            state["downloads"]["concurrent.hda"] = {"downloaded": True}
            save_state(state, state_path)
            return real_copy2(src, dst)

        monkeypatch.setattr(extractor.shutil, "copy2", slow_copy2)

        result = volume_import_one(
            "rec.mp3",
            "VOL",
            output_dir=tmp_path / "out",
            config_path=tmp_path / "config.json",
            state_path=state_path,
        )
        assert result["downloaded"] is True

        state = load_state(state_path)
        assert state["downloads"]["vol:VOL/rec.mp3"]["downloaded"] is True
        assert state["downloads"]["concurrent.hda"] == {"downloaded": True}


# ---------------------------------------------------------------------------
# status_payload catalog save — merge-before-save is covered indirectly via
# merge_partial_catalog tests; here we verify the disconnected download_new
# JSON shape (fix: `errors` key present on both branches).
# ---------------------------------------------------------------------------
class TestDownloadNewShape:
    def test_disconnected_branch_includes_errors_key(self, monkeypatch):
        monkeypatch.setattr(
            extractor,
            "status_payload",
            lambda **kwargs: {"connected": False, "outputDir": "/x", "recordings": [], "error": "nope"},
        )
        result = download_new()
        assert result["connected"] is False
        assert result["errors"] == []
        assert result["downloaded"] == []
        assert result["skipped"] == []


# ---------------------------------------------------------------------------
# main() — JSON on stdout for unexpected exceptions
# ---------------------------------------------------------------------------
class TestMainJsonErrorContract:
    def _run_main(self, argv):
        old_argv = sys.argv
        try:
            sys.argv = argv
            return extractor.main()
        finally:
            sys.argv = old_argv

    def test_unexpected_exception_emits_json(self, monkeypatch, capsys):
        # usb.core.NoBackendError (libusb missing) is not a USBError subclass;
        # simulate any such escape from a JSON-emitting command.
        def boom(**kwargs):
            raise RuntimeError("No backend available")

        monkeypatch.setattr(extractor, "status_payload", boom)
        rc = self._run_main(["extractor", "status"])
        assert rc != 0

        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["connected"] is False
        assert "No backend available" in payload["error"]

    def test_keyboard_interrupt_propagates(self, monkeypatch):
        def interrupted(**kwargs):
            raise KeyboardInterrupt()

        monkeypatch.setattr(extractor, "status_payload", interrupted)
        with pytest.raises(KeyboardInterrupt):
            self._run_main(["extractor", "status"])

    def test_argparse_help_still_exits_zero(self):
        with pytest.raises(SystemExit) as exc_info:
            self._run_main(["extractor", "--help"])
        assert exc_info.value.code == 0
