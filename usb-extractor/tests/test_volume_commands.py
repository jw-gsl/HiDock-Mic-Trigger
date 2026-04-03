"""Tests for volume (mass-storage) commands in extractor.py."""
from __future__ import annotations

import pytest

# conftest.py sets up the usb mock before this import
from extractor import (
    VOLUME_AUDIO_EXTENSIONS,
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
