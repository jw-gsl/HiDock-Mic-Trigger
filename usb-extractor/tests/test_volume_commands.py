"""Tests for volume (mass-storage) commands in extractor.py."""
from __future__ import annotations

import pytest

# conftest.py sets up the usb mock before this import
from extractor import (
    VOLUME_AUDIO_EXTENSIONS,
    _safe_resolve,
    _scan_audio_files,
    _audio_file_metadata,
    volume_status,
    volume_import_one,
    volume_import_new,
    load_state,
    save_state,
)


# ---------------------------------------------------------------------------
# _scan_audio_files
# ---------------------------------------------------------------------------
class TestScanAudioFiles:
    def test_finds_supported_formats(self, tmp_path):
        for ext in (".mp3", ".wav", ".m4a", ".ogg", ".flac", ".wma"):
            (tmp_path / f"track{ext}").write_bytes(b"\x00" * 100)
        (tmp_path / "readme.txt").write_text("ignore me")

        result = _scan_audio_files(tmp_path)
        assert len(result) == 6
        names = {f.name for f in result}
        assert "readme.txt" not in names

    def test_empty_directory(self, tmp_path):
        assert _scan_audio_files(tmp_path) == []

    def test_nonexistent_directory(self, tmp_path):
        assert _scan_audio_files(tmp_path / "nope") == []

    def test_subpath_scoping(self, tmp_path):
        sub = tmp_path / "recordings"
        sub.mkdir()
        (sub / "a.mp3").write_bytes(b"\x00" * 50)
        (tmp_path / "b.mp3").write_bytes(b"\x00" * 50)

        result = _scan_audio_files(tmp_path, subpath="recordings")
        assert len(result) == 1
        assert result[0].name == "a.mp3"

    def test_recursive_scan(self, tmp_path):
        nested = tmp_path / "level1" / "level2"
        nested.mkdir(parents=True)
        (nested / "deep.wav").write_bytes(b"\x00" * 50)
        (tmp_path / "top.wav").write_bytes(b"\x00" * 50)

        result = _scan_audio_files(tmp_path)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# _audio_file_metadata
# ---------------------------------------------------------------------------
class TestAudioFileMetadata:
    def test_basic_metadata(self, tmp_path):
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"\x00" * 1024)

        meta = _audio_file_metadata(audio)
        assert meta["name"] == "test.mp3"
        assert meta["length"] == 1024
        assert meta["mode"] == "external"
        assert meta["version"] == 0
        assert "createDate" in meta
        assert "createTime" in meta
        assert "signature" in meta
        assert "duration" in meta

    def test_signature_deterministic(self, tmp_path):
        audio = tmp_path / "test.mp3"
        audio.write_bytes(b"\x00" * 512)

        m1 = _audio_file_metadata(audio)
        m2 = _audio_file_metadata(audio)
        assert m1["signature"] == m2["signature"]


# ---------------------------------------------------------------------------
# volume_status
# ---------------------------------------------------------------------------
class TestVolumeStatus:
    def test_disconnected_volume(self, tmp_path):
        result = volume_status(
            "NoSuchVolume",
            config_path=tmp_path / "config.json",
            state_path=tmp_path / "state.json",
        )
        assert result["connected"] is False
        assert "error" in result
        assert result["recordings"] == []

    def test_state_only_entries_for_disconnected_volume(self, tmp_path):
        """State entries for a disconnected volume still appear."""
        state_path = tmp_path / "state.json"
        save_state({
            "downloads": {
                "vol:TestVol/old.mp3": {
                    "downloaded": True,
                    "downloaded_at": "2026-01-01T00:00:00+00:00",
                    "output_path": str(tmp_path / "old.mp3"),
                    "length": 1000,
                    "signature": "abc",
                },
            }
        }, state_path)

        result = volume_status(
            "TestVol",
            config_path=tmp_path / "config.json",
            state_path=state_path,
        )
        # Volume not connected, so recordings list is empty (no scan possible)
        assert result["connected"] is False


# ---------------------------------------------------------------------------
# volume_import_one
# ---------------------------------------------------------------------------
class TestVolumeImportOne:
    def test_source_not_found(self, tmp_path):
        state_path = tmp_path / "state.json"
        config_path = tmp_path / "config.json"
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        result = volume_import_one(
            "missing.mp3",
            "NoVol",
            output_dir=output_dir,
            config_path=config_path,
            state_path=state_path,
        )
        assert result["downloaded"] is False
        assert "error" in result
        assert result["written"] == 0

        # State should record the error
        state = load_state(state_path)
        key = "vol:NoVol/missing.mp3"
        assert key in state["downloads"]
        assert state["downloads"][key]["downloaded"] is False
        assert "not found" in state["downloads"][key]["last_error"]

    def test_state_update_on_failed_import(self, tmp_path):
        """Verify state.json is updated correctly after a failed import."""
        state_path = tmp_path / "state.json"
        save_state({
            "downloads": {
                "vol:V/test.mp3": {
                    "downloaded": False,
                    "last_error": "previous error",
                }
            }
        }, state_path)

        result = volume_import_one(
            "test.mp3",
            "V",
            output_dir=tmp_path / "out",
            config_path=tmp_path / "config.json",
            state_path=state_path,
        )
        assert result["downloaded"] is False

        state = load_state(state_path)
        entry = state["downloads"]["vol:V/test.mp3"]
        assert entry["downloaded"] is False
        assert "not found" in entry["last_error"]


# ---------------------------------------------------------------------------
# volume_import_new
# ---------------------------------------------------------------------------
class TestVolumeImportNew:
    def test_disconnected_volume(self, tmp_path):
        result = volume_import_new(
            "NoSuchVol",
            config_path=tmp_path / "config.json",
            state_path=tmp_path / "state.json",
        )
        assert result["connected"] is False
        assert result["downloaded"] == []
        assert "error" in result


# ---------------------------------------------------------------------------
# VOLUME_AUDIO_EXTENSIONS
# ---------------------------------------------------------------------------
class TestVolumeAudioExtensions:
    def test_expected_extensions(self):
        expected = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".wma"}
        assert VOLUME_AUDIO_EXTENSIONS == expected


# ---------------------------------------------------------------------------
# CLI subparser registration
# ---------------------------------------------------------------------------
class TestVolumeCliParsing:
    """Verify the argparse subparsers accept volume commands."""

    def test_scan_volumes_help(self):
        import extractor
        import sys
        with pytest.raises(SystemExit) as exc_info:
            sys.argv = ["extractor", "scan-volumes", "--help"]
            extractor.main()
        assert exc_info.value.code == 0

    def test_volume_status_requires_volume_name(self):
        import extractor
        import sys
        with pytest.raises(SystemExit) as exc_info:
            sys.argv = ["extractor", "volume-status"]
            extractor.main()
        assert exc_info.value.code != 0

    def test_volume_import_requires_filename_and_volume(self):
        import extractor
        import sys
        with pytest.raises(SystemExit) as exc_info:
            sys.argv = ["extractor", "volume-import"]
            extractor.main()
        assert exc_info.value.code != 0

    def test_volume_import_new_requires_volume_name(self):
        import extractor
        import sys
        with pytest.raises(SystemExit) as exc_info:
            sys.argv = ["extractor", "volume-import-new"]
            extractor.main()
        assert exc_info.value.code != 0


# ---------------------------------------------------------------------------
# Path traversal protection (_safe_resolve)
# ---------------------------------------------------------------------------
class TestSafeResolve:
    def test_none_subpath_returns_base(self, tmp_path):
        assert _safe_resolve(tmp_path, None) == tmp_path

    def test_valid_subpath(self, tmp_path):
        sub = tmp_path / "audio"
        sub.mkdir()
        result = _safe_resolve(tmp_path, "audio")
        assert result == sub.resolve()

    def test_dotdot_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="traversal"):
            _safe_resolve(tmp_path, "../etc")

    def test_nested_dotdot_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="traversal"):
            _safe_resolve(tmp_path, "a/../../etc")


# ---------------------------------------------------------------------------
# volume_import_one path traversal protection
# ---------------------------------------------------------------------------
class TestVolumeImportSecurity:
    def test_dotdot_in_filename_rejected(self, tmp_path):
        result = volume_import_one(
            "../../../etc/passwd",
            "V",
            output_dir=tmp_path / "out",
            config_path=tmp_path / "config.json",
            state_path=tmp_path / "state.json",
        )
        assert result["downloaded"] is False
        assert "Invalid filename" in result.get("error", "")

    def test_slash_relpath_accepted_but_missing_file_errors(self, tmp_path):
        # Relative subpaths are now valid identifiers (volume_status names
        # duplicated basenames by scan-root-relative path). A missing file
        # resolves to a not-found error, not an "Invalid filename" rejection.
        result = volume_import_one(
            "subdir/file.mp3",
            "V",
            output_dir=tmp_path / "out",
            config_path=tmp_path / "config.json",
            state_path=tmp_path / "state.json",
        )
        assert result["downloaded"] is False
        assert "not found" in result.get("error", "")

    def test_absolute_path_rejected(self, tmp_path):
        result = volume_import_one(
            "/etc/passwd",
            "V",
            output_dir=tmp_path / "out",
            config_path=tmp_path / "config.json",
            state_path=tmp_path / "state.json",
        )
        assert result["downloaded"] is False
        assert "Invalid filename" in result.get("error", "")

    def test_backslash_in_filename_rejected(self, tmp_path):
        result = volume_import_one(
            "subdir\\file.mp3",
            "V",
            output_dir=tmp_path / "out",
            config_path=tmp_path / "config.json",
            state_path=tmp_path / "state.json",
        )
        assert result["downloaded"] is False
        assert "Invalid filename" in result.get("error", "")

    def test_empty_filename_rejected(self, tmp_path):
        result = volume_import_one(
            "",
            "V",
            output_dir=tmp_path / "out",
            config_path=tmp_path / "config.json",
            state_path=tmp_path / "state.json",
        )
        assert result["downloaded"] is False


# ---------------------------------------------------------------------------
# mark-downloaded with --volume-name prefix
# ---------------------------------------------------------------------------
class TestMarkDownloadedVolumePrefix:
    def test_volume_name_prefixes_state_keys(self, tmp_path, monkeypatch):
        """mark-downloaded --volume-name should store keys as vol:<name>/<file>."""
        import sys
        import extractor

        state_path = tmp_path / "state.json"
        save_state({"downloads": {}}, state_path)
        # Patch both the module constant and the function defaults
        monkeypatch.setattr(extractor, "DEFAULT_STATE_PATH", state_path)
        monkeypatch.setattr(extractor.load_state, "__defaults__", (state_path,))
        monkeypatch.setattr(extractor.save_state, "__defaults__", (state_path,))

        old_argv = sys.argv
        try:
            sys.argv = [
                "extractor",
                "mark-downloaded",
                "--volume-name", "ZOOM",
                "rec1.wav", "rec2.wav",
            ]
            extractor.main()
        finally:
            sys.argv = old_argv

        state = load_state(state_path)
        assert "vol:ZOOM/rec1.wav" in state["downloads"]
        assert "vol:ZOOM/rec2.wav" in state["downloads"]
        assert state["downloads"]["vol:ZOOM/rec1.wav"]["downloaded"] is True
        # Raw filenames should NOT be present as keys
        assert "rec1.wav" not in state["downloads"]

    def test_volume_name_with_spaces_and_parens_no_bogus_output_path(self, tmp_path, monkeypatch):
        """mark-downloaded --volume-name must not route volume names through
        output_path_for: it raises on spaces/parens (HiDock-only charset) and
        appends .mp3 to non-mp3 files, storing a bogus output_path."""
        import sys
        import extractor

        state_path = tmp_path / "state.json"
        save_state({"downloads": {}}, state_path)
        monkeypatch.setattr(extractor, "DEFAULT_STATE_PATH", state_path)
        monkeypatch.setattr(extractor.load_state, "__defaults__", (state_path,))
        monkeypatch.setattr(extractor.save_state, "__defaults__", (state_path,))

        old_argv = sys.argv
        try:
            sys.argv = [
                "extractor",
                "mark-downloaded",
                "--volume-name", "ZOOM H1",
                "My Recording (1).wav",
            ]
            rc = extractor.main()
        finally:
            sys.argv = old_argv

        assert rc == 0
        state = load_state(state_path)
        record = state["downloads"]["vol:ZOOM H1/My Recording (1).wav"]
        assert record["downloaded"] is True
        # No .mp3-suffixed HiDock-style path may be fabricated for volume rows
        assert "output_path" not in record

    def test_plaud_account_no_bogus_output_path(self, tmp_path, monkeypatch):
        import sys
        import extractor

        state_path = tmp_path / "state.json"
        save_state({"downloads": {}}, state_path)
        monkeypatch.setattr(extractor, "DEFAULT_STATE_PATH", state_path)
        monkeypatch.setattr(extractor.load_state, "__defaults__", (state_path,))
        monkeypatch.setattr(extractor.save_state, "__defaults__", (state_path,))

        old_argv = sys.argv
        try:
            sys.argv = [
                "extractor",
                "mark-downloaded",
                "--plaud-account", "acct1",
                "rec-id-123",
            ]
            rc = extractor.main()
        finally:
            sys.argv = old_argv

        assert rc == 0
        state = load_state(state_path)
        record = state["downloads"]["plaud:acct1:rec-id-123"]
        assert record["downloaded"] is True
        assert "output_path" not in record

    def test_without_volume_name_uses_raw_keys(self, tmp_path, monkeypatch):
        """mark-downloaded without --volume-name should use raw filenames."""
        import sys
        import extractor

        state_path = tmp_path / "state.json"
        save_state({"downloads": {}}, state_path)
        monkeypatch.setattr(extractor, "DEFAULT_STATE_PATH", state_path)
        monkeypatch.setattr(extractor.load_state, "__defaults__", (state_path,))
        monkeypatch.setattr(extractor.save_state, "__defaults__", (state_path,))

        old_argv = sys.argv
        try:
            sys.argv = [
                "extractor",
                "mark-downloaded",
                "file1.hda",
            ]
            extractor.main()
        finally:
            sys.argv = old_argv

        state = load_state(state_path)
        assert "file1.hda" in state["downloads"]
        assert state["downloads"]["file1.hda"]["downloaded"] is True


# ---------------------------------------------------------------------------
# Duplicate basenames across subdirectories (relpath state keys)
# ---------------------------------------------------------------------------
import extractor as _extractor


@pytest.fixture
def fake_volumes(tmp_path, monkeypatch):
    """A tmp directory standing in for /Volumes."""
    vroot = tmp_path / "Volumes"
    vroot.mkdir()
    monkeypatch.setattr(_extractor, "VOLUMES_ROOT", vroot)
    return vroot


class TestVolumeDuplicateBasenames:
    def _make_vol(self, fake_volumes):
        vol = fake_volumes / "VOL"
        (vol / "FOLDER01").mkdir(parents=True)
        (vol / "FOLDER02").mkdir(parents=True)
        (vol / "FOLDER01" / "REC0001.wav").write_bytes(b"a" * 100)
        (vol / "FOLDER02" / "REC0001.wav").write_bytes(b"b" * 200)
        return vol

    def test_status_names_duplicates_by_relpath(self, fake_volumes, tmp_path):
        self._make_vol(fake_volumes)
        result = volume_status(
            "VOL",
            config_path=tmp_path / "config.json",
            state_path=tmp_path / "state.json",
        )
        assert result["connected"] is True
        recs = result["recordings"]
        assert len(recs) == 2
        names = {r["name"] for r in recs}
        assert names == {"FOLDER01/REC0001.wav", "FOLDER02/REC0001.wav"}
        relpaths = {r["sourceRelpath"] for r in recs}
        assert relpaths == names
        out_names = {r["outputName"] for r in recs}
        assert out_names == {"FOLDER01_REC0001.wav", "FOLDER02_REC0001.wav"}

    def test_import_new_keeps_both_files_and_records(self, fake_volumes, tmp_path, monkeypatch):
        self._make_vol(fake_volumes)
        out_dir = tmp_path / "out"
        config_path = tmp_path / "config.json"
        state_path = tmp_path / "state.json"
        save_json_config = {"output_dir": str(out_dir)}
        from extractor import save_config
        save_config(save_json_config, config_path)

        result = volume_import_new("VOL", config_path=config_path, state_path=state_path)
        assert result["connected"] is True
        assert result["errors"] == []
        assert len(result["downloaded"]) == 2

        # Both files survive under distinct, deterministic output names
        assert (out_dir / "FOLDER01_REC0001.wav").read_bytes() == b"a" * 100
        assert (out_dir / "FOLDER02_REC0001.wav").read_bytes() == b"b" * 200

        # Distinct state records keyed by relpath
        state = load_state(state_path)
        rec1 = state["downloads"]["vol:VOL/FOLDER01/REC0001.wav"]
        rec2 = state["downloads"]["vol:VOL/FOLDER02/REC0001.wav"]
        assert rec1["downloaded"] is True and rec2["downloaded"] is True
        assert rec1["output_path"] != rec2["output_path"]

        # A second sync must skip both, not re-import
        again = volume_import_new("VOL", config_path=config_path, state_path=state_path)
        assert len(again["downloaded"]) == 0
        assert {s["reason"] for s in again["skipped"]} == {"already_downloaded"}

    def test_import_one_by_relpath(self, fake_volumes, tmp_path):
        self._make_vol(fake_volumes)
        out_dir = tmp_path / "out"
        result = volume_import_one(
            "FOLDER02/REC0001.wav",
            "VOL",
            output_dir=out_dir,
            config_path=tmp_path / "config.json",
            state_path=tmp_path / "state.json",
        )
        assert result["downloaded"] is True
        assert result["outputPath"].endswith("FOLDER02_REC0001.wav")
        state = load_state(tmp_path / "state.json")
        assert "vol:VOL/FOLDER02/REC0001.wav" in state["downloads"]

    def test_import_one_ambiguous_basename_refused(self, fake_volumes, tmp_path):
        self._make_vol(fake_volumes)
        result = volume_import_one(
            "REC0001.wav",
            "VOL",
            output_dir=tmp_path / "out",
            config_path=tmp_path / "config.json",
            state_path=tmp_path / "state.json",
        )
        assert result["downloaded"] is False
        assert "Ambiguous" in result["error"]

    def test_unique_subdir_file_keeps_plain_basename_output(self, fake_volumes, tmp_path):
        vol = fake_volumes / "VOL"
        (vol / "sub").mkdir(parents=True)
        (vol / "sub" / "solo.mp3").write_bytes(b"x" * 50)
        out_dir = tmp_path / "out"

        status = volume_status(
            "VOL",
            config_path=tmp_path / "config.json",
            state_path=tmp_path / "state.json",
        )
        rec = status["recordings"][0]
        assert rec["name"] == "solo.mp3"
        assert rec["sourceRelpath"] == "sub/solo.mp3"
        assert rec["outputName"] == "solo.mp3"

        # Swift passes back item.name (the basename) — unique names resolve
        result = volume_import_one(
            "solo.mp3",
            "VOL",
            output_dir=out_dir,
            config_path=tmp_path / "config.json",
            state_path=tmp_path / "state.json",
        )
        assert result["downloaded"] is True
        assert (out_dir / "solo.mp3").exists()
        state = load_state(tmp_path / "state.json")
        assert "vol:VOL/sub/solo.mp3" in state["downloads"]


class TestVolumeLegacyBasenameState:
    """Read-time migration: legacy basename keys still honored when unique."""

    def test_legacy_key_honored_for_unique_subdir_file(self, fake_volumes, tmp_path):
        vol = fake_volumes / "VOL"
        (vol / "sub").mkdir(parents=True)
        (vol / "sub" / "old.mp3").write_bytes(b"z" * 30)
        state_path = tmp_path / "state.json"
        imported = tmp_path / "old.mp3"
        imported.write_bytes(b"z" * 30)
        save_state({
            "downloads": {
                # Pre-relpath releases keyed subdirectory files by basename
                "vol:VOL/old.mp3": {
                    "downloaded": True,
                    "output_path": str(imported),
                    "length": 30,
                },
            }
        }, state_path)

        status = volume_status(
            "VOL",
            config_path=tmp_path / "config.json",
            state_path=state_path,
        )
        recs = status["recordings"]
        # Exactly one row — no ghost "state-only" duplicate of the legacy key
        assert len(recs) == 1
        assert recs[0]["downloaded"] is True
        assert recs[0]["localExists"] is True

        # And import-new must treat it as already downloaded
        result = volume_import_new("VOL", config_path=tmp_path / "config.json", state_path=state_path)
        assert result["downloaded"] == []
        assert result["skipped"] == [{"filename": "old.mp3", "reason": "already_downloaded"}]

    def test_import_migrates_legacy_key_to_relpath(self, fake_volumes, tmp_path):
        vol = fake_volumes / "VOL"
        (vol / "sub").mkdir(parents=True)
        (vol / "sub" / "old.mp3").write_bytes(b"z" * 30)
        state_path = tmp_path / "state.json"
        save_state({
            "downloads": {
                "vol:VOL/old.mp3": {"downloaded": False, "removed": True},
            }
        }, state_path)

        volume_import_one(
            "sub/old.mp3",
            "VOL",
            output_dir=tmp_path / "out",
            config_path=tmp_path / "config.json",
            state_path=state_path,
        )
        state = load_state(state_path)
        assert "vol:VOL/old.mp3" not in state["downloads"]
        migrated = state["downloads"]["vol:VOL/sub/old.mp3"]
        assert migrated["downloaded"] is True
        assert migrated["removed"] is True  # legacy flags carried over

    def test_legacy_key_ignored_when_basename_duplicated(self, fake_volumes, tmp_path):
        vol = fake_volumes / "VOL"
        (vol / "a").mkdir(parents=True)
        (vol / "b").mkdir(parents=True)
        (vol / "a" / "dup.mp3").write_bytes(b"1" * 10)
        (vol / "b" / "dup.mp3").write_bytes(b"2" * 20)
        state_path = tmp_path / "state.json"
        save_state({
            "downloads": {
                "vol:VOL/dup.mp3": {"downloaded": True},
            }
        }, state_path)

        status = volume_status(
            "VOL",
            config_path=tmp_path / "config.json",
            state_path=state_path,
        )
        live = [r for r in status["recordings"] if r["sourcePath"]]
        assert len(live) == 2
        # Can't tell which file the legacy record meant — neither inherits it
        assert all(r["downloaded"] is False for r in live)


# ---------------------------------------------------------------------------
# mark-removed / unmark-removed --volume-name
# ---------------------------------------------------------------------------
class TestMarkRemovedVolume:
    def _patch_state(self, tmp_path, monkeypatch):
        state_path = tmp_path / "state.json"
        save_state({"downloads": {}}, state_path)
        monkeypatch.setattr(_extractor, "DEFAULT_STATE_PATH", state_path)
        monkeypatch.setattr(_extractor.load_state, "__defaults__", (state_path,))
        monkeypatch.setattr(_extractor.save_state, "__defaults__", (state_path,))
        return state_path

    def _run(self, argv):
        import sys
        old_argv = sys.argv
        try:
            sys.argv = argv
            return _extractor.main()
        finally:
            sys.argv = old_argv

    def test_mark_removed_uses_vol_key(self, tmp_path, monkeypatch):
        state_path = self._patch_state(tmp_path, monkeypatch)
        rc = self._run(["extractor", "mark-removed", "--volume-name", "ZOOM", "rec1.mp3"])
        assert rc == 0
        state = load_state(state_path)
        assert state["downloads"]["vol:ZOOM/rec1.mp3"]["removed"] is True
        # The bare name must NOT pollute the HiDock namespace
        assert "rec1.mp3" not in state["downloads"]

    def test_unmark_removed_clears_vol_key(self, tmp_path, monkeypatch):
        state_path = self._patch_state(tmp_path, monkeypatch)
        save_state({
            "downloads": {"vol:ZOOM/rec1.mp3": {"downloaded": False, "removed": True}}
        }, state_path)
        rc = self._run(["extractor", "unmark-removed", "--volume-name", "ZOOM", "rec1.mp3"])
        assert rc == 0
        state = load_state(state_path)
        assert "removed" not in state["downloads"]["vol:ZOOM/rec1.mp3"]

    def test_removed_volume_row_skipped_by_import_new(self, fake_volumes, tmp_path):
        vol = fake_volumes / "VOL"
        vol.mkdir()
        (vol / "rec.mp3").write_bytes(b"q" * 10)
        state_path = tmp_path / "state.json"
        save_state({
            "downloads": {"vol:VOL/rec.mp3": {"downloaded": False, "removed": True}}
        }, state_path)
        result = volume_import_new("VOL", config_path=tmp_path / "config.json", state_path=state_path)
        assert result["downloaded"] == []
        assert result["skipped"] == [{"filename": "rec.mp3", "reason": "user_removed"}]
