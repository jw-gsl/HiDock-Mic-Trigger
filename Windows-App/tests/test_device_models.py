"""Tests for core/models.py — PairedDevice, DeviceType, persistence."""
from __future__ import annotations

import json
import sys
import types
from unittest.mock import MagicMock

# Mock PyQt6 before importing anything that depends on it
pyqt6_mod = types.ModuleType("PyQt6")
pyqt6_core = types.ModuleType("PyQt6.QtCore")
pyqt6_widgets = types.ModuleType("PyQt6.QtWidgets")
pyqt6_gui = types.ModuleType("PyQt6.QtGui")

# Minimal QSettings mock
class FakeQSettings:
    def __init__(self, *args, **kwargs):
        self._data = {}
    def value(self, key, default=None):
        return self._data.get(key, default)
    def setValue(self, key, value):
        self._data[key] = value

pyqt6_core.Qt = MagicMock()
pyqt6_core.QSettings = FakeQSettings
pyqt6_core.pyqtSignal = MagicMock(return_value=MagicMock())
pyqt6_core.pyqtSlot = lambda *a, **kw: lambda fn: fn
pyqt6_core.QTimer = MagicMock()
pyqt6_core.QThread = MagicMock()
pyqt6_core.QKeySequence = MagicMock()
pyqt6_widgets.QMainWindow = type("QMainWindow", (), {"__init__": lambda self, *a: None})
pyqt6_widgets.QSystemTrayIcon = MagicMock()
pyqt6_widgets.QDialog = type("QDialog", (), {"__init__": lambda self, *a, **kw: None})
pyqt6_widgets.QWidget = type("QWidget", (), {"__init__": lambda self, *a, **kw: None})
pyqt6_widgets.QLabel = MagicMock()
pyqt6_widgets.QPushButton = MagicMock()
pyqt6_widgets.QHBoxLayout = MagicMock()
pyqt6_widgets.QVBoxLayout = MagicMock()
pyqt6_widgets.QScrollArea = MagicMock()
pyqt6_widgets.QLineEdit = MagicMock()
pyqt6_widgets.QComboBox = MagicMock()
pyqt6_widgets.QProgressBar = MagicMock()
pyqt6_gui.QKeySequence = MagicMock()

sys.modules["PyQt6"] = pyqt6_mod
sys.modules["PyQt6.QtCore"] = pyqt6_core
sys.modules["PyQt6.QtWidgets"] = pyqt6_widgets
sys.modules["PyQt6.QtGui"] = pyqt6_gui

from core.models import DeviceType, PairedDevice, load_paired_devices, save_paired_devices  # noqa: E402


class TestDeviceType:
    def test_enum_values(self):
        assert DeviceType.HIDOCK.value == "hidock"
        assert DeviceType.VOLUME.value == "volume"

    def test_str_enum(self):
        assert str(DeviceType.HIDOCK) == "DeviceType.HIDOCK"


class TestPairedDevice:
    def test_hidock_factory(self):
        dev = PairedDevice.hidock(45068, "HiDock H1")
        assert dev.device_type == DeviceType.HIDOCK
        assert dev.product_id == 45068
        assert dev.display_name == "HiDock H1"
        assert dev.device_id == "hidock:45068"
        assert dev.short_name == "H1"
        assert dev.paired_at is not None

    def test_volume_factory(self):
        dev = PairedDevice.volume("ZOOM_H1", "ZOOM_H1", subpath="recordings")
        assert dev.device_type == DeviceType.VOLUME
        assert dev.volume_name == "ZOOM_H1"
        assert dev.subpath == "recordings"
        assert dev.device_id == "volume:ZOOM_H1"
        assert dev.short_name == "ZOOM_H1"

    def test_to_dict_roundtrip(self):
        original = PairedDevice.hidock(45068, "HiDock H1")
        d = original.to_dict()
        restored = PairedDevice.from_dict(d)
        assert restored.device_type == original.device_type
        assert restored.product_id == original.product_id
        assert restored.display_name == original.display_name
        assert restored.device_id == original.device_id

    def test_volume_to_dict_roundtrip(self):
        original = PairedDevice.volume("USB_REC", "USB Recorder", subpath="audio")
        d = original.to_dict()
        restored = PairedDevice.from_dict(d)
        assert restored.device_type == DeviceType.VOLUME
        assert restored.volume_name == "USB_REC"
        assert restored.subpath == "audio"
        assert restored.device_id == original.device_id

    def test_from_dict_defaults(self):
        dev = PairedDevice.from_dict({})
        assert dev.device_type == DeviceType.HIDOCK
        assert dev.display_name == ""
        assert dev.product_id == 0

    def test_device_id_uniqueness(self):
        h1 = PairedDevice.hidock(45068, "H1")
        h2 = PairedDevice.hidock(45070, "P1")
        v1 = PairedDevice.volume("USB1", "USB1")
        v2 = PairedDevice.volume("USB2", "USB2")
        ids = {h1.device_id, h2.device_id, v1.device_id, v2.device_id}
        assert len(ids) == 4


class TestPersistence:
    def test_save_and_load(self):
        settings = FakeQSettings()
        devices = [
            PairedDevice.hidock(45068, "HiDock H1"),
            PairedDevice.volume("ZOOM", "Zoom H1"),
        ]
        save_paired_devices(settings, devices)
        loaded = load_paired_devices(settings)
        assert len(loaded) == 2
        assert loaded[0].device_id == "hidock:45068"
        assert loaded[1].device_id == "volume:ZOOM"

    def test_load_empty(self):
        settings = FakeQSettings()
        assert load_paired_devices(settings) == []

    def test_load_corrupt(self):
        settings = FakeQSettings()
        settings._data["pairedDevices"] = "not json {"
        assert load_paired_devices(settings) == []

    def test_load_non_list(self):
        settings = FakeQSettings()
        settings._data["pairedDevices"] = json.dumps({"key": "value"})
        # json.loads returns a dict, not a list — handled gracefully
        result = load_paired_devices(settings)
        assert result == []
