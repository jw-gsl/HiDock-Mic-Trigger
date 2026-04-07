"""Model Manager dialog — download/delete speech processing models.

Mirrors the macOS ModelManagerView. Shows all registered models with
name, description, size, install status, and download/delete controls.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
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

# Resolve the shared module path
_SHARED_DIR = Path(__file__).resolve().parent.parent.parent / "shared"


def _get_model_statuses() -> dict:
    """Call shared/models.py status and return parsed JSON."""
    try:
        sys.path.insert(0, str(_SHARED_DIR.parent))
        from shared.models import get_model_status
        return get_model_status()
    except Exception:
        return {}


def _download_model(key: str, on_progress=None) -> bool:
    """Download a model by registry key."""
    try:
        sys.path.insert(0, str(_SHARED_DIR.parent))
        from shared.models import MODEL_REGISTRY, download_model_if_needed
        if key not in MODEL_REGISTRY:
            return False
        info = MODEL_REGISTRY[key]
        download_model_if_needed(info["url"], info["filename"], on_progress=on_progress)
        return True
    except Exception as e:
        print(f"Download error: {e}", file=sys.stderr)
        return False


def _delete_model(key: str) -> bool:
    """Delete a model by registry key."""
    try:
        sys.path.insert(0, str(_SHARED_DIR.parent))
        from shared.models import delete_model
        return delete_model(key)
    except Exception:
        return False


class ModelRowWidget(QWidget):
    """A single row in the model manager showing one model."""

    downloadRequested = pyqtSignal(str)  # model key
    deleteRequested = pyqtSignal(str)  # model key

    def __init__(self, key: str, info: dict, parent=None):
        super().__init__(parent)
        self.key = key
        self._downloading = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)

        # Left: icon
        self.icon_label = QLabel()
        self.icon_label.setFixedWidth(24)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.icon_label)

        # Center: text content
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)

        name_row = QHBoxLayout()
        self.name_label = QLabel(info.get("name", key))
        self.name_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        name_row.addWidget(self.name_label)
        name_row.addStretch()
        self.size_label = QLabel(f"{info.get('size_mb', 0)} MB")
        self.size_label.setStyleSheet("color: gray;")
        name_row.addWidget(self.size_label)
        text_layout.addLayout(name_row)

        self.desc_label = QLabel(info.get("description", ""))
        self.desc_label.setStyleSheet("color: gray; font-size: 11px;")
        self.desc_label.setWordWrap(True)
        text_layout.addWidget(self.desc_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setFixedHeight(16)
        text_layout.addWidget(self.progress_bar)

        layout.addLayout(text_layout, stretch=1)

        # Right: status + button
        btn_layout = QVBoxLayout()
        btn_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        btn_layout.setSpacing(4)

        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        btn_layout.addWidget(self.status_label)

        self.action_btn = QPushButton()
        self.action_btn.setFixedWidth(80)
        self.action_btn.clicked.connect(self._on_action)
        btn_layout.addWidget(self.action_btn)

        layout.addLayout(btn_layout)

        self._installed = info.get("installed", False)
        self._update_ui()

    def _update_ui(self):
        if self._downloading:
            self.icon_label.setText("\u23f3")  # hourglass
            self.status_label.setText("Downloading...")
            self.status_label.setStyleSheet("color: #89b4fa; font-size: 11px;")
            self.action_btn.setVisible(False)
            self.progress_bar.setVisible(True)
        elif self._installed:
            self.icon_label.setText("\u2705")  # checkmark
            self.status_label.setText("Installed")
            self.status_label.setStyleSheet("color: #a6e3a1; font-size: 11px;")
            self.action_btn.setText("Delete")
            self.action_btn.setVisible(True)
            self.action_btn.setStyleSheet("")
            self.progress_bar.setVisible(False)
        else:
            self.icon_label.setText("\u2B07")  # down arrow
            self.status_label.setText("Not installed")
            self.status_label.setStyleSheet("color: #f9e2af; font-size: 11px;")
            self.action_btn.setText("Download")
            self.action_btn.setVisible(True)
            self.action_btn.setStyleSheet("")
            self.progress_bar.setVisible(False)

    def _on_action(self):
        if self._installed:
            self.deleteRequested.emit(self.key)
        else:
            self.downloadRequested.emit(self.key)

    def set_downloading(self, downloading: bool):
        self._downloading = downloading
        if not downloading:
            self.progress_bar.setValue(0)
        self._update_ui()

    def set_progress(self, pct: int):
        self.progress_bar.setValue(pct)

    def set_installed(self, installed: bool):
        self._installed = installed
        self._downloading = False
        self._update_ui()


class ModelManagerDialog(QDialog):
    """Dialog for managing speech processing models."""

    _download_progress_signal = pyqtSignal(str, int, int)  # key, downloaded, total
    _download_done_signal = pyqtSignal(str, bool)  # key, success

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Models")
        self.setMinimumSize(520, 380)
        self.resize(560, 420)

        self._rows: dict[str, ModelRowWidget] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QHBoxLayout()
        header.setContentsMargins(16, 12, 16, 8)
        title = QLabel("Models")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        header.addWidget(title)
        header.addStretch()
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh)
        header.addWidget(refresh_btn)
        layout.addLayout(header)

        # Scrollable content area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(0)
        self._content_layout.addStretch()
        scroll.setWidget(self._content)
        layout.addWidget(scroll, stretch=1)

        # Footer
        footer = QHBoxLayout()
        footer.setContentsMargins(16, 8, 16, 12)
        footer.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        footer.addWidget(close_btn)
        layout.addLayout(footer)

        # Connect signals
        self._download_progress_signal.connect(self._on_download_progress)
        self._download_done_signal.connect(self._on_download_done)

        # Load initial data
        self._refresh()

    def _refresh(self):
        statuses = _get_model_statuses()
        if not statuses:
            return

        # Sort: largest first
        sorted_keys = sorted(statuses.keys(), key=lambda k: -statuses[k].get("size_mb", 0))

        # Clear existing rows
        for key in list(self._rows.keys()):
            if key not in statuses:
                w = self._rows.pop(key)
                self._content_layout.removeWidget(w)
                w.deleteLater()

        # Add/update rows
        for i, key in enumerate(sorted_keys):
            if key in self._rows:
                row = self._rows[key]
                row.set_installed(statuses[key].get("installed", False))
            else:
                row = ModelRowWidget(key, statuses[key])
                row.downloadRequested.connect(self._start_download)
                row.deleteRequested.connect(self._do_delete)
                self._rows[key] = row
                # Insert before the stretch
                self._content_layout.insertWidget(i, row)

    @pyqtSlot(str)
    def _start_download(self, key: str):
        if key in self._rows:
            self._rows[key].set_downloading(True)

        def _worker():
            def _progress(downloaded, total):
                self._download_progress_signal.emit(key, downloaded, total)

            ok = _download_model(key, on_progress=_progress)
            self._download_done_signal.emit(key, ok)

        threading.Thread(target=_worker, daemon=True).start()

    @pyqtSlot(str, int, int)
    def _on_download_progress(self, key: str, downloaded: int, total: int):
        if key in self._rows and total > 0:
            pct = int(downloaded * 100 / total)
            self._rows[key].set_progress(pct)

    @pyqtSlot(str, bool)
    def _on_download_done(self, key: str, success: bool):
        if key in self._rows:
            self._rows[key].set_downloading(False)
            if success:
                self._rows[key].set_installed(True)

    @pyqtSlot(str)
    def _do_delete(self, key: str):
        ok = _delete_model(key)
        if ok and key in self._rows:
            self._rows[key].set_installed(False)
