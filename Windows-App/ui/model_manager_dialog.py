"""Model Manager dialog — manage speech-processing models.

Mirrors the macOS ModelManagerView rethink: a two-tier layout that
separates the user's primary backend choices ("Pipeline Stages":
Transcription, Diarization) from the infrastructure backends those
stages depend on ("Supporting Models": VAD, Speaker Embeddings).

Within each stage, every candidate backend is shown as a row with a
radio-style active selector, so stages with alternatives (Whisper vs
Parakeet, Lite vs Sortformer) are pick-one. Each row offers
download/delete with a linear progress bar, marks experimental and
built-in (code-only) entries, and shows size in GB once >= 1024 MB.

The model catalogue, per-model metadata (stage, size, installed state,
experimental/built-in flags) and the *active backend per stage* are all
driven by the shared registry in `shared/models.py`:

  - `get_model_status()` returns every registered model keyed by its
    registry key, each already carrying stage / stage_label / category /
    backend_key / active / installed / experimental / built_in /
    used_by / depends_on / size_mb. We do NOT keep a parallel static
    registry here — the shared module is the single source of truth.
  - Selecting an active backend calls `set_active_backend(stage,
    backend_key)` which persists to ~/HiDock/pipeline_backends.json —
    the exact same file `shared/pipeline_dispatch.py` reads at runtime
    and the macOS app writes. We do not invent a new format.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

# Make `shared/` importable. The repo root is three levels up from this
# file (Windows-App/ui/model_manager_dialog.py -> repo root).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Top-level categorisation, matching the macOS layout. Pipeline stages
# are the user's direct backend choices; supporting stages hold the
# infrastructure models those backends depend on. Order is fixed so the
# UI is stable regardless of dict iteration order.
_PIPELINE_STAGE_ORDER = ["transcription", "diarization"]
_SUPPORTING_STAGE_ORDER = ["vad", "embedding"]

# Catppuccin-ish palette, matching the existing dialog's accent colours.
_C_ACTIVE = "#a6e3a1"      # green — installed / active
_C_DOWNLOAD = "#f9e2af"    # yellow — not installed
_C_PROGRESS = "#89b4fa"    # blue — downloading
_C_EXPERIMENTAL = "#fab387"  # peach — experimental badge
_C_BUILTIN = "#9399b2"     # overlay grey — built-in badge
_C_MUTED = "#a6adc8"       # subtext


def _friendly_stage(stage: str) -> str:
    return {
        "transcription": "Transcription",
        "diarization": "Speaker Diarization",
        "vad": "Voice Activity Detection",
        "embedding": "Speaker Embeddings",
    }.get(stage, stage.capitalize())


def _format_size(mb: int) -> str:
    """Human-readable size — switches to GB once we cross 1024 MB so the
    Models screen reads '1.2 GB' instead of '1200 MB'."""
    if mb >= 1024:
        gb = mb / 1024.0
        return f"{gb:.1f} GB" if gb < 10 else f"{int(round(gb))} GB"
    return f"{mb} MB"


def _get_model_statuses() -> dict:
    """Call shared/models.py and return the per-model status dict."""
    try:
        from shared.models import get_model_status
        return get_model_status()
    except Exception as e:
        print(f"Model status error: {e}", file=sys.stderr)
        return {}


def _download_model(key: str, on_progress=None) -> bool:
    """Download (or pip-install) a model by registry key.

    Mirrors `shared/models._cli()`'s download branch so built-in,
    pip-installable (TEN VAD, NeMo Sortformer) and plain file models all
    work from the GUI.
    """
    try:
        from shared.models import MODEL_REGISTRY, download_model_if_needed
        if key not in MODEL_REGISTRY:
            return False
        info = MODEL_REGISTRY[key]
        if info.get("built_in"):
            return True
        if info.get("pip_package"):
            import subprocess
            pkg = info["pip_package"]
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", pkg],
                capture_output=True, text=True,
            )
            return result.returncode == 0
        download_model_if_needed(info["url"], info["filename"], on_progress=on_progress)
        return True
    except Exception as e:
        print(f"Download error: {e}", file=sys.stderr)
        return False


def _delete_model(key: str) -> bool:
    """Delete a model by registry key."""
    try:
        from shared.models import delete_model
        return delete_model(key)
    except Exception:
        return False


def _set_active_backend(stage: str, backend_key: str) -> bool:
    """Persist the active backend for a stage to pipeline_backends.json."""
    try:
        from shared.models import set_active_backend
        set_active_backend(stage, backend_key)
        return True
    except Exception as e:
        print(f"Set-active error: {e}", file=sys.stderr)
        return False


class ModelRowWidget(QWidget):
    """A single model row: active selector, name + badges + size,
    description, dependency copy, progress bar, and download/delete."""

    downloadRequested = pyqtSignal(str)   # registry key
    deleteRequested = pyqtSignal(str)     # registry key
    activateRequested = pyqtSignal(str)   # registry key

    def __init__(self, key: str, info: dict, allow_selection: bool, parent=None):
        super().__init__(parent)
        self.key = key
        self._info = dict(info)
        self._allow_selection = allow_selection
        self._downloading = False
        self._installed = info.get("installed", False)
        self._active = info.get("active", False)
        self._built_in = info.get("built_in", False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(12)

        # ── Left: active selector ────────────────────────────────────
        self.radio = QRadioButton()
        self.radio.setFixedWidth(24)
        self.radio.toggled.connect(self._on_radio_toggled)
        # Single-candidate stages show a static state icon instead of a
        # pick-one radio (there's nothing to choose).
        self.state_icon = QLabel()
        self.state_icon.setFixedWidth(24)
        self.state_icon.setAlignment(Qt.AlignmentFlag.AlignTop)
        if allow_selection:
            layout.addWidget(self.radio, alignment=Qt.AlignmentFlag.AlignTop)
            self.state_icon.setVisible(False)
        else:
            layout.addWidget(self.state_icon, alignment=Qt.AlignmentFlag.AlignTop)
            self.radio.setVisible(False)

        # ── Center: text content ─────────────────────────────────────
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)

        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        self.name_label = QLabel(info.get("name", key))
        self.name_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        name_row.addWidget(self.name_label)

        self.active_badge = self._make_badge("ACTIVE", "#1e1e2e", _C_ACTIVE)
        name_row.addWidget(self.active_badge)
        if self._built_in:
            name_row.addWidget(self._make_badge("BUILT-IN", "#1e1e2e", _C_BUILTIN))
        if info.get("experimental"):
            name_row.addWidget(self._make_badge("EXPERIMENTAL", "#1e1e2e", _C_EXPERIMENTAL))

        name_row.addStretch()
        size_mb = info.get("size_mb", 0)
        size_text = "" if (self._built_in or size_mb <= 0) else _format_size(size_mb)
        self.size_label = QLabel(size_text)
        self.size_label.setStyleSheet(f"color: {_C_MUTED};")
        name_row.addWidget(self.size_label)
        text_layout.addLayout(name_row)

        self.desc_label = QLabel(info.get("description", ""))
        self.desc_label.setStyleSheet(f"color: {_C_MUTED}; font-size: 11px;")
        self.desc_label.setWordWrap(True)
        text_layout.addWidget(self.desc_label)

        # Stage-relationship copy, matching the macOS "Uses:" / "Used by:".
        depends_on = info.get("depends_on", "")
        used_by = info.get("used_by", "")
        if depends_on:
            rel = QLabel(f"Uses: {depends_on}")
            rel.setStyleSheet(f"color: {_C_MUTED}; font-size: 10px; font-style: italic;")
            rel.setWordWrap(True)
            text_layout.addWidget(rel)
        if used_by:
            rel = QLabel(f"Used by: {used_by}")
            rel.setStyleSheet(f"color: {_C_MUTED}; font-size: 10px; font-style: italic;")
            rel.setWordWrap(True)
            text_layout.addWidget(rel)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setFixedHeight(16)
        text_layout.addWidget(self.progress_bar)

        layout.addLayout(text_layout, stretch=1)

        # ── Right: status + action button ────────────────────────────
        btn_layout = QVBoxLayout()
        btn_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        btn_layout.setSpacing(4)

        self.status_label = QLabel()
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        btn_layout.addWidget(self.status_label)

        self.action_btn = QPushButton()
        self.action_btn.setFixedWidth(84)
        self.action_btn.clicked.connect(self._on_action)
        btn_layout.addWidget(self.action_btn)

        layout.addLayout(btn_layout)

        self._update_ui()

    @staticmethod
    def _make_badge(text: str, fg: str, bg: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"color: {fg}; background-color: {bg}; font-size: 9px; "
            "font-weight: bold; padding: 1px 5px; border-radius: 6px;"
        )
        return lbl

    # ── State rendering ──────────────────────────────────────────────
    def _update_ui(self):
        self.active_badge.setVisible(self._active and self._installed and not self._built_in)

        # Active selector reflects installed/active state. Only installed
        # rows can be promoted; not-installed rows can't be selected until
        # downloaded (matches the macOS radio behaviour).
        if self._allow_selection:
            self.radio.blockSignals(True)
            self.radio.setChecked(self._active)
            self.radio.setEnabled(self._installed)
            self.radio.blockSignals(False)
        else:
            self.state_icon.setText("✅" if self._installed else "⬇")

        if self._downloading:
            self.status_label.setText("Downloading...")
            self.status_label.setStyleSheet(f"color: {_C_PROGRESS}; font-size: 11px;")
            self.action_btn.setVisible(False)
            self.progress_bar.setVisible(True)
        elif self._built_in:
            self.status_label.setText("Always on")
            self.status_label.setStyleSheet(f"color: {_C_MUTED}; font-size: 11px;")
            self.action_btn.setVisible(False)
            self.progress_bar.setVisible(False)
        elif self._installed:
            self.status_label.setText("Installed")
            self.status_label.setStyleSheet(f"color: {_C_ACTIVE}; font-size: 11px;")
            self.action_btn.setText("Delete")
            self.action_btn.setVisible(True)
            # Deleting the active backend would leave the pipeline broken
            # — gate deletion behind "not currently active".
            self.action_btn.setEnabled(not self._active)
            self.action_btn.setToolTip(
                "Can't delete the active backend — pick a different one first"
                if self._active else "Remove this model from disk"
            )
            self.progress_bar.setVisible(False)
        else:
            self.status_label.setText("Not installed")
            self.status_label.setStyleSheet(f"color: {_C_DOWNLOAD}; font-size: 11px;")
            self.action_btn.setText("Install" if self._info.get("nemo_model") else "Download")
            self.action_btn.setVisible(True)
            self.action_btn.setEnabled(True)
            self.action_btn.setToolTip("")
            self.progress_bar.setVisible(False)

    def _on_action(self):
        if self._installed:
            self.deleteRequested.emit(self.key)
        else:
            self.downloadRequested.emit(self.key)

    def _on_radio_toggled(self, checked: bool):
        # Only react to a user promoting this row — ignore the
        # programmatic unchecks done by QButtonGroup / _update_ui.
        if checked and self._installed and not self._active:
            self.activateRequested.emit(self.key)

    # ── External setters used by the dialog ─────────────────────────
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

    def set_active(self, active: bool):
        self._active = active
        self._update_ui()


class ModelManagerDialog(QDialog):
    """Two-tier model manager: Pipeline Stages vs Supporting Models."""

    _download_progress_signal = pyqtSignal(str, int, int)  # key, downloaded, total
    _download_done_signal = pyqtSignal(str, bool)          # key, success

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Models")
        self.setMinimumSize(560, 460)
        self.resize(620, 540)

        # key -> ModelRowWidget; stage -> QButtonGroup (radio exclusivity)
        self._rows: dict[str, ModelRowWidget] = {}
        self._stage_groups: dict[str, QButtonGroup] = {}
        # registry key -> stage, so a download-completion can refresh the
        # right stage's active state.
        self._key_stage: dict[str, str] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header ───────────────────────────────────────────────────
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

        # ── Scrollable content ───────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(0)
        scroll.setWidget(self._content)
        layout.addWidget(scroll, stretch=1)

        # ── Footer ───────────────────────────────────────────────────
        footer = QHBoxLayout()
        footer.setContentsMargins(16, 8, 16, 12)
        footer.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        footer.addWidget(close_btn)
        layout.addLayout(footer)

        self._download_progress_signal.connect(self._on_download_progress)
        self._download_done_signal.connect(self._on_download_done)

        self._refresh()

    # ── Layout building ──────────────────────────────────────────────
    def _clear_content(self):
        """Tear down all rows and headers so a refresh rebuilds cleanly."""
        self._rows.clear()
        self._stage_groups.clear()
        self._key_stage.clear()
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _section_header(self, title: str, subtitle: str):
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("font-size: 14px; font-weight: bold; padding: 14px 16px 0 16px;")
        self._content_layout.addWidget(title_lbl)
        sub_lbl = QLabel(subtitle)
        sub_lbl.setWordWrap(True)
        sub_lbl.setStyleSheet(f"color: {_C_MUTED}; font-size: 11px; padding: 2px 16px 4px 16px;")
        self._content_layout.addWidget(sub_lbl)
        self._content_layout.addWidget(self._divider())

    def _stage_header(self, stage: str, count: int):
        text = _friendly_stage(stage)
        if count > 1:
            text += "  — pick one"
        lbl = QLabel(text)
        lbl.setStyleSheet("font-size: 12px; font-weight: bold; padding: 12px 16px 4px 16px;")
        self._content_layout.addWidget(lbl)

    @staticmethod
    def _divider() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #45475a;")
        return line

    def _add_stage_rows(self, stage: str, entries: list[tuple[str, dict]]):
        """Render every backend candidate for one stage. Entries are
        (key, status) tuples already sorted active-first."""
        allow_selection = len(entries) > 1
        if allow_selection:
            group = QButtonGroup(self)
            group.setExclusive(True)
            self._stage_groups[stage] = group

        for key, status in entries:
            row = ModelRowWidget(key, status, allow_selection)
            row.downloadRequested.connect(self._start_download)
            row.deleteRequested.connect(self._do_delete)
            row.activateRequested.connect(self._do_activate)
            self._rows[key] = row
            self._key_stage[key] = stage
            if allow_selection:
                self._stage_groups[stage].addButton(row.radio)
            self._content_layout.addWidget(row)
            self._content_layout.addWidget(self._divider())

    def _refresh(self):
        statuses = _get_model_statuses()
        self._clear_content()
        if not statuses:
            msg = QLabel("Could not load model statuses.")
            msg.setStyleSheet(f"color: {_C_MUTED}; padding: 24px;")
            msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._content_layout.addWidget(msg)
            self._content_layout.addStretch()
            return

        # Group by stage, sorting active-first then built-in then name so
        # the current selection sits at the top of each stage.
        by_stage: dict[str, list[tuple[str, dict]]] = {}
        for key, status in statuses.items():
            by_stage.setdefault(status.get("stage", "other"), []).append((key, status))
        for stage in by_stage:
            by_stage[stage].sort(key=lambda kv: (
                not kv[1].get("active", False),
                not kv[1].get("built_in", False),
                kv[1].get("name", kv[0]),
            ))

        self._section_header(
            "Pipeline Stages",
            "Your primary choices — what transforms audio into diarized transcripts.",
        )
        for stage in _PIPELINE_STAGE_ORDER:
            entries = by_stage.get(stage)
            if entries:
                self._stage_header(stage, len(entries))
                self._add_stage_rows(stage, entries)

        self._section_header(
            "Supporting Models",
            "Infrastructure consumed by one or more pipeline backends. Each "
            "stage is also pick-one so alternatives can land later.",
        )
        for stage in _SUPPORTING_STAGE_ORDER:
            entries = by_stage.get(stage)
            if entries:
                self._stage_header(stage, len(entries))
                self._add_stage_rows(stage, entries)

        self._content_layout.addStretch()

    # ── Download / delete / activate ─────────────────────────────────
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

    @pyqtSlot(str)
    def _do_activate(self, key: str):
        """Persist a new active backend for the stage this key belongs to,
        then sync the ACTIVE badges + delete-gating across the stage."""
        stage = self._key_stage.get(key)
        statuses = _get_model_statuses()
        backend_key = statuses.get(key, {}).get("backend_key")
        if not stage or not backend_key:
            return
        if not _set_active_backend(stage, backend_key):
            return
        # Re-read derived active flags and update every row in the stage.
        fresh = _get_model_statuses()
        for other_key, row in self._rows.items():
            if self._key_stage.get(other_key) == stage:
                row.set_active(fresh.get(other_key, {}).get("active", False))
