"""Transcription queue dialog — shows queue status, progress, and controls."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class TranscriptionQueueDialog(QDialog):
    """Pop-out window showing the transcription queue with pause/resume/cancel."""

    pause_clicked = pyqtSignal()
    resume_clicked = pyqtSignal()
    cancel_clicked = pyqtSignal()
    remove_clicked = pyqtSignal(int)  # index

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Transcription Queue")
        self.setMinimumSize(400, 300)
        self.resize(450, 400)
        self._items: list[dict] = []
        self._paused = False
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QHBoxLayout()
        header.setContentsMargins(12, 8, 12, 8)
        header.addWidget(QLabel("<b>Transcription Queue</b>"))
        header.addStretch()

        self._pause_btn = QPushButton("Pause")
        self._pause_btn.clicked.connect(self._on_pause)
        header.addWidget(self._pause_btn)

        self._cancel_btn = QPushButton("Cancel All")
        self._cancel_btn.setStyleSheet("color: red;")
        self._cancel_btn.clicked.connect(self.cancel_clicked.emit)
        header.addWidget(self._cancel_btn)

        header_widget = QWidget()
        header_widget.setLayout(header)
        header_widget.setStyleSheet("background: rgba(128,128,128,0.08);")
        layout.addWidget(header_widget)

        # Scroll area for items
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        self._items_widget = QWidget()
        self._items_layout = QVBoxLayout(self._items_widget)
        self._items_layout.setContentsMargins(12, 8, 12, 8)
        self._items_layout.setSpacing(4)
        self._items_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        scroll.setWidget(self._items_widget)
        layout.addWidget(scroll, stretch=1)

        # Footer
        self._footer = QLabel("")
        self._footer.setContentsMargins(12, 6, 12, 6)
        self._footer.setStyleSheet("background: rgba(128,128,128,0.08); font-size: 11px;")
        layout.addWidget(self._footer)

    def _on_pause(self):
        if self._paused:
            self._paused = False
            self._pause_btn.setText("Pause")
            self.resume_clicked.emit()
        else:
            self._paused = True
            self._pause_btn.setText("Resume")
            self.pause_clicked.emit()

    def update_queue(self, items: list[dict], paused: bool = False):
        """Update the queue display. Each item: {filename, status, progress}."""
        self._items = items
        self._paused = paused
        self._pause_btn.setText("Resume" if paused else "Pause")

        # Clear existing
        while self._items_layout.count():
            child = self._items_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        queued = 0
        active = 0
        done = 0

        for i, item in enumerate(items):
            row = QHBoxLayout()
            row.setSpacing(8)

            status = item.get("status", "queued")
            if status == "transcribing":
                icon = "⏳"
                active += 1
            elif status == "completed":
                icon = "✓"
                done += 1
            elif status == "failed":
                icon = "✗"
            elif status == "cancelled":
                icon = "—"
            else:
                icon = "⏱"
                queued += 1

            row.addWidget(QLabel(icon))

            name_label = QLabel(item.get("filename", ""))
            name_label.setMinimumWidth(200)
            row.addWidget(name_label, stretch=1)

            if status == "transcribing":
                pbar = QProgressBar()
                pbar.setFixedWidth(80)
                pbar.setFixedHeight(16)
                pbar.setValue(item.get("progress", 0))
                row.addWidget(pbar)
            elif status == "queued":
                remove_btn = QPushButton("✕")
                remove_btn.setFixedSize(20, 20)
                remove_btn.setStyleSheet("border: none; color: gray;")
                remove_btn.clicked.connect(lambda checked, idx=i: self.remove_clicked.emit(idx))
                row.addWidget(remove_btn)

            container = QWidget()
            container.setLayout(row)
            self._items_layout.addWidget(container)

        parts = []
        if active:
            parts.append(f"{active} transcribing")
        if queued:
            parts.append(f"{queued} queued")
        if done:
            parts.append(f"{done} done")
        if paused:
            parts.append("PAUSED")
        self._footer.setText(" · ".join(parts) if parts else "Queue empty")
