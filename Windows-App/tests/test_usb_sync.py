"""Tests for core/usb_sync.py — dataclasses and extractor_ready."""
from core.usb_sync import SyncRecording, SyncRecordingEntry, extractor_ready


class TestSyncRecordingFromDict:
    def test_full_dict(self):
        d = {
            "name": "rec001.mp3",
            "createDate": "2025-01-15",
            "createTime": "14:30:00",
            "length": 1024000,
            "duration": 65.3,
            "version": 2,
            "mode": "normal",
            "signature": "abc123",
            "outputPath": "/tmp/rec001.mp3",
            "outputName": "rec001.mp3",
            "downloaded": True,
            "localExists": True,
            "downloadedAt": "2025-01-15T15:00:00Z",
            "lastError": None,
            "status": "downloaded",
            "humanLength": "1.0 MB",
        }
        rec = SyncRecording.from_dict(d)
        assert rec.name == "rec001.mp3"
        assert rec.create_date == "2025-01-15"
        assert rec.duration == 65.3
        assert rec.downloaded is True
        assert rec.local_exists is True
        assert rec.status == "downloaded"

    def test_empty_dict_uses_defaults(self):
        rec = SyncRecording.from_dict({})
        assert rec.name == ""
        assert rec.length == 0
        assert rec.duration == 0.0
        assert rec.downloaded is False
        assert rec.transcribed is False
        assert rec.transcript_path is None


class TestSyncRecordingEntry:
    def test_wraps_recording(self):
        rec = SyncRecording(name="test.mp3")
        entry = SyncRecordingEntry(recording=rec, device_product_id=42, device_name="HiDock")
        assert entry.recording.name == "test.mp3"
        assert entry.device_product_id == 42
        assert entry.device_name == "HiDock"


class TestExtractorReady:
    def test_missing_script(self, monkeypatch, tmp_path):
        import core.config as cfg
        monkeypatch.setattr(cfg, "EXTRACTOR_SCRIPT", tmp_path / "nonexistent.py")
        monkeypatch.setattr(cfg, "EXTRACTOR_PYTHON", tmp_path / "python.exe")
        # Re-import to pick up patched values
        import core.usb_sync as usb_mod
        monkeypatch.setattr(usb_mod, "EXTRACTOR_SCRIPT", tmp_path / "nonexistent.py")
        monkeypatch.setattr(usb_mod, "EXTRACTOR_PYTHON", tmp_path / "python.exe")
        ready, msg = extractor_ready()
        assert ready is False
        assert "not found" in msg.lower()

    def test_missing_python(self, monkeypatch, tmp_path):
        import core.usb_sync as usb_mod
        script = tmp_path / "extractor.py"
        script.write_text("# stub")
        monkeypatch.setattr(usb_mod, "EXTRACTOR_SCRIPT", script)
        monkeypatch.setattr(usb_mod, "EXTRACTOR_PYTHON", tmp_path / "nonexistent_python.exe")
        ready, msg = extractor_ready()
        assert ready is False
        assert "venv" in msg.lower()

    def test_both_exist(self, monkeypatch, tmp_path):
        import core.usb_sync as usb_mod
        script = tmp_path / "extractor.py"
        script.write_text("# stub")
        python = tmp_path / "python.exe"
        python.write_text("# stub")
        monkeypatch.setattr(usb_mod, "EXTRACTOR_SCRIPT", script)
        monkeypatch.setattr(usb_mod, "EXTRACTOR_PYTHON", python)
        ready, msg = extractor_ready()
        assert ready is True
        assert msg == ""
