"""Unified main window — mirrors the macOS hidock-mic-trigger app.

Layout (top to bottom):
  1. Menu bar
  2. Three card groups: Mic Trigger, USB Sync, Transcription
  3. Recording table
  4. Progress bar
  5. Status bar
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QSettings, QTimer, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QSystemTrayIcon,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from core.config import HIDOCK_ROOT, RECORDINGS_DIR, RAW_TRANSCRIPTS_DIR, whisper_model_ready
from core.mic_trigger import MicTrigger, list_audio_input_devices
from core.usb_sync import SyncRecording, SyncRecordingEntry, extractor_ready, run_extractor
from core.transcription import get_transcription_status
from ui.recording_model import RecordingTableModel


class MainWindow(QMainWindow):
    # Signals for cross-thread UI updates
    _log_signal = pyqtSignal(str)
    _sync_complete_signal = pyqtSignal(object, object)  # (data, error)
    _transcription_status_signal = pyqtSignal(dict)
    _progress_signal = pyqtSignal(int, int, str)  # (value, max, label_text)

    _model_download_signal = pyqtSignal(int, int)       # bytes_downloaded, total_bytes
    _model_download_done_signal = pyqtSignal()
    _model_download_error_signal = pyqtSignal(str)

    def __init__(self, tray_icon: QSystemTrayIcon | None = None):
        super().__init__()
        self._tray_icon = tray_icon
        self._force_quit = False
        self.settings = QSettings("HiDock", "HiDockTools")
        self.mic_trigger = MicTrigger(
            on_state_change=self._on_mic_state_change,
            on_log=self._on_mic_log,
        )
        self._entries: list[SyncRecordingEntry] = []
        self._sync_busy = False
        self._trigger_start_time: float | None = None

        self._init_menu_bar()
        self._init_ui()
        self._init_context_menu()
        self._init_shortcuts()
        self._connect_signals()
        self._load_settings()
        self._refresh_mic_list()
        self._restore_geometry()

        # Uptime timer
        self._uptime_timer = QTimer(self)
        self._uptime_timer.timeout.connect(self._update_uptime)
        self._uptime_timer.start(1000)

        # USB auto-refresh timer (10 seconds)
        self._usb_check_timer = QTimer(self)
        self._usb_check_timer.timeout.connect(self._usb_auto_check)
        self._usb_check_timer.start(10_000)
        self._last_extractor_ready = False

    # ── Menu Bar ────────────────────────────────────────────────────────────

    def _init_menu_bar(self):
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("File")
        rec_folder_act = file_menu.addAction("Recordings Folder...")
        rec_folder_act.triggered.connect(self._choose_recordings_folder)
        trans_folder_act = file_menu.addAction("Transcript Folder...")
        trans_folder_act.triggered.connect(self._choose_transcript_folder)
        file_menu.addSeparator()
        quit_act = file_menu.addAction("Quit")
        quit_act.setShortcut(QKeySequence("Ctrl+Q"))
        quit_act.triggered.connect(self._quit_app)

        # Actions menu
        actions_menu = menubar.addMenu("Actions")
        refresh_act = actions_menu.addAction("Refresh")
        refresh_act.setShortcut(QKeySequence("Ctrl+R"))
        refresh_act.triggered.connect(self._refresh_status)
        dl_new_act = actions_menu.addAction("Download New")
        dl_new_act.triggered.connect(self._download_new)
        trans_all_act = actions_menu.addAction("Transcribe All")
        trans_all_act.triggered.connect(self._transcribe_all)
        dl_model_act = actions_menu.addAction("Download Model")
        dl_model_act.triggered.connect(self._download_model)

        # Trigger menu
        trigger_menu = menubar.addMenu("Trigger")
        start_act = trigger_menu.addAction("Start")
        start_act.setShortcut(QKeySequence("Ctrl+S"))
        start_act.triggered.connect(self._start_trigger)
        stop_act = trigger_menu.addAction("Stop")
        stop_act.triggered.connect(self._stop_trigger)

        # Help menu
        help_menu = menubar.addMenu("Help")
        about_act = help_menu.addAction("About")
        about_act.triggered.connect(self._show_about)

    # ── UI Layout ───────────────────────────────────────────────────────────

    def _init_ui(self):
        self.setWindowTitle("HiDock")
        self.setMinimumSize(1000, 620)
        self.resize(1140, 700)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 10, 14, 6)
        root.setSpacing(8)

        # Top cards row
        cards_row = QHBoxLayout()
        cards_row.setSpacing(10)

        # ── Card 1: Mic Trigger ─────────────────────────────────────────
        trigger_box = QGroupBox("Mic Trigger")
        tl = QVBoxLayout(trigger_box)
        tl.setContentsMargins(10, 10, 10, 10)
        tl.setSpacing(6)

        # Status row
        status_row = QHBoxLayout()
        self.trigger_status_dot = QLabel("\u25cf")
        self.trigger_status_dot.setObjectName("statusDotStopped")
        self.trigger_status_dot.setFixedWidth(20)
        status_row.addWidget(self.trigger_status_dot)
        self.trigger_status_label = QLabel("Stopped")
        status_row.addWidget(self.trigger_status_label)
        status_row.addStretch()
        self.trigger_uptime_label = QLabel("")
        self.trigger_uptime_label.setObjectName("secondaryLabel")
        status_row.addWidget(self.trigger_uptime_label)
        tl.addLayout(status_row)

        # Buttons row
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.start_btn.setObjectName("successButton")
        self.start_btn.clicked.connect(self._start_trigger)
        btn_row.addWidget(self.start_btn)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("dangerButton")
        self.stop_btn.clicked.connect(self._stop_trigger)
        self.stop_btn.setEnabled(False)
        btn_row.addWidget(self.stop_btn)
        tl.addLayout(btn_row)

        # Mic combo
        mic_row = QHBoxLayout()
        mic_row.addWidget(QLabel("Trigger Mic:"))
        self.mic_combo = QComboBox()
        self.mic_combo.setMinimumWidth(180)
        self.mic_combo.currentTextChanged.connect(self._on_mic_changed)
        mic_row.addWidget(self.mic_combo, stretch=1)
        tl.addLayout(mic_row)

        self.auto_start_check = QCheckBox("Auto-start on launch")
        self.auto_start_check.stateChanged.connect(self._on_auto_start_changed)
        tl.addWidget(self.auto_start_check)

        cards_row.addWidget(trigger_box, stretch=1)

        # ── Card 2: USB Sync ────────────────────────────────────────────
        sync_box = QGroupBox("USB Sync")
        sl = QVBoxLayout(sync_box)
        sl.setContentsMargins(10, 10, 10, 10)
        sl.setSpacing(6)

        # Connection status row
        conn_row = QHBoxLayout()
        self.sync_status_dot = QLabel("\u25cf")
        self.sync_status_dot.setObjectName("statusDotDisconnected")
        self.sync_status_dot.setFixedWidth(20)
        conn_row.addWidget(self.sync_status_dot)
        self.sync_status_label = QLabel("Not loaded")
        conn_row.addWidget(self.sync_status_label)
        conn_row.addStretch()
        self.summary_label = QLabel("")
        self.summary_label.setObjectName("secondaryLabel")
        conn_row.addWidget(self.summary_label)
        sl.addLayout(conn_row)

        # Button rows
        sync_btn_row1 = QHBoxLayout()
        self.pair_btn = QPushButton("Pair")
        self.pair_btn.clicked.connect(self._pair_dock)
        sync_btn_row1.addWidget(self.pair_btn)
        self.unpair_btn = QPushButton("Unpair")
        self.unpair_btn.clicked.connect(self._unpair_dock)
        sync_btn_row1.addWidget(self.unpair_btn)
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setObjectName("accentButton")
        self.refresh_btn.clicked.connect(self._refresh_status)
        sync_btn_row1.addWidget(self.refresh_btn)
        sl.addLayout(sync_btn_row1)

        sync_btn_row2 = QHBoxLayout()
        self.download_selected_btn = QPushButton("Download Selected")
        self.download_selected_btn.clicked.connect(self._download_selected)
        sync_btn_row2.addWidget(self.download_selected_btn)
        self.download_new_btn = QPushButton("Download New")
        self.download_new_btn.clicked.connect(self._download_new)
        sync_btn_row2.addWidget(self.download_new_btn)
        mark_btn = QPushButton("Mark Downloaded")
        mark_btn.clicked.connect(self._mark_downloaded)
        sync_btn_row2.addWidget(mark_btn)
        sl.addLayout(sync_btn_row2)

        # Folder info
        self.recordings_folder_label = QLabel("Recordings: Not set")
        self.recordings_folder_label.setObjectName("secondaryLabel")
        sl.addWidget(self.recordings_folder_label)
        self.transcript_folder_label = QLabel(f"Transcripts: {RAW_TRANSCRIPTS_DIR}")
        self.transcript_folder_label.setObjectName("secondaryLabel")
        sl.addWidget(self.transcript_folder_label)

        # Checkboxes
        checks_row = QHBoxLayout()
        self.auto_download_check = QCheckBox("Auto-download new")
        self.auto_download_check.stateChanged.connect(self._on_auto_download_changed)
        checks_row.addWidget(self.auto_download_check)
        self.hide_downloaded_check = QCheckBox("Hide downloaded")
        self.hide_downloaded_check.stateChanged.connect(self._on_hide_downloaded_changed)
        checks_row.addWidget(self.hide_downloaded_check)
        checks_row.addStretch()
        sl.addLayout(checks_row)

        cards_row.addWidget(sync_box, stretch=2)

        # ── Card 3: Transcription ───────────────────────────────────────
        trans_box = QGroupBox("Transcription")
        xl = QVBoxLayout(trans_box)
        xl.setContentsMargins(10, 10, 10, 10)
        xl.setSpacing(6)

        # Model status
        model_row = QHBoxLayout()
        self.model_status_dot = QLabel("\u25cf")
        model_row.addWidget(self.model_status_dot)
        self.model_status_label = QLabel("")
        model_row.addWidget(self.model_status_label)
        model_row.addStretch()
        xl.addLayout(model_row)

        self.transcribe_selected_btn = QPushButton("Transcribe Selected")
        self.transcribe_selected_btn.clicked.connect(self._transcribe_selected)
        xl.addWidget(self.transcribe_selected_btn)

        self.transcribe_all_btn = QPushButton("Transcribe All")
        self.transcribe_all_btn.clicked.connect(self._transcribe_all)
        xl.addWidget(self.transcribe_all_btn)

        self.download_model_btn = QPushButton("Download Model")
        self.download_model_btn.clicked.connect(self._download_model)
        xl.addWidget(self.download_model_btn)

        xl.addStretch()
        self._update_model_button_state()

        cards_row.addWidget(trans_box, stretch=1)

        root.addLayout(cards_row)

        # ── Recording table ─────────────────────────────────────────────
        self.table_model = RecordingTableModel()
        self.table_view = QTableView()
        self.table_view.setModel(self.table_model)
        self.table_view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table_view.setAlternatingRowColors(True)
        self.table_view.setSortingEnabled(True)
        self.table_view.horizontalHeader().setStretchLastSection(True)
        self.table_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table_view.customContextMenuRequested.connect(self._show_table_context_menu)
        self.table_view.doubleClicked.connect(self._on_row_double_click)

        header = self.table_view.horizontalHeader()
        widths = [100, 100, 85, 250, 150, 80, 80, 300]
        for i, w in enumerate(widths):
            if i < self.table_model.columnCount():
                header.resizeSection(i, w)

        root.addWidget(self.table_view, stretch=1)

        # ── Progress bar ────────────────────────────────────────────────
        progress_row = QHBoxLayout()
        progress_row.setSpacing(8)
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("secondaryLabel")
        self.progress_label.setMinimumWidth(200)
        progress_row.addWidget(self.progress_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(True)
        progress_row.addWidget(self.progress_bar, stretch=1)
        root.addLayout(progress_row)

        # Status bar
        self.statusBar().showMessage("Ready")

    # ── Context menu for table ──────────────────────────────────────────

    def _init_context_menu(self):
        pass  # created dynamically in _show_table_context_menu

    def _show_table_context_menu(self, pos):
        index = self.table_view.indexAt(pos)
        if not index.isValid():
            return
        entries = self.table_model.entries()
        if index.row() >= len(entries):
            return
        entry = entries[index.row()]
        rec = entry.recording

        menu = QMenu(self)

        if not rec.downloaded:
            dl_act = menu.addAction("Download")
            dl_act.triggered.connect(lambda: self._ctx_download(entry))
            mark_act = menu.addAction("Mark as Downloaded")
            mark_act.triggered.connect(lambda: self._ctx_mark_downloaded(entry))

        if rec.downloaded and rec.output_path and not rec.transcribed:
            trans_act = menu.addAction("Transcribe")
            trans_act.triggered.connect(lambda: self._ctx_transcribe(entry))

        if rec.output_path and os.path.exists(rec.output_path):
            open_loc_act = menu.addAction("Open File Location")
            open_loc_act.triggered.connect(lambda: self._open_file_location(rec.output_path))

        if rec.transcript_path and os.path.exists(rec.transcript_path):
            open_trans_act = menu.addAction("Open Transcript")
            open_trans_act.triggered.connect(lambda: self._open_file(rec.transcript_path))

        if menu.actions():
            menu.exec(self.table_view.viewport().mapToGlobal(pos))

    def _ctx_download(self, entry: SyncRecordingEntry):
        self._run_download(["download", entry.recording.name])

    def _ctx_mark_downloaded(self, entry: SyncRecordingEntry):
        try:
            run_extractor(["mark-downloaded", entry.recording.name])
            self._refresh_status()
        except Exception as e:
            self.statusBar().showMessage(f"Error: {e}", 5000)

    def _ctx_transcribe(self, entry: SyncRecordingEntry):
        if entry.recording.output_path:
            self._run_transcription([Path(entry.recording.output_path)])

    def _open_file_location(self, filepath: str):
        dirpath = os.path.dirname(filepath)
        if platform.system() == "Windows":
            os.startfile(dirpath)  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", dirpath])
        else:
            subprocess.Popen(["xdg-open", dirpath])

    def _open_file(self, filepath: str):
        if platform.system() == "Windows":
            os.startfile(filepath)  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", filepath])
        else:
            subprocess.Popen(["xdg-open", filepath])

    # ── Keyboard shortcuts ──────────────────────────────────────────────

    def _init_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(self._refresh_status)
        QShortcut(QKeySequence("F5"), self).activated.connect(self._refresh_status)
        QShortcut(QKeySequence("Ctrl+D"), self).activated.connect(self._download_selected)
        QShortcut(QKeySequence("Ctrl+T"), self).activated.connect(self._transcribe_selected)
        # Ctrl+S toggle trigger
        QShortcut(QKeySequence("Ctrl+Shift+S"), self).activated.connect(self._toggle_trigger)
        QShortcut(QKeySequence("Ctrl+A"), self).activated.connect(self._select_all_rows)

    def _toggle_trigger(self):
        if self.mic_trigger.is_running:
            self._stop_trigger()
        else:
            self._start_trigger()

    def _select_all_rows(self):
        if self.table_model.rowCount() > 0:
            self.table_view.selectAll()

    # ── Signal connections ──────────────────────────────────────────────

    def _connect_signals(self):
        self._log_signal.connect(self._append_log)
        self._sync_complete_signal.connect(self._on_sync_complete)
        self._transcription_status_signal.connect(self._on_transcription_status)
        self._progress_signal.connect(self._on_progress)

    # ── Settings ────────────────────────────────────────────────────────

    def _load_settings(self):
        mic = self.settings.value("triggerMicName", "")
        auto_start = self.settings.value("autoStartTrigger", False, type=bool)
        self.auto_start_check.setChecked(auto_start)

        rec_folder = self.settings.value("recordingsFolder", "")
        if rec_folder:
            self.recordings_folder_label.setText(f"Recordings: {rec_folder}")

        transcript_folder = self.settings.value("transcriptFolder", str(RAW_TRANSCRIPTS_DIR))
        if transcript_folder:
            self.transcript_folder_label.setText(f"Transcripts: {transcript_folder}")

        self.hide_downloaded_check.setChecked(
            self.settings.value("hideDownloaded", False, type=bool)
        )
        self.auto_download_check.setChecked(
            self.settings.value("autoDownload", False, type=bool)
        )

        if auto_start:
            QTimer.singleShot(500, self._start_trigger)

    def _restore_geometry(self):
        geom = self.settings.value("windowGeometry")
        if geom:
            self.restoreGeometry(geom)
        state = self.settings.value("windowState")
        if state:
            self.restoreState(state)

    def _save_geometry(self):
        self.settings.setValue("windowGeometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())

    def _refresh_mic_list(self):
        devices = list_audio_input_devices()
        self.mic_combo.clear()
        self.mic_combo.addItems(devices)
        saved = self.settings.value("triggerMicName", "")
        if saved and saved in devices:
            self.mic_combo.setCurrentText(saved)

    # ── Mic Trigger ─────────────────────────────────────────────────────

    @pyqtSlot()
    def _start_trigger(self):
        mic_name = self.mic_combo.currentText()
        if not mic_name:
            return
        self.mic_trigger.trigger_mic_name = mic_name
        self.mic_trigger.start()
        self._trigger_start_time = time.time()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.trigger_status_label.setText("Running")
        self.trigger_status_dot.setObjectName("statusDotRunning")
        self.trigger_status_dot.setStyle(self.trigger_status_dot.style())  # force re-style
        self.statusBar().showMessage("Mic trigger started")
        self._update_tray_tooltip()

    @pyqtSlot()
    def _stop_trigger(self):
        self.mic_trigger.stop()
        self._trigger_start_time = None
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.trigger_status_label.setText("Stopped")
        self.trigger_status_dot.setObjectName("statusDotStopped")
        self.trigger_status_dot.setStyle(self.trigger_status_dot.style())
        self.trigger_uptime_label.setText("")
        self.statusBar().showMessage("Mic trigger stopped")
        self._update_tray_tooltip()

    def _on_mic_state_change(self, holding: bool):
        # Called from trigger thread — use signal
        pass

    def _on_mic_log(self, msg: str):
        self._log_signal.emit(msg)

    def _on_mic_changed(self, name: str):
        self.settings.setValue("triggerMicName", name)

    def _on_auto_start_changed(self, state):
        self.settings.setValue("autoStartTrigger", state == Qt.CheckState.Checked.value)

    def _update_uptime(self):
        if self._trigger_start_time:
            elapsed = int(time.time() - self._trigger_start_time)
            h, m, s = elapsed // 3600, (elapsed % 3600) // 60, elapsed % 60
            if h > 0:
                self.trigger_uptime_label.setText(f"Uptime: {h}h {m:02d}m {s:02d}s")
            else:
                self.trigger_uptime_label.setText(f"Uptime: {m}m {s:02d}s")

    def _update_tray_tooltip(self):
        if self._tray_icon:
            parts = ["HiDock Tools"]
            if self.mic_trigger.is_running:
                parts.append("Trigger: Running")
            else:
                parts.append("Trigger: Stopped")
            total = len(self._entries)
            if total:
                downloaded = sum(1 for e in self._entries if e.recording.downloaded)
                parts.append(f"{total} recordings, {downloaded} downloaded")
            self._tray_icon.setToolTip(" | ".join(parts))

    # ── USB Sync ────────────────────────────────────────────────────────

    @pyqtSlot()
    def _refresh_status(self):
        ready, err = extractor_ready()
        if not ready:
            self.sync_status_label.setText(err)
            self.sync_status_dot.setObjectName("statusDotDisconnected")
            self.sync_status_dot.setStyle(self.sync_status_dot.style())
            self.statusBar().showMessage("Extractor not ready", 5000)
            return

        self.sync_status_label.setText("Refreshing...")
        self._sync_busy = True

        def _run():
            try:
                data = run_extractor(["status"], timeout=10)
                self._sync_complete_signal.emit(data, None)
            except Exception as e:
                self._sync_complete_signal.emit(None, str(e))

        threading.Thread(target=_run, daemon=True).start()

    @pyqtSlot(object, object)
    def _on_sync_complete(self, data, error):
        self._sync_busy = False
        # Route transcription-done signals to separate handler
        if data and isinstance(data, dict) and data.get("_transcription_done"):
            self._on_transcription_done(data, error)
            return
        if error:
            self.sync_status_label.setText(str(error))
            self.sync_status_dot.setObjectName("statusDotDisconnected")
            self.sync_status_dot.setStyle(self.sync_status_dot.style())
            self.statusBar().showMessage(f"Sync error: {error}", 5000)
            return

        if not data:
            self.sync_status_label.setText("No response")
            return

        connected = data.get("connected", False)
        recordings = data.get("recordings", [])
        output_dir = data.get("outputDir", "")

        if output_dir:
            self.recordings_folder_label.setText(f"Recordings: {output_dir}")

        status_text = f"{'Connected' if connected else 'Not connected'} \u2014 {len(recordings)} recordings"
        self.sync_status_label.setText(status_text)
        if connected:
            self.sync_status_dot.setObjectName("statusDotConnected")
        else:
            self.sync_status_dot.setObjectName("statusDotDisconnected")
        self.sync_status_dot.setStyle(self.sync_status_dot.style())

        entries = []
        for r in recordings:
            rec = SyncRecording.from_dict(r)
            entries.append(SyncRecordingEntry(recording=rec, device_name="HiDock"))

        self._entries = entries
        self._refresh_transcription_state()
        self._update_table()
        self._update_tray_tooltip()
        self.statusBar().showMessage(f"Loaded {len(entries)} recordings", 3000)

        # Auto-download if enabled
        if self.auto_download_check.isChecked():
            not_downloaded = [e for e in entries if not e.recording.downloaded]
            if not_downloaded:
                self._download_new()

    def _update_table(self):
        visible = self._entries
        if self.hide_downloaded_check.isChecked():
            visible = [e for e in visible if not e.recording.downloaded]
        self.table_model.set_entries(visible)
        self._update_summary()

    def _update_summary(self):
        total = len(self._entries)
        downloaded = sum(1 for e in self._entries if e.recording.downloaded)
        transcribed = sum(1 for e in self._entries if e.recording.transcribed)
        parts = [f"{total} rec"]
        if downloaded:
            parts.append(f"{downloaded} dl")
        if transcribed:
            parts.append(f"{transcribed} tx")
        self.summary_label.setText(" \u00b7 ".join(parts))

    def _refresh_transcription_state(self):
        try:
            status = get_transcription_status()
            for entry in self._entries:
                key = entry.recording.output_name or entry.recording.name
                if key in status:
                    entry.recording.transcribed = status[key].get("transcribed", False)
                    entry.recording.transcript_path = status[key].get("transcript_path")
        except Exception:
            pass

    @pyqtSlot()
    def _pair_dock(self):
        ready, err = extractor_ready()
        if not ready:
            self.statusBar().showMessage(f"Pair failed: {err}", 5000)
            return
        self._refresh_status()

    @pyqtSlot()
    def _unpair_dock(self):
        self._entries = []
        self._update_table()
        self.sync_status_label.setText("Unpaired")
        self.sync_status_dot.setObjectName("statusDotDisconnected")
        self.sync_status_dot.setStyle(self.sync_status_dot.style())

    @pyqtSlot()
    def _choose_recordings_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Choose Recordings Folder",
            self.settings.value("recordingsFolder", str(RECORDINGS_DIR)),
        )
        if folder:
            self.settings.setValue("recordingsFolder", folder)
            self.recordings_folder_label.setText(f"Recordings: {folder}")
            try:
                run_extractor(["set-output", folder])
                self._refresh_status()
            except Exception as e:
                self.statusBar().showMessage(f"Error setting folder: {e}", 5000)

    @pyqtSlot()
    def _choose_transcript_folder(self):
        current = self.settings.value("transcriptFolder", str(RAW_TRANSCRIPTS_DIR))
        folder = QFileDialog.getExistingDirectory(self, "Choose Transcript Folder", current)
        if folder:
            self.settings.setValue("transcriptFolder", folder)
            self.transcript_folder_label.setText(f"Transcripts: {folder}")

    @pyqtSlot()
    def _download_selected(self):
        indices = self.table_view.selectionModel().selectedRows()
        if not indices:
            self.statusBar().showMessage("No rows selected", 3000)
            return
        entries = self.table_model.entries()
        filenames = [entries[i.row()].recording.name for i in indices]
        self._run_download(["download"] + filenames)

    @pyqtSlot()
    def _download_new(self):
        self._run_download(["download-new"])

    def _run_download(self, args: list[str]):
        self.sync_status_label.setText("Downloading...")
        self._show_progress(0, 0, "Downloading...")

        def _run():
            try:
                data = run_extractor(args, timeout=300)
                self._sync_complete_signal.emit(data, None)
            except Exception as e:
                self._sync_complete_signal.emit(None, str(e))
            finally:
                self._progress_signal.emit(-1, -1, "")  # hide progress

        threading.Thread(target=_run, daemon=True).start()

    @pyqtSlot()
    def _mark_downloaded(self):
        indices = self.table_view.selectionModel().selectedRows()
        if not indices:
            self.statusBar().showMessage("No rows selected", 3000)
            return
        entries = self.table_model.entries()
        filenames = [entries[i.row()].recording.name for i in indices]
        try:
            run_extractor(["mark-downloaded"] + filenames)
            self._refresh_status()
        except Exception as e:
            self.statusBar().showMessage(f"Error: {e}", 5000)

    @pyqtSlot()
    def _transcribe_selected(self):
        indices = self.table_view.selectionModel().selectedRows()
        if not indices:
            self.statusBar().showMessage("No rows selected", 3000)
            return
        entries = self.table_model.entries()
        targets = []
        for i in indices:
            entry = entries[i.row()]
            if entry.recording.downloaded and entry.recording.output_path:
                targets.append(Path(entry.recording.output_path))
        if not targets:
            self.statusBar().showMessage("No downloaded recordings selected", 3000)
            return
        self._run_transcription(targets)

    @pyqtSlot()
    def _transcribe_all(self):
        targets = []
        for entry in self._entries:
            if (entry.recording.downloaded
                    and not entry.recording.transcribed
                    and entry.recording.output_path):
                targets.append(Path(entry.recording.output_path))
        if not targets:
            self.statusBar().showMessage("No untranscribed recordings to process", 3000)
            return
        self._run_transcription(targets)

    def _run_transcription(self, targets: list[Path]):
        """Transcribe a list of audio files in a background thread."""
        if not whisper_model_ready():
            QMessageBox.information(
                self, "Model Required",
                "The speech recognition model needs to be downloaded first.\n"
                "Click 'Download Model' to get started."
            )
            return
        from core.transcription import transcribe_file

        self.transcribe_selected_btn.setEnabled(False)
        self.transcribe_all_btn.setEnabled(False)
        self.statusBar().showMessage(f"Transcribing {len(targets)} file(s)...")

        def _worker():
            model = None
            results = []
            for i, mp3_path in enumerate(targets):
                try:
                    def _progress(pct, _i=i):
                        total_pct = int((_i * 100 + pct) / len(targets))
                        self._progress_signal.emit(total_pct, 100, f"Transcribing {_i+1}/{len(targets)}")
                        self._log_signal.emit(f"Transcribing {_i+1}/{len(targets)}: {total_pct}%")

                    result = transcribe_file(mp3_path, model=model, on_progress=_progress)
                    results.append(result)
                except Exception as e:
                    self._log_signal.emit(f"Error transcribing {mp3_path.name}: {e}")

            succeeded = sum(1 for r in results if r.get("transcribed"))
            self._sync_complete_signal.emit(
                {"_transcription_done": True, "succeeded": succeeded, "total": len(targets)},
                None,
            )

        threading.Thread(target=_worker, daemon=True).start()

    @pyqtSlot(object, object)
    def _on_transcription_done(self, data, error):
        """Handle transcription batch completion (routed through _on_sync_complete)."""
        self.transcribe_selected_btn.setEnabled(True)
        self.transcribe_all_btn.setEnabled(True)
        succeeded = data.get("succeeded", 0)
        total = data.get("total", 0)
        self.statusBar().showMessage(f"Transcribed {succeeded}/{total} files", 5000)
        self._hide_progress()
        self._refresh_transcription_state()
        self._update_table()
        # Tray notification
        if self._tray_icon:
            self._tray_icon.showMessage(
                "Transcription Complete",
                f"Transcribed {succeeded}/{total} files",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )

    def _on_hide_downloaded_changed(self, state):
        self.settings.setValue("hideDownloaded", state == Qt.CheckState.Checked.value)
        self._update_table()

    def _on_auto_download_changed(self, state):
        self.settings.setValue("autoDownload", state == Qt.CheckState.Checked.value)

    def _on_row_double_click(self, index):
        entries = self.table_model.entries()
        if index.row() < len(entries):
            entry = entries[index.row()]
            path = entry.recording.output_path
            if path and os.path.exists(path):
                self._open_file_location(path)

    @pyqtSlot(str)
    def _append_log(self, msg: str):
        print(f"[HiDock] {msg}")

    @pyqtSlot(dict)
    def _on_transcription_status(self, status: dict):
        for entry in self._entries:
            key = entry.recording.output_name or entry.recording.name
            if key in status:
                entry.recording.transcribed = status[key].get("transcribed", False)
                entry.recording.transcript_path = status[key].get("transcript_path")
        self._update_table()

    # ── Progress bar ────────────────────────────────────────────────────

    @pyqtSlot(int, int, str)
    def _on_progress(self, value: int, maximum: int, label: str):
        if value < 0:
            self._hide_progress()
            return
        self._show_progress(value, maximum, label)

    def _show_progress(self, value: int, maximum: int, label: str):
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(maximum)
        self.progress_bar.setValue(value)
        self.progress_label.setText(label)

    def _hide_progress(self):
        self.progress_bar.setVisible(False)
        self.progress_bar.setValue(0)
        self.progress_label.setText("")

    # ── Model download ──────────────────────────────────────────────────

    def _update_model_button_state(self):
        """Update Download Model button based on whether model exists."""
        if whisper_model_ready():
            self.download_model_btn.setText("Model Ready")
            self.download_model_btn.setEnabled(False)
            self.download_model_btn.setToolTip("Whisper model is downloaded and ready")
            self.download_model_btn.setObjectName("successButton")
            self.download_model_btn.setStyle(self.download_model_btn.style())
            self.model_status_dot.setText("\u25cf")
            self.model_status_dot.setStyleSheet("color: #a6e3a1; font-size: 14px;")
            self.model_status_label.setText("Model ready")
            self.transcribe_selected_btn.setEnabled(True)
            self.transcribe_all_btn.setEnabled(True)
        else:
            self.download_model_btn.setText("Download Model (~550 MB)")
            self.download_model_btn.setEnabled(True)
            self.download_model_btn.setToolTip("Download the speech recognition model")
            self.download_model_btn.setObjectName("")
            self.download_model_btn.setStyle(self.download_model_btn.style())
            self.model_status_dot.setText("\u25cf")
            self.model_status_dot.setStyleSheet("color: #f9e2af; font-size: 14px;")
            self.model_status_label.setText("Model not downloaded")
            self.transcribe_selected_btn.setEnabled(False)
            self.transcribe_all_btn.setEnabled(False)

    @pyqtSlot()
    def _download_model(self):
        """Download the Whisper model in a background thread with progress."""
        from core.model_download import download_model

        self.download_model_btn.setEnabled(False)
        self.download_model_btn.setText("Downloading... 0%")
        self.statusBar().showMessage("Downloading speech recognition model...")

        # Connect signals
        self._model_download_signal.connect(self._on_model_progress)
        self._model_download_done_signal.connect(self._on_model_done)
        self._model_download_error_signal.connect(self._on_model_error)

        def _worker():
            download_model(
                on_progress=lambda dl, total: self._model_download_signal.emit(dl, total),
                on_complete=lambda: self._model_download_done_signal.emit(),
                on_error=lambda msg: self._model_download_error_signal.emit(msg),
            )

        threading.Thread(target=_worker, daemon=True).start()

    @pyqtSlot(int, int)
    def _on_model_progress(self, downloaded: int, total: int):
        if total > 0:
            pct = int(downloaded * 100 / total)
            mb_dl = downloaded / (1024 * 1024)
            mb_total = total / (1024 * 1024)
            self.download_model_btn.setText(f"Downloading... {pct}%")
            self._show_progress(pct, 100, f"Model: {mb_dl:.0f}/{mb_total:.0f} MB")
            self.statusBar().showMessage(f"Downloading model: {pct}% ({mb_dl:.0f}/{mb_total:.0f} MB)")

    @pyqtSlot()
    def _on_model_done(self):
        self._update_model_button_state()
        self._hide_progress()
        self.statusBar().showMessage("Model downloaded - ready to transcribe", 5000)
        self._model_download_signal.disconnect(self._on_model_progress)
        self._model_download_done_signal.disconnect(self._on_model_done)
        self._model_download_error_signal.disconnect(self._on_model_error)
        if self._tray_icon:
            self._tray_icon.showMessage(
                "Model Downloaded",
                "Speech recognition model is ready",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )

    @pyqtSlot(str)
    def _on_model_error(self, msg: str):
        self.download_model_btn.setText("Download Model (~550 MB)")
        self.download_model_btn.setEnabled(True)
        self._hide_progress()
        self.statusBar().showMessage("Model download failed", 5000)
        QMessageBox.warning(self, "Download Failed", f"Failed to download model:\n{msg}")
        self._model_download_signal.disconnect(self._on_model_progress)
        self._model_download_done_signal.disconnect(self._on_model_done)
        self._model_download_error_signal.disconnect(self._on_model_error)

    # ── USB auto-refresh ────────────────────────────────────────────────

    def _usb_auto_check(self):
        """Periodically check if extractor becomes ready and auto-refresh."""
        ready, _ = extractor_ready()
        if ready and not self._last_extractor_ready and not self._sync_busy:
            self._refresh_status()
        self._last_extractor_ready = ready

    # ── Window events ───────────────────────────────────────────────────

    def closeEvent(self, event):
        """Minimize to tray on close, unless force-quitting."""
        self._save_geometry()
        if self._force_quit or self._tray_icon is None:
            self.mic_trigger.stop()
            event.accept()
            super().closeEvent(event)
        else:
            event.ignore()
            self.hide()
            if self._tray_icon:
                self._tray_icon.showMessage(
                    "HiDock Tools",
                    "Minimized to system tray. Right-click the icon to quit.",
                    QSystemTrayIcon.MessageIcon.Information,
                    2000,
                )

    def _quit_app(self):
        self._force_quit = True
        self.mic_trigger.stop()
        if self._tray_icon:
            self._tray_icon.hide()
        QApplication.instance().quit()

    def _show_about(self):
        QMessageBox.about(
            self,
            "About HiDock Tools",
            "HiDock Tools\n\n"
            "USB sync, mic trigger, and transcription for HiDock.\n\n"
            "Python/PyQt6 port of the macOS app."
        )
