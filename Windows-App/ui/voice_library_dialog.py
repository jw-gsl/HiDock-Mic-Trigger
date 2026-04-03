"""Voice library management dialog for Windows."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


def _voice_library_script() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "shared" / "voice_library_lite.py"


def _run_voice_library(args: list[str]) -> str | None:
    """Run voice_library_lite.py with the given arguments and return stdout."""
    script = _voice_library_script()
    if not script.exists():
        return None
    try:
        result = subprocess.run(
            [sys.executable, str(script)] + args,
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception as e:
        print(f"voice_library_lite error: {e}")
        return None


class VoiceLibraryDialog(QDialog):
    """Dialog for managing the voice library."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Voice Library")
        self.setMinimumSize(500, 350)
        self.resize(600, 400)
        self.speakers: list[dict] = []

        self._load_speakers()
        self._init_ui()

    def _load_speakers(self):
        output = _run_voice_library(["list"])
        if output:
            try:
                self.speakers = json.loads(output)
            except json.JSONDecodeError:
                self.speakers = []
        else:
            self.speakers = []

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Header
        header = QHBoxLayout()
        title_label = QLabel("Voice Library")
        title_label.setFont(QFont("", 14, QFont.Weight.Bold.value))
        header.addWidget(title_label)
        header.addStretch()
        count_label = QLabel(f"{len(self.speakers)} speaker{'s' if len(self.speakers) != 1 else ''}")
        count_label.setStyleSheet("color: gray;")
        header.addWidget(count_label)
        layout.addLayout(header)

        if not self.speakers:
            # Empty state
            empty = QLabel(
                "No voices enrolled.\n\n"
                "Transcribe a recording with speaker labels,\n"
                "then name the speakers."
            )
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: gray; font-size: 13px;")
            layout.addWidget(empty, stretch=1)
        else:
            # Table
            self.table = QTableWidget(len(self.speakers), 4)
            self.table.setHorizontalHeaderLabels(["Name", "Samples", "Last Updated", ""])
            self.table.horizontalHeader().setStretchLastSection(False)
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
            self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
            self.table.verticalHeader().setVisible(False)
            self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
            self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

            for i, speaker in enumerate(self.speakers):
                name_item = QTableWidgetItem(speaker.get("name", ""))
                self.table.setItem(i, 0, name_item)

                count_item = QTableWidgetItem(str(speaker.get("sample_count", 0)))
                count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(i, 1, count_item)

                updated = speaker.get("last_updated", "")
                if updated:
                    # Show just the date portion
                    updated = updated[:10] if len(updated) >= 10 else updated
                updated_item = QTableWidgetItem(updated)
                self.table.setItem(i, 2, updated_item)

                # Delete button
                del_btn = QPushButton("Delete")
                del_btn.setStyleSheet("color: red; border: none; padding: 2px 8px;")
                del_btn.clicked.connect(lambda checked, n=speaker.get("name", ""): self._delete_speaker(n))
                self.table.setCellWidget(i, 3, del_btn)

            self.table.doubleClicked.connect(self._on_double_click)
            layout.addWidget(self.table, stretch=1)

        # Bottom buttons
        bottom = QHBoxLayout()
        bottom.addStretch()

        if self.speakers:
            rename_btn = QPushButton("Rename Selected")
            rename_btn.clicked.connect(self._rename_selected)
            bottom.addWidget(rename_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)

        layout.addLayout(bottom)

    def _delete_speaker(self, name: str):
        reply = QMessageBox.question(
            self, "Delete Speaker",
            f"Delete '{name}' from the voice library?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        result = _run_voice_library(["delete", "--name", name])
        if result is not None:
            self._reload()
        else:
            QMessageBox.warning(self, "Error", f"Failed to delete speaker '{name}'.")

    def _rename_selected(self):
        if not hasattr(self, "table"):
            return
        row = self.table.currentRow()
        if row < 0 or row >= len(self.speakers):
            return
        old_name = self.speakers[row].get("name", "")
        self._do_rename(old_name)

    def _on_double_click(self, index):
        row = index.row()
        if row < 0 or row >= len(self.speakers):
            return
        old_name = self.speakers[row].get("name", "")
        self._do_rename(old_name)

    def _do_rename(self, old_name: str):
        new_name, ok = QInputDialog.getText(
            self, "Rename Speaker",
            f"Enter new name for '{old_name}':",
            text=old_name,
        )
        if not ok or not new_name.strip() or new_name.strip() == old_name:
            return

        result = _run_voice_library(["rename", "--old", old_name, "--new", new_name.strip()])
        if result is not None:
            self._reload()
        else:
            QMessageBox.warning(self, "Error", f"Failed to rename speaker '{old_name}'.")

    def _reload(self):
        """Reload speakers and rebuild UI."""
        self._load_speakers()
        # Remove all widgets and rebuild
        layout = self.layout()
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
            elif child.layout():
                while child.layout().count():
                    sub = child.layout().takeAt(0)
                    if sub.widget():
                        sub.widget().deleteLater()
        self._init_ui()
