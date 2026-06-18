"""Qt table model for sync recordings."""
from __future__ import annotations

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtGui import QColor

from core.usb_sync import SyncRecordingEntry

COLUMNS = [
    ("device", "Device"),
    ("status", "Status"),
    ("transcribed", "Transcribed"),
    ("summary", "Summary"),
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
_ORANGE = QColor("#fab387")
_PURPLE = QColor("#cba6f7")
_SECONDARY = QColor("#a6adc8")
_ACCENT = QColor("#89b4fa")


_MERGE_TINT = QColor(137, 180, 250, 28)  # faint accent wash on merge-candidate rows


class RecordingTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Full backing set (all known entries, in load order).
        self._all_entries: list[SyncRecordingEntry] = []
        # Visible display list — what entries() returns; one real
        # SyncRecordingEntry per visible row, in display order.
        self._entries: list[SyncRecordingEntry] = []
        self._merge_candidate_paths: set[str] = set()
        # output_path -> ordered list of constituent piece output_paths.
        self._merge_groups: dict[str, list[str]] = {}
        # Parent output_paths currently expanded. Default collapsed.
        self._expanded: set[str] = set()

    def set_entries(self, entries: list[SyncRecordingEntry]):
        self.beginResetModel()
        self._all_entries = list(entries)
        self._rebuild_display()
        self.endResetModel()

    def set_merge_groups(self, groups: dict[str, list[str]]):
        """Define expandable merge groups.

        ``groups`` maps a merged recording's ``output_path`` (the parent) to the
        ordered list of its constituent piece ``output_path``s (the children).
        """
        self.beginResetModel()
        self._merge_groups = dict(groups or {})
        # Drop expand state for groups that no longer exist.
        self._expanded &= set(self._merge_groups.keys())
        self._rebuild_display()
        self.endResetModel()

    def toggle_group(self, parent_output_path: str):
        """Flip the expand state for a merge parent and rebuild the display."""
        if parent_output_path not in self._merge_groups:
            return
        self.beginResetModel()
        if parent_output_path in self._expanded:
            self._expanded.discard(parent_output_path)
        else:
            self._expanded.add(parent_output_path)
        self._rebuild_display()
        self.endResetModel()

    def _rebuild_display(self):
        """Compute the visible display list from _all_entries + merge groups.

        Walk _all_entries in order. Parents are emitted in place; their children
        are emitted (in group piece order) immediately after, only when the
        parent is expanded. Children are never emitted at top level. Everything
        else is emitted normally.
        """
        groups = self._merge_groups
        # Set of all child output_paths across every group.
        child_paths: set[str] = set()
        for pieces in groups.values():
            child_paths.update(pieces)
        # Map output_path -> entry for fast child lookup.
        by_path: dict[str, SyncRecordingEntry] = {}
        for e in self._all_entries:
            p = e.recording.output_path
            if p:
                by_path[p] = e

        display: list[SyncRecordingEntry] = []
        for entry in self._all_entries:
            path = entry.recording.output_path
            if path in groups:
                # Parent row — always shown.
                display.append(entry)
                if path in self._expanded:
                    for piece in groups[path]:
                        child = by_path.get(piece)
                        if child is not None:
                            display.append(child)
            elif path and path in child_paths:
                # Child — only shown under its expanded parent (above).
                continue
            else:
                display.append(entry)
        self._entries = display

    def set_merge_candidates(self, paths: set[str]):
        """Paths that belong to a detected split-recording chain — tinted so
        the user can spot and multi-select them for Merge."""
        self._merge_candidate_paths = paths or set()
        if self._entries:
            top = self.index(0, 0)
            bottom = self.index(len(self._entries) - 1, len(COLUMNS) - 1)
            self.dataChanged.emit(top, bottom)

    def entries(self) -> list[SyncRecordingEntry]:
        return self._entries

    def is_parent(self, row: int) -> bool:
        """True if the entry at ``row`` is a merge parent."""
        if row < 0 or row >= len(self._entries):
            return False
        return self._entries[row].recording.output_path in self._merge_groups

    def parent_path_at(self, row: int) -> str | None:
        """The output_path if the row is a merge parent, else None."""
        if self.is_parent(row):
            return self._entries[row].recording.output_path
        return None

    def _child_paths(self) -> set[str]:
        paths: set[str] = set()
        for pieces in self._merge_groups.values():
            paths.update(pieces)
        return paths

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
        path = rec.output_path

        is_parent = path in self._merge_groups
        is_child = bool(path) and not is_parent and path in self._child_paths()

        if role == Qt.ItemDataRole.DisplayRole:
            if col_key == "device":
                return entry.device_name
            elif col_key == "status":
                if rec.downloaded and rec.local_exists:
                    return "\u2713 Downloaded"
                elif rec.downloaded:
                    return "\u2713 Skipped"
                elif rec.last_error:
                    return "\u2717 Failed"
                return "\u25cf On device"
            elif col_key == "transcribed":
                if rec.transcribed and rec.speakers_tagged:
                    return "\u2713"
                elif rec.transcribed:
                    return "\U0001f3f7"  # tag emoji
                return "\u2014"
            elif col_key == "summary":
                return "\u2713" if rec.summary_path else "\u2014"
            elif col_key == "name":
                base = rec.output_name or rec.name
                if is_parent:
                    glyph = "▾ " if path in self._expanded else "▸ "
                    n = len(self._merge_groups[path])
                    return f"{glyph}{base} ({n} pieces)"
                elif is_child:
                    return f"    ↳ {base}"
                return base
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
                if rec.transcribed and rec.speakers_tagged:
                    return _GREEN
                elif rec.transcribed:
                    return _ORANGE
                return _GRAY
            elif col_key == "summary":
                return _PURPLE if rec.summary_path else _GRAY
            elif col_key == "path":
                return _SECONDARY

        elif role == Qt.ItemDataRole.BackgroundRole:
            if rec.output_path and rec.output_path in self._merge_candidate_paths:
                return _MERGE_TINT

        elif role == Qt.ItemDataRole.ToolTipRole:
            if col_key == "status":
                if rec.last_error:
                    return f"Error: {rec.last_error}"
                if rec.downloaded and rec.local_exists:
                    return f"Downloaded to: {rec.output_path}"
                if rec.downloaded:
                    return "Skipped — marked as downloaded; won't re-download or auto-transcribe"
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
                if rec.transcribed and rec.speakers_tagged:
                    return f"Ready — Transcript: {rec.transcript_path}"
                elif rec.transcribed:
                    return "Speakers need tagging — click to open transcript"
                return "Not transcribed"
            elif col_key == "summary":
                if rec.summary_path:
                    return f"Summarised — click to view: {rec.summary_path}"
                return "Not summarised"
            elif col_key == "path":
                return rec.output_path or "Not downloaded"

        return None

    def flags(self, index):
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
