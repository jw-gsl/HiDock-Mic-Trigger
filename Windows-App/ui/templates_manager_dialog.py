"""Summary Templates manager dialog for Windows.

Mirrors the macOS TemplatesManagerView. Lists the user's summary template
files (.md in ~/HiDock/Summary Templates/) with Import, New, Reveal,
Open in Editor, and Delete actions. There is no in-app markdown editor —
editing happens via the user's default editor.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from core import summarize


def _reveal_in_explorer(path: Path) -> None:
    """Open a folder in the OS file browser."""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception as e:
        print(f"reveal error: {e}", file=sys.stderr)


def _open_in_editor(path: Path) -> None:
    """Open a file in the OS default editor."""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception as e:
        print(f"open editor error: {e}", file=sys.stderr)


def _starter_template(title: str) -> str:
    """A minimal starter template body matching the existing template format."""
    return (
        f"# {title}\n\n"
        "> **Extraction guidance:** Summarise the transcript under the sections "
        "below. Keep it concise and faithful to what was said.\n\n"
        "## Summary\n\n"
        "## Key Points\n\n"
        "## Action Items\n"
    )


class TemplatesManagerDialog(QDialog):
    """Dialog for managing summary templates."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Summary Templates")
        self.setMinimumSize(460, 360)
        self.resize(520, 460)

        # name -> path, populated by _reload().
        self._templates: dict[str, Path] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Header
        header = QHBoxLayout()
        title_label = QLabel("Summary Templates")
        title_label.setFont(QFont("", 14, QFont.Weight.Bold.value))
        header.addWidget(title_label)
        header.addStretch()
        self._count_label = QLabel("")
        self._count_label.setStyleSheet("color: #a6adc8;")
        header.addWidget(self._count_label)
        layout.addLayout(header)

        # List of templates
        self._list = QListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.itemDoubleClicked.connect(self._open_selected)
        layout.addWidget(self._list, stretch=1)

        # Empty-state guidance
        self._empty_label = QLabel(
            "No templates yet. Import or create one to get started."
        )
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("color: #a6adc8; font-size: 13px;")
        self._empty_label.setWordWrap(True)
        layout.addWidget(self._empty_label, stretch=1)

        # Action buttons
        actions = QHBoxLayout()
        actions.setSpacing(6)

        import_btn = QPushButton("Import…")
        import_btn.clicked.connect(self._import_template)
        actions.addWidget(import_btn)

        new_btn = QPushButton("New…")
        new_btn.clicked.connect(self._new_template)
        actions.addWidget(new_btn)

        reveal_btn = QPushButton("Reveal")
        reveal_btn.clicked.connect(self._reveal_folder)
        actions.addWidget(reveal_btn)

        actions.addStretch()
        layout.addLayout(actions)

        # Per-selection buttons
        selection = QHBoxLayout()
        selection.setSpacing(6)

        self._open_btn = QPushButton("Open in Editor")
        self._open_btn.clicked.connect(self._open_selected)
        selection.addWidget(self._open_btn)

        self._delete_btn = QPushButton("Delete")
        self._delete_btn.setStyleSheet("color: #f38ba8;")
        self._delete_btn.clicked.connect(self._delete_selected)
        selection.addWidget(self._delete_btn)

        selection.addStretch()

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        selection.addWidget(close_btn)
        layout.addLayout(selection)

        self._list.itemSelectionChanged.connect(self._update_buttons)

        self._reload()

    def _reload(self):
        """Reload the template list from disk."""
        try:
            self._templates = summarize.list_templates()
        except Exception as e:
            print(f"list_templates error: {e}", file=sys.stderr)
            self._templates = {}

        self._list.clear()
        for name in sorted(self._templates, key=str.lower):
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, str(self._templates[name]))
            self._list.addItem(item)

        count = len(self._templates)
        self._count_label.setText(f"{count} template{'s' if count != 1 else ''}")

        has_templates = count > 0
        self._list.setVisible(has_templates)
        self._empty_label.setVisible(not has_templates)
        self._update_buttons()

    def _update_buttons(self):
        has_selection = self._list.currentItem() is not None
        self._open_btn.setEnabled(has_selection)
        self._delete_btn.setEnabled(has_selection)

    def _selected_path(self) -> Path | None:
        item = self._list.currentItem()
        if item is None:
            return None
        raw = item.data(Qt.ItemDataRole.UserRole)
        return Path(str(raw)) if raw else None

    def _import_template(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Template", "", "Markdown Files (*.md);;All Files (*)"
        )
        if not path:
            return

        src = Path(path)
        dest_dir = summarize.templates_dir()
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / src.name
            # Avoid clobbering an existing template with the same name.
            if dest.exists():
                dest = dest_dir / f"{src.stem} (imported){src.suffix}"
            shutil.copy2(src, dest)
        except Exception as e:
            QMessageBox.warning(self, "Import Failed", f"Could not import template:\n{e}")
            return

        self._reload()

    def _new_template(self):
        name, ok = QInputDialog.getText(
            self, "New Template", "Template name:"
        )
        if not ok:
            return
        name = name.strip()
        if not name:
            return

        dest_dir = summarize.templates_dir()
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{name}.md"
            if dest.exists():
                QMessageBox.warning(
                    self, "Already Exists",
                    f"A template named '{name}' already exists.",
                )
                return
            dest.write_text(_starter_template(name), encoding="utf-8")
        except Exception as e:
            QMessageBox.warning(self, "Create Failed", f"Could not create template:\n{e}")
            return

        self._reload()
        _open_in_editor(dest)

    def _reveal_folder(self):
        dest_dir = summarize.templates_dir()
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"reveal mkdir error: {e}", file=sys.stderr)
        _reveal_in_explorer(dest_dir)

    def _open_selected(self):
        path = self._selected_path()
        if path is None:
            return
        _open_in_editor(path)

    def _delete_selected(self):
        path = self._selected_path()
        if path is None:
            return

        reply = QMessageBox.question(
            self, "Delete Template",
            f"Delete '{path.stem}'?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            path.unlink()
        except Exception as e:
            QMessageBox.warning(self, "Delete Failed", f"Could not delete template:\n{e}")
            return

        self._reload()
