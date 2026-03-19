"""Qt table model for sync recordings."""
from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtGui import QColor

from core.usb_sync import SyncRecordingEntry

COLUMNS = [
    ("device", "Device"),
    ("status", "Status"),
    ("transcribed", "Transcribed"),
    ("name", "Recording"),
    ("created", "Created"),
    ("duration", "Length"),
    ("size", "Size"),
    ("path", "Output"),
]

# Theme colors
_GREEN = QColor("#a6e3a1")
_RED = QColor("#f38ba8")
_YELLOW = QColor("#f9e2af")
_GRAY = QColor("#585b70")
_SECONDARY = QColor("#a6adc8")
_ACCENT = QColor("#89b4fa")


class RecordingTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: list[SyncRecordingEntry] = []

    def set_entries(self, entries: list[SyncRecordingEntry]):
        self.beginResetModel()
        self._entries = entries
        self.endResetModel()

    def entries(self) -> list[SyncRecordingEntry]:
        return self._entries

    def rowCount(self, parent=QModelIndex()):
        return len(self._entries)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section][1]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None

        entry = self._entries[index.row()]
        rec = entry.recording
        col_key = COLUMNS[index.column()][0]

        if role == Qt.ItemDataRole.DisplayRole:
            if col_key == "device":
                return entry.device_name
            elif col_key == "status":
                if rec.downloaded and rec.local_exists:
                    return "\u2713 Downloaded"
                elif rec.downloaded:
                    return "\u2713 Marked"
                elif rec.last_error:
                    return "\u2717 Failed"
                return "\u25cf On device"
            elif col_key == "transcribed":
                return "\u2713" if rec.transcribed else "\u2014"
            elif col_key == "name":
                return rec.output_name or rec.name
            elif col_key == "created":
                return f"{rec.create_date} {rec.create_time}"
            elif col_key == "duration":
                return rec.human_length
            elif col_key == "size":
                if rec.length > 0:
                    mb = rec.length / (1024 * 1024)
                    return f"{mb:.1f} MB"
                return ""
            elif col_key == "path":
                return rec.output_path

        elif role == Qt.ItemDataRole.ForegroundRole:
            if col_key == "status":
                if rec.downloaded and rec.local_exists:
                    return _GREEN
                elif rec.downloaded:
                    return _ACCENT
                elif rec.last_error:
                    return _RED
                return _GRAY
            elif col_key == "transcribed":
                return _GREEN if rec.transcribed else _GRAY
            elif col_key == "path":
                return _SECONDARY

        elif role == Qt.ItemDataRole.ToolTipRole:
            if col_key == "status":
                if rec.last_error:
                    return f"Error: {rec.last_error}"
                if rec.downloaded and rec.local_exists:
                    return f"Downloaded to: {rec.output_path}"
                if rec.downloaded:
                    return "Marked as downloaded (file may not exist locally)"
                return "Recording is on the HiDock device"
            elif col_key == "name":
                parts = [rec.output_name or rec.name]
                if rec.output_path:
                    parts.append(f"Path: {rec.output_path}")
                if rec.mode:
                    parts.append(f"Mode: {rec.mode}")
                if rec.signature:
                    parts.append(f"Signature: {rec.signature}")
                return "\n".join(parts)
            elif col_key == "transcribed":
                if rec.transcribed and rec.transcript_path:
                    return f"Transcript: {rec.transcript_path}"
                elif rec.transcribed:
                    return "Transcribed"
                return "Not transcribed"
            elif col_key == "path":
                return rec.output_path or "Not downloaded"

        return None

    def flags(self, index):
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
