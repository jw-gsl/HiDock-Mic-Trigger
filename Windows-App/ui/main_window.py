"""Main window — mirrors the macOS hidock-mic-trigger desktop app.

Layout (top to bottom):
  1. Menu bar
  2. Mic Trigger strip (status + start/stop + mic dropdown + auto-start)
  3. Status + folder paths + download buttons row
  4. Device buttons + transcribe + model row
  5. Select / filter / options row
  6. Recording table
  7. Progress bar
  8. Footer (update status + check updates + feedback buttons)
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote

from PyQt6.QtCore import QSettings, QTimer, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
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
        self._transcribe_after_download = False
        self._trigger_start_time: float | None = None
        self._last_transcript_path: str | None = None
        self._paired_devices: list = []  # list[PairedDevice] loaded lazily

        self._init_menu_bar()
        self._init_ui()
        self._init_context_menu()
        self._init_shortcuts()
        self._connect_signals()
        self._load_settings()
        self._refresh_mic_list()
        self._restore_geometry()

        # First-run onboarding wizard
        if not self.settings.value("hasCompletedOnboarding", False, type=bool):
            from ui.onboarding_dialog import OnboardingDialog
            dlg = OnboardingDialog(self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self.settings.setValue("hasCompletedOnboarding", True)
                if dlg.selected_mic:
                    self.mic_combo.setCurrentText(dlg.selected_mic)
                    self.settings.setValue("triggerMicName", dlg.selected_mic)

        # Uptime timer
        self._uptime_timer = QTimer(self)
        self._uptime_timer.timeout.connect(self._update_uptime)
        self._uptime_timer.start(1000)

        # USB auto-refresh timer (10 seconds)
        self._usb_check_timer = QTimer(self)
        self._usb_check_timer.timeout.connect(self._usb_auto_check)
        self._usb_check_timer.start(10_000)
        self._last_extractor_ready = False

        # Check for updates after 5 seconds
        QTimer.singleShot(5000, self._check_for_updates_auto)

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
        actions_menu.addSeparator()
        voice_lib_act = actions_menu.addAction("Voice Library...")
        voice_lib_act.triggered.connect(self._show_voice_library)
        model_mgr_act = actions_menu.addAction("Models...")
        model_mgr_act.triggered.connect(self._show_model_manager)
        device_mgr_act = actions_menu.addAction("Devices...")
        device_mgr_act.triggered.connect(self._show_device_manager)

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
        feedback_act = help_menu.addAction("Send Feedback...")
        feedback_act.triggered.connect(self._send_feedback)
        history_act = help_menu.addAction("My Feedback")
        history_act.triggered.connect(self._show_feedback_history)
        help_menu.addSeparator()

        # Notifications submenu
        notif_menu = help_menu.addMenu("Notifications")
        self._notif_transcription_act = QAction("Transcription Complete", self, checkable=True)
        self._notif_transcription_act.setChecked(
            self.settings.value("notifyTranscription", True, type=bool)
        )
        self._notif_transcription_act.triggered.connect(
            lambda checked: self.settings.setValue("notifyTranscription", checked)
        )
        notif_menu.addAction(self._notif_transcription_act)

        self._notif_download_act = QAction("Download Complete", self, checkable=True)
        self._notif_download_act.setChecked(
            self.settings.value("notifyDownload", True, type=bool)
        )
        self._notif_download_act.triggered.connect(
            lambda checked: self.settings.setValue("notifyDownload", checked)
        )
        notif_menu.addAction(self._notif_download_act)

        self._notif_mic_act = QAction("Mic Changes", self, checkable=True)
        self._notif_mic_act.setChecked(
            self.settings.value("notifyMicChanges", True, type=bool)
        )
        self._notif_mic_act.triggered.connect(
            lambda checked: self.settings.setValue("notifyMicChanges", checked)
        )
        notif_menu.addAction(self._notif_mic_act)

        help_menu.addSeparator()

        # Appearance submenu
        appearance_menu = help_menu.addMenu("Appearance")
        appearance_group = QActionGroup(self)
        appearance_group.setExclusive(True)

        current_theme = self.settings.value("theme", "auto")

        dark_act = QAction("Dark", self, checkable=True)
        dark_act.setChecked(current_theme == "dark")
        dark_act.triggered.connect(lambda: self._set_theme("dark"))
        appearance_group.addAction(dark_act)
        appearance_menu.addAction(dark_act)

        light_act = QAction("Light", self, checkable=True)
        light_act.setChecked(current_theme == "light")
        light_act.triggered.connect(lambda: self._set_theme("light"))
        appearance_group.addAction(light_act)
        appearance_menu.addAction(light_act)

        auto_act = QAction("Auto (System)", self, checkable=True)
        auto_act.setChecked(current_theme not in ("dark", "light"))
        auto_act.triggered.connect(lambda: self._set_theme("auto"))
        appearance_group.addAction(auto_act)
        appearance_menu.addAction(auto_act)

        help_menu.addSeparator()
        update_act = help_menu.addAction("Check for Updates...")
        update_act.triggered.connect(self._check_for_updates_manual)

    # ── UI Layout ───────────────────────────────────────────────────────────

    def _init_ui(self):
        self.setWindowTitle("HiDock")
        self.setMinimumSize(1000, 620)
        self.resize(1140, 700)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 10, 14, 6)
        root.setSpacing(6)

        # ── Row 1: Mic Trigger strip ────────────────────────────────────
        mic_strip = QHBoxLayout()
        mic_strip.setSpacing(8)

        self.trigger_status_dot = QLabel("\u25cf")
        self.trigger_status_dot.setObjectName("statusDotStopped")
        self.trigger_status_dot.setFixedWidth(16)
        mic_strip.addWidget(self.trigger_status_dot)
        self.trigger_status_label = QLabel("Stopped")
        mic_strip.addWidget(self.trigger_status_label)
        self.trigger_uptime_label = QLabel("")
        self.trigger_uptime_label.setObjectName("secondaryLabel")
        mic_strip.addWidget(self.trigger_uptime_label)

        mic_strip.addSpacing(12)

        self.start_btn = QPushButton("Start")
        self.start_btn.setObjectName("successButton")
        self.start_btn.clicked.connect(self._start_trigger)
        mic_strip.addWidget(self.start_btn)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("dangerButton")
        self.stop_btn.clicked.connect(self._stop_trigger)
        self.stop_btn.setEnabled(False)
        mic_strip.addWidget(self.stop_btn)

        mic_strip.addSpacing(12)

        mic_strip.addWidget(QLabel("Mic:"))
        self.mic_combo = QComboBox()
        self.mic_combo.setMinimumWidth(180)
        self.mic_combo.currentTextChanged.connect(self._on_mic_changed)
        mic_strip.addWidget(self.mic_combo)

        mic_strip.addSpacing(12)

        self.auto_start_check = QCheckBox("Auto-start")
        self.auto_start_check.stateChanged.connect(self._on_auto_start_changed)
        mic_strip.addWidget(self.auto_start_check)

        mic_strip.addStretch()
        root.addLayout(mic_strip)

        # ── Row 2: Status + Paths + Downloads ───────────────────────────
        row2 = QHBoxLayout()
        row2.setSpacing(8)

        # Left: status dot + connection text
        self.sync_status_dot = QLabel("\u25cf")
        self.sync_status_dot.setObjectName("statusDotDisconnected")
        self.sync_status_dot.setFixedWidth(16)
        row2.addWidget(self.sync_status_dot)
        self.sync_status_label = QLabel("Not loaded")
        row2.addWidget(self.sync_status_label)

        row2.addSpacing(8)

        # Folder path labels
        self.recordings_folder_label = QLabel("Recordings: Not set")
        self.recordings_folder_label.setObjectName("secondaryLabel")
        row2.addWidget(self.recordings_folder_label)

        row2.addSpacing(6)

        self.transcript_folder_label = QLabel(f"Transcripts: {RAW_TRANSCRIPTS_DIR}")
        self.transcript_folder_label.setObjectName("secondaryLabel")
        row2.addWidget(self.transcript_folder_label)

        row2.addStretch()

        # Right: summary + download buttons
        self.summary_label = QLabel("")
        self.summary_label.setObjectName("secondaryLabel")
        row2.addWidget(self.summary_label)

        row2.addSpacing(8)

        self.download_selected_btn = QPushButton("Download Selected")
        self.download_selected_btn.clicked.connect(self._download_selected)
        row2.addWidget(self.download_selected_btn)
        self.download_new_btn = QPushButton("Download New")
        self.download_new_btn.clicked.connect(self._download_new)
        row2.addWidget(self.download_new_btn)
        mark_btn = QPushButton("Mark Done")
        mark_btn.clicked.connect(self._mark_downloaded)
        row2.addWidget(mark_btn)

        root.addLayout(row2)

        # ── Row 3: Device buttons + Transcribe ──────────────────────────
        row3 = QHBoxLayout()
        row3.setSpacing(8)

        # Left: device management
        self.pair_btn = QPushButton("Pair")
        self.pair_btn.clicked.connect(self._pair_dock)
        row3.addWidget(self.pair_btn)
        self.unpair_btn = QPushButton("Unpair")
        self.unpair_btn.clicked.connect(self._unpair_dock)
        row3.addWidget(self.unpair_btn)

        rec_folder_btn = QPushButton("Recordings Folder")
        rec_folder_btn.clicked.connect(self._choose_recordings_folder)
        row3.addWidget(rec_folder_btn)
        trans_folder_btn = QPushButton("Transcripts Folder")
        trans_folder_btn.clicked.connect(self._choose_transcript_folder)
        row3.addWidget(trans_folder_btn)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setObjectName("accentButton")
        self.refresh_btn.clicked.connect(self._refresh_status)
        row3.addWidget(self.refresh_btn)

        row3.addStretch()

        # Right: transcribe + model
        self.transcribe_selected_btn = QPushButton("Transcribe Selected")
        self.transcribe_selected_btn.clicked.connect(self._transcribe_selected)
        row3.addWidget(self.transcribe_selected_btn)
        self.transcribe_all_btn = QPushButton("Transcribe All")
        self.transcribe_all_btn.clicked.connect(self._transcribe_all)
        row3.addWidget(self.transcribe_all_btn)

        self.diarize_check = QCheckBox("Speaker Labels")
        self.diarize_check.setToolTip("Enable speaker diarization (identifies who is speaking)")
        self.diarize_check.stateChanged.connect(self._on_diarize_changed)
        row3.addWidget(self.diarize_check)

        row3.addSpacing(8)

        self.download_model_btn = QPushButton("Download Model")
        self.download_model_btn.clicked.connect(self._download_model)
        row3.addWidget(self.download_model_btn)
        self.model_status_dot = QLabel("\u25cf")
        row3.addWidget(self.model_status_dot)
        self.model_status_label = QLabel("")
        row3.addWidget(self.model_status_label)

        self._update_model_button_state()

        root.addLayout(row3)

        # ── Row 4: Select / Filter / Options ────────────────────────────
        row4 = QHBoxLayout()
        row4.setSpacing(8)

        select_all_btn = QPushButton("Select All")
        select_all_btn.clicked.connect(self._select_all_rows)
        row4.addWidget(select_all_btn)
        select_none_btn = QPushButton("Select None")
        select_none_btn.clicked.connect(lambda: self.table_view.clearSelection())
        row4.addWidget(select_none_btn)
        select_new_btn = QPushButton("Select New")
        select_new_btn.clicked.connect(self._select_new_rows)
        row4.addWidget(select_new_btn)

        row4.addStretch()

        # Right: hide downloaded + auto-download
        self.hide_downloaded_check = QCheckBox("Hide Downloaded")
        self.hide_downloaded_check.stateChanged.connect(self._on_hide_downloaded_changed)
        row4.addWidget(self.hide_downloaded_check)
        self.auto_download_check = QCheckBox("Auto-download")
        self.auto_download_check.stateChanged.connect(self._on_auto_download_changed)
        row4.addWidget(self.auto_download_check)

        root.addLayout(row4)

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
        self.cancel_transcription_btn = QPushButton("Cancel")
        self.cancel_transcription_btn.setObjectName("dangerButton")
        self.cancel_transcription_btn.setVisible(False)
        self.cancel_transcription_btn.clicked.connect(self._cancel_transcription)
        progress_row.addWidget(self.cancel_transcription_btn)
        root.addLayout(progress_row)

        # ── Footer row ──────────────────────────────────────────────────
        footer_row = QHBoxLayout()
        footer_row.setSpacing(8)

        self.update_status_label = QLabel("")
        self.update_status_label.setObjectName("secondaryLabel")
        footer_row.addWidget(self.update_status_label)

        footer_row.addStretch()

        cowork_btn = QPushButton("\u2728 Cowork")
        cowork_btn.clicked.connect(self._show_cowork_prompt)
        footer_row.addWidget(cowork_btn)
        models_btn = QPushButton("Models")
        models_btn.clicked.connect(self._show_model_manager)
        footer_row.addWidget(models_btn)
        voice_lib_btn = QPushButton("Voice Library")
        voice_lib_btn.clicked.connect(self._show_voice_library)
        footer_row.addWidget(voice_lib_btn)
        check_updates_btn = QPushButton("Check for Updates")
        check_updates_btn.clicked.connect(self._check_for_updates_manual)
        footer_row.addWidget(check_updates_btn)
        my_feedback_btn = QPushButton("My Feedback")
        my_feedback_btn.clicked.connect(self._show_feedback_history)
        footer_row.addWidget(my_feedback_btn)
        send_feedback_btn = QPushButton("Send Feedback")
        send_feedback_btn.clicked.connect(self._send_feedback)
        footer_row.addWidget(send_feedback_btn)

        root.addLayout(footer_row)

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
            # Check for diarized JSON to open transcript viewer
            diarized_path = self._diarized_json_path(rec.transcript_path)
            if diarized_path and os.path.exists(diarized_path):
                view_trans_act = menu.addAction("View Transcript (Speaker View)")
                view_trans_act.triggered.connect(
                    lambda: self._open_transcript_viewer(diarized_path, rec.output_path or "")
                )
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

    def _select_new_rows(self):
        """Select rows that have not been downloaded yet."""
        sel = self.table_view.selectionModel()
        sel.clearSelection()
        entries = self.table_model.entries()
        for i, entry in enumerate(entries):
            if not entry.recording.downloaded:
                idx = self.table_model.index(i, 0)
                sel.select(idx, sel.SelectionFlag.Select | sel.SelectionFlag.Rows)

    # ── Signal connections ──────────────────────────────────────────────

    def _connect_signals(self):
        self._log_signal.connect(self._append_log)
        self._sync_complete_signal.connect(self._on_sync_complete)
        self._transcription_status_signal.connect(self._on_transcription_status)
        self._progress_signal.connect(self._on_progress)

    # ── Settings ────────────────────────────────────────────────────────

    def _load_settings(self):
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
        self.diarize_check.setChecked(
            self.settings.value("diarizeEnabled", False, type=bool)
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

    def _set_theme(self, theme: str):
        """Save the chosen theme and inform the user a restart is needed."""
        self.settings.setValue("theme", theme)
        QMessageBox.information(
            self,
            "Theme Changed",
            f"Theme set to '{theme}'. Please restart the application for the change to take effect.",
        )

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
            from core.models import DeviceType, load_paired_devices
            devices = load_paired_devices(self.settings)
            all_recordings = []
            any_connected = False
            output_dir = ""
            errors = []

            # If no paired devices, fall back to default HiDock status
            if not devices:
                try:
                    data = run_extractor(["status"], timeout=10)
                    self._sync_complete_signal.emit(data, None)
                except Exception as e:
                    self._sync_complete_signal.emit(None, str(e))
                return

            for device in devices:
                try:
                    if device.device_type == DeviceType.VOLUME:
                        args = ["volume-status", "--volume-name", device.volume_name or ""]
                        if device.subpath:
                            args += ["--subpath", device.subpath]
                        data = run_extractor(args, timeout=10)
                    else:
                        data = run_extractor(["status"], product_id=device.product_id, timeout=10)

                    if data.get("connected"):
                        any_connected = True
                    if data.get("outputDir") and not output_dir:
                        output_dir = data["outputDir"]
                    for r in data.get("recordings", []):
                        r["_device_id"] = device.device_id
                        r["_device_name"] = device.display_name
                        r["_device_product_id"] = device.product_id
                        all_recordings.append(r)
                except Exception as e:
                    errors.append(f"{device.display_name}: {e}")

            merged = {
                "connected": any_connected,
                "outputDir": output_dir,
                "recordings": all_recordings,
                "_multi_device": True,
            }
            if errors and not any_connected:
                merged["error"] = "; ".join(errors)
            self._sync_complete_signal.emit(merged, None)

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
            entries.append(SyncRecordingEntry(
                recording=rec,
                device_product_id=r.get("_device_product_id", 0),
                device_id=r.get("_device_id", ""),
                device_name=r.get("_device_name", "HiDock"),
            ))

        self._entries = entries
        self._refresh_transcription_state()
        self._update_table()
        self._update_tray_tooltip()
        self.statusBar().showMessage(f"Loaded {len(entries)} recordings", 3000)

        # Auto-transcribe after download-for-transcription
        if self._transcribe_after_download:
            self._transcribe_after_download = False
            targets = [
                Path(e.recording.output_path)
                for e in entries
                if e.recording.downloaded and e.recording.output_path and not e.recording.transcribed
            ]
            if targets:
                self._run_transcription(targets)

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
        visible = self.table_model.entries()
        selected = [visible[i.row()] for i in indices]

        # Group by device for proper command routing
        from core.models import DeviceType, load_paired_devices
        devices = {d.device_id: d for d in load_paired_devices(self.settings)}

        # Build per-device download commands
        commands: list[tuple[list[str], int | None]] = []
        for entry in selected:
            device = devices.get(entry.device_id)
            if device and device.device_type == DeviceType.VOLUME:
                args = ["volume-import", entry.recording.name, "--volume-name", device.volume_name or ""]
                if device.subpath:
                    args += ["--subpath", device.subpath]
                commands.append((args, None))
            else:
                commands.append((["download", entry.recording.name, "--length", str(entry.recording.length)], entry.device_product_id or None))

        self._run_download_commands(commands)

    @pyqtSlot()
    def _download_new(self):
        from core.models import DeviceType, load_paired_devices
        devices = load_paired_devices(self.settings)

        if not devices:
            self._run_download(["download-new"])
            return

        commands: list[tuple[list[str], int | None]] = []
        for device in devices:
            if device.device_type == DeviceType.VOLUME:
                args = ["volume-import-new", "--volume-name", device.volume_name or ""]
                if device.subpath:
                    args += ["--subpath", device.subpath]
                commands.append((args, None))
            else:
                commands.append((["download-new"], device.product_id))
        self._run_download_commands(commands)

    def _run_download(self, args: list[str], product_id: int | None = None):
        self._run_download_commands([(args, product_id)])

    def _run_download_commands(self, commands: list[tuple[list[str], int | None]]):
        """Run multiple extractor download commands sequentially in a background thread."""
        self.sync_status_label.setText("Downloading...")
        self._show_progress(0, 0, "Downloading...")

        def _run():
            last_data = None
            last_error = None
            for args, pid in commands:
                try:
                    last_data = run_extractor(args, product_id=pid, timeout=300)
                except Exception as e:
                    last_error = str(e)
            if last_error and last_data is None:
                self._sync_complete_signal.emit(None, last_error)
            else:
                self._sync_complete_signal.emit(last_data or {}, None)
            self._progress_signal.emit(-1, -1, "")  # hide progress

        threading.Thread(target=_run, daemon=True).start()

    @pyqtSlot()
    def _mark_downloaded(self):
        indices = self.table_view.selectionModel().selectedRows()
        if not indices:
            self.statusBar().showMessage("No rows selected", 3000)
            return
        visible = self.table_model.entries()
        selected = [visible[i.row()] for i in indices]

        # Group by device_id
        from core.models import DeviceType, load_paired_devices
        devices = {d.device_id: d for d in load_paired_devices(self.settings)}
        by_device: dict[str, list[SyncRecordingEntry]] = {}
        for entry in selected:
            by_device.setdefault(entry.device_id, []).append(entry)

        try:
            for device_id, device_entries in by_device.items():
                filenames = [e.recording.name for e in device_entries]
                device = devices.get(device_id)
                if device and device.device_type == DeviceType.VOLUME:
                    run_extractor(["mark-downloaded", "--volume-name", device.volume_name or ""] + filenames)
                else:
                    pid = device.product_id if device else None
                    run_extractor(["mark-downloaded"] + filenames, product_id=pid)
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
        ready = []
        needs_download = []
        for i in indices:
            entry = entries[i.row()]
            if entry.recording.downloaded and entry.recording.output_path:
                ready.append(Path(entry.recording.output_path))
            elif not entry.recording.downloaded:
                needs_download.append(entry.recording.name)

        if not ready and not needs_download:
            self.statusBar().showMessage("No recordings selected", 3000)
            return

        if needs_download:
            self._transcribe_after_download = True
            self.statusBar().showMessage(
                f"Downloading {len(needs_download)} recording(s) before transcription..."
            )
            self._run_download(["download"] + needs_download)
        elif ready:
            self._run_transcription(ready)

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

        self._transcription_cancelled = False
        self.transcribe_selected_btn.setEnabled(False)
        self.transcribe_all_btn.setEnabled(False)
        self.cancel_transcription_btn.setVisible(True)
        self.statusBar().showMessage(f"Transcribing {len(targets)} file(s)...")

        diarize = self.diarize_check.isChecked()

        def _worker():
            model = None
            results = []
            for i, mp3_path in enumerate(targets):
                if self._transcription_cancelled:
                    break
                try:
                    def _progress(pct, _i=i):
                        total_pct = int((_i * 100 + pct) / len(targets))
                        self._progress_signal.emit(
                            total_pct, 100,
                            f"Transcribing {_i+1}/{len(targets)} — {total_pct}%"
                        )

                    result = transcribe_file(
                        mp3_path, model=model, on_progress=_progress,
                        diarize=diarize,
                    )
                    results.append(result)
                except Exception as e:
                    self._log_signal.emit(f"Error transcribing {mp3_path.name}: {e}")

            succeeded = sum(1 for r in results if r.get("transcribed"))
            transcript_paths = [r["transcript_path"] for r in results if r.get("transcribed") and r.get("transcript_path")]
            self._sync_complete_signal.emit(
                {
                    "_transcription_done": True,
                    "succeeded": succeeded,
                    "total": len(targets),
                    "transcript_paths": transcript_paths,
                },
                None,
            )

        threading.Thread(target=_worker, daemon=True).start()

    def _cancel_transcription(self):
        """Cancel the current transcription batch."""
        self._transcription_cancelled = True
        self.cancel_transcription_btn.setVisible(False)
        self.statusBar().showMessage("Transcription cancelled", 5000)
        self._hide_progress()

    @pyqtSlot(object, object)
    def _on_transcription_done(self, data, error):
        """Handle transcription batch completion (routed through _on_sync_complete)."""
        self.transcribe_selected_btn.setEnabled(True)
        self.transcribe_all_btn.setEnabled(True)
        self.cancel_transcription_btn.setVisible(False)
        succeeded = data.get("succeeded", 0)
        total = data.get("total", 0)
        transcript_paths = data.get("transcript_paths", [])
        self.statusBar().showMessage(f"Transcribed {succeeded}/{total} files", 5000)
        self._hide_progress()
        self._refresh_transcription_state()
        self._update_table()

        # Store last transcript path for click-to-open from tray notification
        if transcript_paths:
            self._last_transcript_path = transcript_paths[-1]
        elif str(RAW_TRANSCRIPTS_DIR) and RAW_TRANSCRIPTS_DIR.exists():
            self._last_transcript_path = str(RAW_TRANSCRIPTS_DIR)

        # Tray notification (respects user preference)
        if self._tray_icon and self.settings.value("notifyTranscription", True, type=bool):
            body = f"Transcribed {succeeded}/{total} files"
            if succeeded == 1 and transcript_paths:
                body += "\nClick to open transcript"
            elif succeeded > 1:
                body += "\nClick to open transcript folder"
            self._tray_icon.showMessage(
                "Transcription Complete",
                body,
                QSystemTrayIcon.MessageIcon.Information,
                5000,
            )

    def _on_tray_notification_clicked(self):
        """Handle click on tray notification — opens the last completed transcript."""
        path = self._last_transcript_path
        if not path:
            return
        if os.path.isfile(path) or os.path.isdir(path):
            if platform.system() == "Windows":
                os.startfile(path)
            else:
                subprocess.Popen(["xdg-open", path])
        self._last_transcript_path = None

    def _on_hide_downloaded_changed(self, state):
        self.settings.setValue("hideDownloaded", state == Qt.CheckState.Checked.value)
        self._update_table()

    def _on_auto_download_changed(self, state):
        self.settings.setValue("autoDownload", state == Qt.CheckState.Checked.value)

    def _on_diarize_changed(self, state):
        self.settings.setValue("diarizeEnabled", state == Qt.CheckState.Checked.value)

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
                entry.recording.speakers_tagged = status[key].get("speakers_tagged", False)
                entry.recording.summary_path = status[key].get("summary_path")
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

    # ── Update Checker ─────────────────────────────────────────────────

    def _check_for_updates_auto(self):
        """Auto-check on startup — only show dialog if update available."""
        def _worker():
            from core.update_checker import check_for_update
            return check_for_update()

        def _done(release):
            if release:
                self._show_update_dialog(release)

        def _run():
            release = _worker()
            if release:
                from PyQt6.QtCore import QTimer
                # Must show dialog on main thread
                self._pending_release = release
                QTimer.singleShot(0, lambda: self._show_update_dialog(self._pending_release))

        self._pending_release = None
        threading.Thread(target=_run, daemon=True).start()

    def _check_for_updates_manual(self):
        """Manual check — always show a result."""
        self.statusBar().showMessage("Checking for updates...")

        def _run():
            from core.update_checker import check_for_update
            release = check_for_update()
            self._pending_release = release
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, self._show_manual_update_result)

        threading.Thread(target=_run, daemon=True).start()

    def _show_manual_update_result(self):
        if self._pending_release:
            self._show_update_dialog(self._pending_release)
        else:
            self.statusBar().showMessage("You're up to date!", 3000)
            QMessageBox.information(self, "Up to Date", "HiDock 1.0.0 is the latest version.")

    def _show_update_dialog(self, release):
        if not release:
            return
        version = release.get("tag_name", "").lstrip("v")
        name = release.get("name", version)

        msg = QMessageBox(self)
        msg.setWindowTitle("Update Available")
        msg.setText(f"Version {version} is available (you have 1.0.0).")
        msg.setInformativeText(f"{name}")
        msg.setIcon(QMessageBox.Icon.Information)

        restart_btn = msg.addButton("Restart && Update", QMessageBox.ButtonRole.AcceptRole)
        quit_btn = msg.addButton("Update on Quit", QMessageBox.ButtonRole.ActionRole)
        msg.addButton("Skip this version", QMessageBox.ButtonRole.RejectRole)

        msg.exec()
        clicked = msg.clickedButton()

        if clicked == restart_btn:
            self._download_and_install(release, restart=True)
        elif clicked == quit_btn:
            self._download_and_install(release, restart=False)

    def _download_and_install(self, release, restart: bool):
        from core.update_checker import find_windows_asset, download_update, install_and_restart, install_on_quit

        asset = find_windows_asset(release)
        if not asset:
            QMessageBox.warning(self, "Update Failed", "No Windows download found in this release.")
            return

        asset_name, download_url = asset
        self.statusBar().showMessage(f"Downloading {asset_name}...")

        def _worker():
            def _progress(dl, total):
                if total > 0:
                    mb = dl / (1024 * 1024)
                    mb_total = total / (1024 * 1024)
                    self._log_signal.emit(f"Downloading update: {mb:.0f}/{mb_total:.0f} MB")

            return download_update(download_url, on_progress=_progress)

        def _run():
            exe_path = _worker()
            if exe_path:
                if restart:
                    install_and_restart(exe_path)
                else:
                    install_on_quit(exe_path)
                    self._log_signal.emit("Update downloaded — will install when you quit")

        threading.Thread(target=_run, daemon=True).start()

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
        # Install pending update if downloaded
        from core.update_checker import apply_pending_update
        apply_pending_update()
        QApplication.instance().quit()

    def _show_about(self):
        QMessageBox.about(
            self,
            "About HiDock Tools",
            "HiDock Tools\n\n"
            "USB sync, mic trigger, and transcription for HiDock.\n\n"
            "Python/PyQt6 port of the macOS app."
        )

    # User-friendly labels → technical mapping
    _FEEDBACK_CATEGORIES = [
        ("Something isn't working", "bug", "General"),
        ("Recording & downloads", "usb-sync", "`Windows-Script/extractor.py`, `core/usb_sync.py`"),
        ("Microphone detection", "mic-trigger", "`core/mic_trigger.py`"),
        ("Transcription & speech-to-text", "transcription", "`core/transcription.py`, `transcribe_cpp.py`"),
        ("App appearance or layout", "ui", "`ui/main_window.py`, `resources/theme.qss`"),
        ("I have a suggestion", "enhancement", "General"),
    ]
    _FEEDBACK_SEVERITIES = [
        ("It stops me from working", "priority-high"),
        ("It's annoying but I can work around it", "priority-medium"),
        ("It's a minor thing", "priority-low"),
    ]

    def _send_feedback(self):
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QTextEdit

        dlg = QDialog(self)
        dlg.setWindowTitle("Send Feedback")
        dlg.setMinimumWidth(450)
        layout = QFormLayout(dlg)

        cat_combo = QComboBox()
        for label, _, _ in self._FEEDBACK_CATEGORIES:
            cat_combo.addItem(label)
        layout.addRow("What's this about?", cat_combo)

        sev_combo = QComboBox()
        for label, _ in self._FEEDBACK_SEVERITIES:
            sev_combo.addItem(label)
        layout.addRow("How much does it affect you?", sev_combo)

        desc_edit = QTextEdit()
        desc_edit.setPlaceholderText("Describe what happened...")
        desc_edit.setMaximumHeight(100)
        layout.addRow("What happened?", desc_edit)

        expected_edit = QTextEdit()
        expected_edit.setPlaceholderText("What did you expect to happen instead?")
        expected_edit.setMaximumHeight(60)
        layout.addRow("What did you expect?", expected_edit)

        from PyQt6.QtWidgets import QLineEdit
        steps_edit = QLineEdit()
        steps_edit.setPlaceholderText("e.g. Click Download, wait 30 seconds, app freezes")
        layout.addRow("Steps (optional)", steps_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Send")
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addRow(buttons)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        description = desc_edit.toPlainText().strip()
        if not description:
            return

        cat_label, cat_gh, cat_component = self._FEEDBACK_CATEGORIES[cat_combo.currentIndex()]
        sev_label, sev_gh = self._FEEDBACK_SEVERITIES[sev_combo.currentIndex()]
        expected = expected_edit.toPlainText().strip()
        steps = steps_edit.text().strip()

        # System info
        app_version = "1.0.0"
        win_version = platform.platform()
        py_version = sys.version.split()[0]
        device_status = "Connected" if self._last_extractor_ready else "Not connected"
        trigger_status = "Running" if self.mic_trigger._running else "Stopped"
        rec_count = len(self._entries)
        dl_count = sum(1 for e in self._entries if e.recording.downloaded)

        # Build structured issue
        title = (f"Feature: {description[:60]}" if cat_gh == "enhancement"
                 else f"{cat_label}: {description[:50]}")

        body = f"## Description\n{description}\n"
        if expected:
            body += f"\n## Expected Behavior\n{expected}\n"
        if steps:
            body += f"\n## Steps to Reproduce\n{steps}\n"
        body += f"\n## Component\n{cat_component}\n"
        body += "\n## Platform\nWindows\n"
        body += (
            f"\n<details>\n<summary>System Information</summary>\n\n"
            f"- **App Version:** {app_version}\n"
            f"- **Windows:** {win_version}\n"
            f"- **Python:** {py_version}\n"
            f"- **Devices:** {device_status}\n"
            f"- **Mic Trigger:** {trigger_status}\n"
            f"- **Recordings:** {rec_count} synced, {dl_count} downloaded\n"
            f"</details>\n"
        )

        labels = [cat_gh, sev_gh, "feedback"]
        token = self._get_feedback_token()
        if token:
            self._submit_github_issue(title, body, token, labels)
        else:
            encoded_body = quote(body)
            lbl = ",".join(labels)
            webbrowser.open(
                "https://github.com/jw-gsl/HiDock-Mic-Trigger/issues/new"
                f"?title={quote(title)}&body={encoded_body}&labels={lbl}"
            )

    def _get_feedback_token(self) -> str | None:
        try:
            from core import feedback_token
            return feedback_token.TOKEN.strip()
        except (ImportError, AttributeError):
            pass
        token_path = Path(__file__).resolve().parent.parent / "feedback_token.txt"
        if token_path.exists():
            return token_path.read_text().strip()
        return None

    def _submit_github_issue(self, title: str, body: str, token: str, labels: list[str] | None = None):
        import json as _json
        self.statusBar().showMessage("Sending feedback...")

        def _worker():
            import urllib.request
            import ssl
            try:
                import certifi
                ctx = ssl.create_default_context(cafile=certifi.where())
            except ImportError:
                ctx = ssl.create_default_context()

            data = _json.dumps({"title": title, "body": body, "labels": labels or ["feedback"]}).encode()
            req = urllib.request.Request(
                "https://api.github.com/repos/jw-gsl/HiDock-Mic-Trigger/issues",
                data=data, method="POST",
            )
            req.add_header("Authorization", f"token {token}")
            req.add_header("Content-Type", "application/json")
            req.add_header("User-Agent", "HiDock/1.0")
            try:
                resp = urllib.request.urlopen(req, timeout=15, context=ctx)
                if resp.status == 201:
                    resp_data = _json.loads(resp.read())
                    self._save_feedback_history(
                        title=title,
                        url=resp_data.get("html_url", ""),
                        number=resp_data.get("number", 0),
                        state=resp_data.get("state", "open"),
                    )
                    return True
            except Exception as e:
                self._log_signal.emit(f"Feedback failed: {e}")
            return False

        def _run():
            if _worker():
                self._log_signal.emit("Feedback submitted")
                self.statusBar().showMessage("Feedback sent — thank you!", 3000)

        threading.Thread(target=_run, daemon=True).start()

    # ── Feedback History ──

    @property
    def _feedback_history_path(self) -> Path:
        return HIDOCK_ROOT / "feedback_history.json"

    def _load_feedback_history(self) -> list[dict]:
        import json as _json
        try:
            return _json.loads(self._feedback_history_path.read_text())
        except Exception:
            return []

    def _save_feedback_history(self, title: str, url: str, number: int, state: str):
        import json as _json
        from datetime import datetime, timezone
        history = self._load_feedback_history()
        history.insert(0, {
            "title": title,
            "url": url,
            "number": number,
            "state": state,
            "date": datetime.now(timezone.utc).isoformat(),
        })
        history = history[:50]
        self._feedback_history_path.parent.mkdir(parents=True, exist_ok=True)
        self._feedback_history_path.write_text(_json.dumps(history, indent=2))

    def _show_feedback_history(self):
        from PyQt6.QtWidgets import (
            QDialog, QLineEdit, QListWidget, QListWidgetItem,
            QTextEdit,
        )

        history = self._load_feedback_history()
        if not history:
            QMessageBox.information(self, "My Feedback", "No feedback submitted yet.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("My Feedback")
        dlg.setMinimumSize(700, 450)
        dlg_layout = QVBoxLayout(dlg)

        # Filter row: All / Open / Closed + Sort + Search
        filter_row = QHBoxLayout()
        open_count = sum(1 for h in history if h.get("state") != "closed")
        closed_count = sum(1 for h in history if h.get("state") == "closed")
        filter_all_btn = QPushButton(f"All ({len(history)})")
        filter_open_btn = QPushButton(f"Open ({open_count})")
        filter_closed_btn = QPushButton(f"Closed ({closed_count})")
        for btn in (filter_all_btn, filter_open_btn, filter_closed_btn):
            btn.setCheckable(True)
            btn.setMaximumHeight(26)
            filter_row.addWidget(btn)
        filter_all_btn.setChecked(True)
        filter_row.addStretch()
        sort_combo = QComboBox()
        sort_combo.addItems(["Newest First", "Oldest First", "Issue Number"])
        sort_combo.setMaximumWidth(140)
        filter_row.addWidget(sort_combo)
        search_edit = QLineEdit()
        search_edit.setPlaceholderText("Search feedback...")
        search_edit.setMaximumWidth(200)
        filter_row.addWidget(search_edit)
        dlg_layout.addLayout(filter_row)

        # Split view: list left, detail right
        splitter = QSplitter(Qt.Orientation.Horizontal)
        list_widget = QListWidget()
        list_widget.setMinimumWidth(250)
        splitter.addWidget(list_widget)
        detail_text = QTextEdit()
        detail_text.setReadOnly(True)
        detail_text.setMinimumWidth(300)
        splitter.addWidget(detail_text)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        dlg_layout.addWidget(splitter, stretch=1)

        # Bottom row
        bottom_row = QHBoxLayout()
        github_btn = QPushButton("View on GitHub")
        github_btn.setEnabled(False)
        bottom_row.addWidget(github_btn)
        bottom_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dlg.reject)
        bottom_row.addWidget(close_btn)
        dlg_layout.addLayout(bottom_row)

        current_filter = ["all"]
        current_url = [""]

        def _fmt_date(d):
            try:
                from datetime import datetime
                return datetime.fromisoformat(d).strftime("%d %b %Y, %H:%M")
            except Exception:
                return d

        def _category(title):
            if title.startswith("Feature:"):
                return "Suggestion"
            parts = title.split(":", 1)
            return parts[0].strip() if len(parts) == 2 else "General"

        def _refresh():
            list_widget.clear()
            detail_text.clear()
            github_btn.setEnabled(False)
            current_url[0] = ""
            query = search_edit.text().lower().strip()
            items = history[:]
            if current_filter[0] == "open":
                items = [h for h in items if h.get("state") != "closed"]
            elif current_filter[0] == "closed":
                items = [h for h in items if h.get("state") == "closed"]
            if query:
                items = [h for h in items if (
                    query in h.get("title", "").lower()
                    or query in h.get("body", "").lower()
                    or query in f"#{h.get('number', 0)}"
                )]
            si = sort_combo.currentIndex()
            if si == 0:
                items.sort(key=lambda h: h.get("date", ""), reverse=True)
            elif si == 1:
                items.sort(key=lambda h: h.get("date", ""))
            else:
                items.sort(key=lambda h: h.get("number", 0), reverse=True)
            for item in items:
                icon = "✅" if item.get("state") == "closed" else "🔵"
                n = item.get("number", 0)
                t = item.get("title", "Untitled")
                d = _fmt_date(item.get("date", ""))
                cat = _category(t)
                li = QListWidgetItem(f"{icon} #{n} — {t}\n     {d}  [{cat}]")
                li.setData(Qt.ItemDataRole.UserRole, item)
                list_widget.addItem(li)
            if items:
                list_widget.setCurrentRow(0)

        def _on_select():
            cur = list_widget.currentItem()
            if not cur:
                detail_text.clear()
                github_btn.setEnabled(False)
                return
            item = cur.data(Qt.ItemDataRole.UserRole)
            n = item.get("number", 0)
            t = item.get("title", "")
            st = "Closed" if item.get("state") == "closed" else "Open"
            d = _fmt_date(item.get("date", ""))
            body = item.get("body", "")
            url = item.get("url", "")
            current_url[0] = url
            github_btn.setEnabled(bool(url))
            clean = (body
                .replace("<details>", "").replace("</details>", "")
                .replace("<summary>System Information</summary>", "System Information:")
                .replace("## ", "").replace("- **", "  ").replace("**", "").strip())
            detail_text.setText(f"#{n} — {t}\nStatus: {st}  |  {d}\n{'─' * 50}\n\n{clean}")

        def _set_filter(f):
            current_filter[0] = f
            filter_all_btn.setChecked(f == "all")
            filter_open_btn.setChecked(f == "open")
            filter_closed_btn.setChecked(f == "closed")
            _refresh()

        filter_all_btn.clicked.connect(lambda: _set_filter("all"))
        filter_open_btn.clicked.connect(lambda: _set_filter("open"))
        filter_closed_btn.clicked.connect(lambda: _set_filter("closed"))
        sort_combo.currentIndexChanged.connect(lambda _: _refresh())
        search_edit.textChanged.connect(lambda _: _refresh())
        list_widget.currentRowChanged.connect(lambda _: _on_select())
        github_btn.clicked.connect(lambda: webbrowser.open(current_url[0]) if current_url[0] else None)

        _refresh()
        dlg.exec()

    # ── Voice Library ──────────────────────────────────────────────────

    def _show_cowork_prompt(self):
        from ui.cowork_dialog import CoworkDialog
        dlg = CoworkDialog(self)
        dlg.exec()

    def _show_voice_library(self):
        from ui.voice_library_dialog import VoiceLibraryDialog
        dlg = VoiceLibraryDialog(self)
        dlg.exec()

    def _show_model_manager(self):
        from ui.model_manager_dialog import ModelManagerDialog
        dlg = ModelManagerDialog(self)
        dlg.exec()

    def _show_device_manager(self):
        from core.models import PairedDevice, load_paired_devices, save_paired_devices
        from ui.device_manager_dialog import DeviceManagerDialog

        self._paired_devices = load_paired_devices(self.settings)
        dlg = DeviceManagerDialog(self._paired_devices, parent=self)

        def _on_forget(device_id: str):
            self._paired_devices = [d for d in self._paired_devices if d.device_id != device_id]
            save_paired_devices(self.settings, self._paired_devices)

        def _on_pair_volume(volume_name: str, subpath: str):
            device = PairedDevice.volume(volume_name, volume_name, subpath=subpath or None)
            if any(d.device_id == device.device_id for d in self._paired_devices):
                return
            self._paired_devices.append(device)
            save_paired_devices(self.settings, self._paired_devices)
            dlg.set_devices(self._paired_devices)

        def _on_scan_volumes():
            import json
            try:
                data = run_extractor(["scan-volumes"], timeout=10)
                result = json.loads(data) if data else {}
                volumes = result.get("volumes", [])
            except Exception:
                volumes = []
            dlg.pair_widget.set_scan_results(volumes)

        dlg.deviceForgotten.connect(_on_forget)
        dlg.volumePaired.connect(_on_pair_volume)
        dlg.pair_widget.scanRequested.connect(_on_scan_volumes)
        try:
            dlg.exec()
        finally:
            dlg.deleteLater()

    # ── Transcript Viewer ──────────────────────────────────────────────

    @staticmethod
    def _diarized_json_path(transcript_path: str) -> str | None:
        """Derive _diarized.json path from a .md transcript path."""
        p = Path(transcript_path)
        base = p.stem  # e.g. "recording" from "recording.md"
        diarized = p.parent / f"{base}_diarized.json"
        return str(diarized) if diarized.exists() else None

    def _open_transcript_viewer(self, json_path: str, audio_path: str):
        from ui.transcript_viewer import TranscriptViewerDialog
        dlg = TranscriptViewerDialog(json_path, audio_path, parent=self)
        dlg.exec()
