"""Trim audio dialog — lets the user specify start/end times for an audio clip."""
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QMessageBox,
)


class TrimDialog(QDialog):
    def __init__(self, filepath: str, duration: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Trim Audio")
        self.setFixedWidth(300)
        self._start = 0.0
        self._end = duration
        self._save_as_copy = True

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(f"<b>{Path(filepath).name}</b>"))
        layout.addWidget(QLabel(f"Duration: {self._format_time(duration)}"))

        row_start = QHBoxLayout()
        row_start.addWidget(QLabel("Start:"))
        self._start_edit = QLineEdit("00:00")
        self._start_edit.setFixedWidth(80)
        row_start.addWidget(self._start_edit)
        row_start.addStretch()
        layout.addLayout(row_start)

        row_end = QHBoxLayout()
        row_end.addWidget(QLabel("End:"))
        self._end_edit = QLineEdit(self._format_time(duration))
        self._end_edit.setFixedWidth(80)
        row_end.addWidget(self._end_edit)
        row_end.addStretch()
        layout.addLayout(row_end)

        self._copy_check = QCheckBox("Save as copy")
        self._copy_check.setChecked(True)
        layout.addWidget(self._copy_check)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        trim_btn = QPushButton("Trim")
        trim_btn.setDefault(True)
        trim_btn.clicked.connect(self._on_trim)
        btn_row.addWidget(trim_btn)
        layout.addLayout(btn_row)

        self._duration = duration

    def _on_trim(self):
        start = self._parse_time(self._start_edit.text())
        end = self._parse_time(self._end_edit.text())
        if start is None:
            QMessageBox.warning(self, "Invalid", "Invalid start time (use MM:SS or HH:MM:SS)")
            return
        if end is None:
            QMessageBox.warning(self, "Invalid", "Invalid end time (use MM:SS or HH:MM:SS)")
            return
        if start >= end:
            QMessageBox.warning(self, "Invalid", "Start must be before end")
            return
        if end > self._duration + 1:
            QMessageBox.warning(self, "Invalid", "End exceeds recording duration")
            return
        self._start = start
        self._end = end
        self._save_as_copy = self._copy_check.isChecked()
        self.accept()

    def result_values(self) -> tuple[float, float, bool]:
        return self._start, self._end, self._save_as_copy

    @staticmethod
    def _format_time(seconds: float) -> str:
        total = int(seconds)
        h, remainder = divmod(total, 3600)
        m, s = divmod(remainder, 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    @staticmethod
    def _parse_time(text: str) -> float | None:
        parts = text.strip().split(":")
        try:
            nums = [int(p) for p in parts]
        except ValueError:
            return None
        if len(nums) == 2:
            return float(nums[0] * 60 + nums[1])
        if len(nums) == 3:
            return float(nums[0] * 3600 + nums[1] * 60 + nums[2])
        return None
