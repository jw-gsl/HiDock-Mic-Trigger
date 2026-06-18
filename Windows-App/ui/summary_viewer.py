"""Summary viewer dialog — shows a generated summary with a classification header.

Mirrors the macOS ``SummaryViewerView.swift``: a classification header (type /
area / title / reason) above the rendered markdown body, with a Reclassify
dropdown that re-runs the AI summary against a different template in the
background.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core import summarize

# Catppuccin-ish palette, matching the rest of the Windows UI.
INDIGO = "#cba6f7"
SECONDARY = "#a6adc8"


def _reveal_in_explorer(path: str) -> None:
    """Open the folder containing ``path`` in the OS file browser."""
    folder = os.path.dirname(os.path.abspath(path))
    try:
        if sys.platform == "win32":
            # /select highlights the file in Explorer.
            subprocess.Popen(["explorer", "/select,", os.path.abspath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", os.path.abspath(path)])
        else:
            subprocess.Popen(["xdg-open", folder])
    except Exception as e:
        print(f"Failed to reveal in explorer: {e}")


def _open_in_editor(path: str) -> None:
    """Open ``path`` in the OS default editor."""
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        print(f"Failed to open in editor: {e}")


class _ResummariseWorker(QObject):
    """Runs ``summarize_transcript`` off the UI thread."""

    finished = pyqtSignal(dict)

    def __init__(self, transcript_path: str, template: str):
        super().__init__()
        self.transcript_path = transcript_path
        self.template = template

    def run(self):
        try:
            result = summarize.summarize_transcript(
                self.transcript_path, force_template=self.template
            )
        except Exception as e:
            result = {"summarized": False, "error": str(e)}
        self.finished.emit(result)


class SummaryViewer(QDialog):
    """Dialog showing a generated summary with a Reclassify control."""

    resummarized = pyqtSignal(str)  # emits the new summary_path

    def __init__(self, summary_path: str, transcript_path: str | None = None, parent=None):
        super().__init__(parent)
        self.summary_path = summary_path
        self.transcript_path = transcript_path

        self.fields: dict[str, str] = {}
        self.body: str = ""

        self._thread: QThread | None = None
        self._worker: _ResummariseWorker | None = None

        self.setWindowTitle("Summary")
        self.setMinimumSize(560, 480)
        self.resize(720, 680)

        self._load_summary()
        self._init_ui()

    # -- loading ---------------------------------------------------------

    def _load_summary(self):
        self.fields, self.body = summarize.read_summary(self.summary_path)

    def _can_reclassify(self) -> bool:
        tp = self.transcript_path
        return bool(tp) and Path(tp).expanduser().exists()

    # -- UI --------------------------------------------------------------

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._build_header(layout)

        # Rendered markdown body.
        self.body_view = QTextBrowser()
        self.body_view.setOpenExternalLinks(True)
        self.body_view.setReadOnly(True)
        self.body_view.setMarkdown(self.body)
        layout.addWidget(self.body_view, stretch=1)

        self._build_footer(layout)

    def _build_header(self, layout: QVBoxLayout):
        header = QWidget()
        header.setStyleSheet(f"background: {INDIGO}11;")
        hlayout = QVBoxLayout(header)
        hlayout.setContentsMargins(16, 12, 16, 12)
        hlayout.setSpacing(6)

        # Type row + reclassify control.
        type_row = QHBoxLayout()
        type_row.setSpacing(8)

        classified_label = QLabel("Classified as:")
        classified_label.setStyleSheet(f"color: {SECONDARY};")
        type_row.addWidget(classified_label)

        self._type_label = QLabel(self.fields.get("type", "") or "—")
        self._type_label.setStyleSheet(f"color: {INDIGO}; font-weight: 600;")
        self._type_label.setFont(QFont("", 14, QFont.Weight.DemiBold.value))
        type_row.addWidget(self._type_label)

        type_row.addStretch()

        # Reclassify dropdown + button.
        self._template_combo = QComboBox()
        templates = list(summarize.list_templates().keys())
        self._template_combo.addItems(templates)
        current = self.fields.get("type", "")
        if current in templates:
            self._template_combo.setCurrentText(current)
        type_row.addWidget(self._template_combo)

        self._resummarise_btn = QPushButton("Re-summarise")
        self._resummarise_btn.clicked.connect(self._on_resummarise)
        type_row.addWidget(self._resummarise_btn)

        if not self._can_reclassify():
            self._template_combo.setEnabled(False)
            self._resummarise_btn.setEnabled(False)

        hlayout.addLayout(type_row)

        # Title.
        title = self.fields.get("title", "") or os.path.basename(self.summary_path)
        self._title_label = QLabel(title)
        self._title_label.setFont(QFont("", 13, QFont.Weight.Bold.value))
        self._title_label.setWordWrap(True)
        hlayout.addWidget(self._title_label)

        # Classified reason (secondary).
        self._reason_label = QLabel(self.fields.get("classified", "") or "")
        self._reason_label.setStyleSheet(f"color: {SECONDARY};")
        self._reason_label.setWordWrap(True)
        self._reason_label.setVisible(bool(self.fields.get("classified")))
        hlayout.addWidget(self._reason_label)

        # Area + recorded meta line.
        meta_bits = []
        if self.fields.get("area"):
            meta_bits.append(self.fields["area"])
        if self.fields.get("recorded"):
            meta_bits.append(self.fields["recorded"])
        self._meta_label = QLabel("   •   ".join(meta_bits))
        self._meta_label.setStyleSheet(f"color: {SECONDARY}; font-size: 11px;")
        self._meta_label.setVisible(bool(meta_bits))
        hlayout.addWidget(self._meta_label)

        layout.addWidget(header)

        # Divider.
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(f"color: {SECONDARY};")
        layout.addWidget(divider)

    def _build_footer(self, layout: QVBoxLayout):
        footer = QHBoxLayout()
        footer.setContentsMargins(12, 8, 12, 8)

        reveal_btn = QPushButton("Reveal in Explorer")
        reveal_btn.clicked.connect(lambda: _reveal_in_explorer(self.summary_path))
        footer.addWidget(reveal_btn)

        open_btn = QPushButton("Open in Editor")
        open_btn.clicked.connect(lambda: _open_in_editor(self.summary_path))
        footer.addWidget(open_btn)

        footer.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        footer.addWidget(close_btn)

        layout.addLayout(footer)

    # -- reclassify ------------------------------------------------------

    def _on_resummarise(self):
        if not self._can_reclassify() or self._thread is not None:
            return
        template = self._template_combo.currentText().strip()
        if not template:
            return

        self._set_busy(True)

        self._thread = QThread(self)
        self._worker = _ResummariseWorker(str(self.transcript_path), template)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_resummarise_done)
        self._thread.start()

    def _on_resummarise_done(self, result: dict):
        self._teardown_thread()
        self._set_busy(False)

        if not result.get("summarized"):
            err = result.get("error", "Summarisation failed.")
            QMessageBox.warning(self, "Re-summarise Failed", str(err))
            return

        new_path = result.get("summary_path")
        if new_path:
            self.summary_path = str(new_path)
            self._load_summary()
            self._refresh_view()
            self.resummarized.emit(self.summary_path)

    def _refresh_view(self):
        """Reload header + body after a re-summarise."""
        self._type_label.setText(self.fields.get("type", "") or "—")
        title = self.fields.get("title", "") or os.path.basename(self.summary_path)
        self._title_label.setText(title)
        self._reason_label.setText(self.fields.get("classified", "") or "")
        self._reason_label.setVisible(bool(self.fields.get("classified")))

        meta_bits = []
        if self.fields.get("area"):
            meta_bits.append(self.fields["area"])
        if self.fields.get("recorded"):
            meta_bits.append(self.fields["recorded"])
        self._meta_label.setText("   •   ".join(meta_bits))
        self._meta_label.setVisible(bool(meta_bits))

        current = self.fields.get("type", "")
        if self._template_combo.findText(current) >= 0:
            self._template_combo.setCurrentText(current)

        self.body_view.setMarkdown(self.body)

    def _set_busy(self, busy: bool):
        self._resummarise_btn.setText("Summarising…" if busy else "Re-summarise")
        self._resummarise_btn.setEnabled(not busy)
        self._template_combo.setEnabled(not busy)

    def _teardown_thread(self):
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait()
            self._thread = None
        self._worker = None

    def closeEvent(self, event):
        self._teardown_thread()
        super().closeEvent(event)
