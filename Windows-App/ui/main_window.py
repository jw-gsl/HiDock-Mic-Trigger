"""Unified main window — mirrors the macOS hidock-mic-trigger app.

Layout (top to bottom):
  1. Mic Trigger strip — status, Start/Stop, mic selector, auto-start
  2. Separator
  3. Sync status + folder labels
  4. Toolbar rows (Pair, Unpair, Recordings Folder, Transcript Folder, Refresh, etc.)
  5. Recording table
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QSettings, QTimer, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QAction, QColor, QFont, QIcon
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
    QMessageBox,
    QPushButton,
    QSplitter,
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

    def __init__(self):
        super().__init__()
        self.settings = QSettings("HiDock", "HiDockTools")
        self.mic_trigger = MicTrigger(
            on_state_change=self._on_mic_state_change,
            on_log=self._on_mic_log,
        )
        self._entries: list[SyncRecordingEntry] = []
        self._sync_busy = False
        self._trigger_start_time: float | None = None
        self._init_ui()
        self._connect_signals()
        self._load_settings()
        self._refresh_mic_list()

        # Uptime timer
        self._uptime_timer = QTimer(self)
        self._uptime_timer.timeout.connect(self._update_uptime)
        self._uptime_timer.start(1000)

    def _init_ui(self):
        self.setWindowTitle("HiDock")
        self.setMinimumSize(980, 580)
        self.resize(1120, 640)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        # ── Mic Trigger section ──
        trigger_box = QGroupBox("Mic Trigger")
        trigger_layout = QHBoxLayout(trigger_box)
        trigger_layout.setContentsMargins(8, 4, 8, 4)

        self.trigger_status_label = QLabel("Stopped")
        self.trigger_status_label.setStyleSheet("color: gray;")
        trigger_layout.addWidget(self.trigger_status_label)

        self.trigger_uptime_label = QLabel("")
        self.trigger_uptime_label.setStyleSheet("color: gray; font-size: 11px;")
        trigger_layout.addWidget(self.trigger_uptime_label)

        trigger_layout.addStretch()

        self.start_btn = QPushButton("Start")
        self.start_btn.clicked.connect(self._start_trigger)
        trigger_layout.addWidget(self.start_btn)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self._stop_trigger)
        self.stop_btn.setEnabled(False)
        trigger_layout.addWidget(self.stop_btn)

        trigger_layout.addWidget(QLabel("Trigger Mic:"))
        self.mic_combo = QComboBox()
        self.mic_combo.setMinimumWidth(200)
        self.mic_combo.currentTextChanged.connect(self._on_mic_changed)
        trigger_layout.addWidget(self.mic_combo)

        self.auto_start_check = QCheckBox("Auto-start")
        self.auto_start_check.stateChanged.connect(self._on_auto_start_changed)
        trigger_layout.addWidget(self.auto_start_check)

        layout.addWidget(trigger_box)

        # ── Sync status labels ──
        self.sync_status_label = QLabel("Status: Not loaded")
        layout.addWidget(self.sync_status_label)

        self.recordings_folder_label = QLabel("Recordings folder: Not set")
        self.recordings_folder_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self.recordings_folder_label)

        self.transcript_folder_label = QLabel(f"Transcript folder: {RAW_TRANSCRIPTS_DIR}")
        self.transcript_folder_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self.transcript_folder_label)

        # ── Toolbar Row 1: Pair, Unpair, Folders, Refresh ──
        row1 = QHBoxLayout()
        row1.setSpacing(6)

        self.pair_btn = QPushButton("Pair Dock")
        self.pair_btn.clicked.connect(self._pair_dock)
        row1.addWidget(self.pair_btn)

        self.unpair_btn = QPushButton("Unpair")
        self.unpair_btn.clicked.connect(self._unpair_dock)
        row1.addWidget(self.unpair_btn)

        rec_folder_btn = QPushButton("Recordings Folder")
        rec_folder_btn.clicked.connect(self._choose_recordings_folder)
        row1.addWidget(rec_folder_btn)

        transcript_folder_btn = QPushButton("Transcript Folder")
        transcript_folder_btn.clicked.connect(self._choose_transcript_folder)
        row1.addWidget(transcript_folder_btn)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._refresh_status)
        row1.addWidget(self.refresh_btn)

        row1.addStretch()

        # Summary label on the right
        self.summary_label = QLabel("No recordings loaded")
        self.summary_label.setStyleSheet("color: gray;")
        row1.addWidget(self.summary_label)

        layout.addLayout(row1)

        # ── Toolbar Row 2: Download, Transcribe ──
        row2 = QHBoxLayout()
        row2.setSpacing(6)

        self.download_selected_btn = QPushButton("Download Selected")
        self.download_selected_btn.clicked.connect(self._download_selected)
        row2.addWidget(self.download_selected_btn)

        self.download_new_btn = QPushButton("Download New")
        self.download_new_btn.clicked.connect(self._download_new)
        row2.addWidget(self.download_new_btn)

        mark_btn = QPushButton("Mark Downloaded")
        mark_btn.clicked.connect(self._mark_downloaded)
        row2.addWidget(mark_btn)

        self.transcribe_selected_btn = QPushButton("Transcribe Selected")
        self.transcribe_selected_btn.clicked.connect(self._transcribe_selected)
        row2.addWidget(self.transcribe_selected_btn)

        self.transcribe_all_btn = QPushButton("Transcribe All")
        self.transcribe_all_btn.clicked.connect(self._transcribe_all)
        row2.addWidget(self.transcribe_all_btn)

        self.download_model_btn = QPushButton("Download Model")
        self.download_model_btn.clicked.connect(self._download_model)
        row2.addWidget(self.download_model_btn)
        self._update_model_button_state()

        row2.addStretch()

        self.hide_downloaded_check = QCheckBox("Hide Downloaded")
        self.hide_downloaded_check.stateChanged.connect(self._on_hide_downloaded_changed)
        row2.addWidget(self.hide_downloaded_check)

        self.auto_download_check = QCheckBox("Auto-download New")
        self.auto_download_check.stateChanged.connect(self._on_auto_download_changed)
        row2.addWidget(self.auto_download_check)

        layout.addLayout(row2)

        # ── Recording table ──
        self.table_model = RecordingTableModel()
        self.table_view = QTableView()
        self.table_view.setModel(self.table_model)
        self.table_view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table_view.setAlternatingRowColors(True)
        self.table_view.setSortingEnabled(True)
        self.table_view.horizontalHeader().setStretchLastSection(True)
        self.table_view.doubleClicked.connect(self._on_row_double_click)

        # Column widths
        header = self.table_view.horizontalHeader()
        widths = [130, 100, 80, 250, 160, 80, 80, 300]
        for i, w in enumerate(widths):
            if i < self.table_model.columnCount():
                header.resizeSection(i, w)

        layout.addWidget(self.table_view, stretch=1)

    def _connect_signals(self):
        self._log_signal.connect(self._append_log)
        self._sync_complete_signal.connect(self._on_sync_complete)
        self._transcription_status_signal.connect(self._on_transcription_status)

    def _load_settings(self):
        mic = self.settings.value("triggerMicName", "")
        auto_start = self.settings.value("autoStartTrigger", False, type=bool)
        self.auto_start_check.setChecked(auto_start)

        rec_folder = self.settings.value("recordingsFolder", "")
        if rec_folder:
            self.recordings_folder_label.setText(f"Recordings folder: {rec_folder}")

        transcript_folder = self.settings.value("transcriptFolder", str(RAW_TRANSCRIPTS_DIR))
        if transcript_folder:
            self.transcript_folder_label.setText(f"Transcript folder: {transcript_folder}")

        self.hide_downloaded_check.setChecked(
            self.settings.value("hideDownloaded", False, type=bool)
        )
        self.auto_download_check.setChecked(
            self.settings.value("autoDownload", False, type=bool)
        )

        if auto_start:
            QTimer.singleShot(500, self._start_trigger)

    def _refresh_mic_list(self):
        devices = list_audio_input_devices()
        self.mic_combo.clear()
        self.mic_combo.addItems(devices)
        saved = self.settings.value("triggerMicName", "")
        if saved and saved in devices:
            self.mic_combo.setCurrentText(saved)

    # ── Mic Trigger ──

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
        self.trigger_status_label.setStyleSheet("color: green;")

    @pyqtSlot()
    def _stop_trigger(self):
        self.mic_trigger.stop()
        self._trigger_start_time = None
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.trigger_status_label.setText("Stopped")
        self.trigger_status_label.setStyleSheet("color: gray;")
        self.trigger_uptime_label.setText("")

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

    # ── USB Sync ──

    @pyqtSlot()
    def _refresh_status(self):
        ready, err = extractor_ready()
        if not ready:
            self.sync_status_label.setText(f"Status: {err}")
            self.sync_status_label.setStyleSheet("color: orange;")
            return

        self.sync_status_label.setText("Status: Refreshing...")
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
            self.sync_status_label.setText(f"Status: {error}")
            self.sync_status_label.setStyleSheet("color: red;")
            return

        if not data:
            self.sync_status_label.setText("Status: No response")
            return

        connected = data.get("connected", False)
        recordings = data.get("recordings", [])
        output_dir = data.get("outputDir", "")

        if output_dir:
            self.recordings_folder_label.setText(f"Recordings folder: {output_dir}")

        status_text = f"Status: {'Connected' if connected else 'Not connected'} — {len(recordings)} recordings"
        self.sync_status_label.setText(status_text)
        self.sync_status_label.setStyleSheet(f"color: {'green' if connected else 'orange'};")

        entries = []
        for r in recordings:
            rec = SyncRecording.from_dict(r)
            entries.append(SyncRecordingEntry(recording=rec, device_name="HiDock"))

        self._entries = entries
        self._refresh_transcription_state()
        self._update_table()

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
        parts = [f"{total} recordings"]
        if downloaded:
            parts.append(f"{downloaded} downloaded")
        if transcribed:
            parts.append(f"{transcribed} transcribed")
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
            QMessageBox.warning(self, "Error", err)
            return
        # Pairing just means refreshing status — the extractor auto-detects
        self._refresh_status()

    @pyqtSlot()
    def _unpair_dock(self):
        self._entries = []
        self._update_table()
        self.sync_status_label.setText("Status: Unpaired")

    @pyqtSlot()
    def _choose_recordings_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Choose Recordings Folder",
            self.settings.value("recordingsFolder", str(RECORDINGS_DIR)),
        )
        if folder:
            self.settings.setValue("recordingsFolder", folder)
            self.recordings_folder_label.setText(f"Recordings folder: {folder}")
            try:
                run_extractor(["set-output", folder])
                self._refresh_status()
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to set folder: {e}")

    @pyqtSlot()
    def _choose_transcript_folder(self):
        current = self.settings.value("transcriptFolder", str(RAW_TRANSCRIPTS_DIR))
        folder = QFileDialog.getExistingDirectory(self, "Choose Transcript Folder", current)
        if folder:
            self.settings.setValue("transcriptFolder", folder)
            self.transcript_folder_label.setText(f"Transcript folder: {folder}")

    @pyqtSlot()
    def _download_selected(self):
        indices = self.table_view.selectionModel().selectedRows()
        if not indices:
            return
        entries = self.table_model.entries()
        filenames = [entries[i.row()].recording.name for i in indices]
        self._run_download(["download"] + filenames)

    @pyqtSlot()
    def _download_new(self):
        self._run_download(["download-new"])

    def _run_download(self, args: list[str]):
        self.sync_status_label.setText("Status: Downloading...")

        def _run():
            try:
                data = run_extractor(args, timeout=300)
                self._sync_complete_signal.emit(data, None)
            except Exception as e:
                self._sync_complete_signal.emit(None, str(e))

        threading.Thread(target=_run, daemon=True).start()

    @pyqtSlot()
    def _mark_downloaded(self):
        indices = self.table_view.selectionModel().selectedRows()
        if not indices:
            return
        entries = self.table_model.entries()
        filenames = [entries[i.row()].recording.name for i in indices]
        try:
            run_extractor(["mark-downloaded"] + filenames)
            self._refresh_status()
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    @pyqtSlot()
    def _transcribe_selected(self):
        indices = self.table_view.selectionModel().selectedRows()
        if not indices:
            return
        entries = self.table_model.entries()
        # Filter to downloaded files only
        targets = []
        for i in indices:
            entry = entries[i.row()]
            if entry.recording.downloaded and entry.recording.output_path:
                targets.append(Path(entry.recording.output_path))
        if not targets:
            QMessageBox.information(self, "Transcribe", "No downloaded recordings selected.")
            return
        self._run_transcription(targets)

    @pyqtSlot()
    def _transcribe_all(self):
        # Collect all downloaded + untranscribed recordings
        targets = []
        for entry in self._entries:
            if (entry.recording.downloaded
                    and not entry.recording.transcribed
                    and entry.recording.output_path):
                targets.append(Path(entry.recording.output_path))
        if not targets:
            QMessageBox.information(self, "Transcribe", "No untranscribed recordings to process.")
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
        self.sync_status_label.setText(f"Status: Transcribing {len(targets)} file(s)...")

        def _worker():
            model = None
            results = []
            for i, mp3_path in enumerate(targets):
                try:
                    def _progress(pct, _i=i):
                        total_pct = int((_i * 100 + pct) / len(targets))
                        self._log_signal.emit(f"Transcribing {_i+1}/{len(targets)}: {total_pct}%")

                    result = transcribe_file(mp3_path, model=model, on_progress=_progress)
                    results.append(result)
                    # Reuse model for subsequent files (returned in result isn't accessible,
                    # but transcribe_file caches internally on subsequent calls)
                except Exception as e:
                    self._log_signal.emit(f"Error transcribing {mp3_path.name}: {e}")

            # Signal completion
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
        self.sync_status_label.setText(f"Status: Transcribed {succeeded}/{total} files")
        self._refresh_transcription_state()
        self._update_table()

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
                os.startfile(os.path.dirname(path))

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

    def _update_model_button_state(self):
        """Update Download Model button based on whether model exists."""
        if whisper_model_ready():
            self.download_model_btn.setText("✓ Model Ready")
            self.download_model_btn.setEnabled(False)
            self.download_model_btn.setToolTip("Whisper model is downloaded and ready")
            self.transcribe_selected_btn.setEnabled(True)
            self.transcribe_all_btn.setEnabled(True)
        else:
            self.download_model_btn.setText("Download Model (~550 MB)")
            self.download_model_btn.setEnabled(True)
            self.download_model_btn.setToolTip("Download the speech recognition model")
            self.transcribe_selected_btn.setEnabled(False)
            self.transcribe_all_btn.setEnabled(False)

    _model_download_signal = pyqtSignal(int, int)       # bytes_downloaded, total_bytes
    _model_download_done_signal = pyqtSignal()
    _model_download_error_signal = pyqtSignal(str)

    @pyqtSlot()
    def _download_model(self):
        """Download the Whisper model in a background thread with progress."""
        from core.model_download import download_model

        self.download_model_btn.setEnabled(False)
        self.download_model_btn.setText("Downloading... 0%")
        self.sync_status_label.setText("Status: Downloading speech recognition model...")

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
            self.sync_status_label.setText(
                f"Status: Downloading model — {pct}% ({mb_dl:.0f}/{mb_total:.0f} MB)"
            )

    @pyqtSlot()
    def _on_model_done(self):
        self._update_model_button_state()
        self.sync_status_label.setText("Status: Model downloaded — ready to transcribe")
        self._model_download_signal.disconnect(self._on_model_progress)
        self._model_download_done_signal.disconnect(self._on_model_done)
        self._model_download_error_signal.disconnect(self._on_model_error)

    @pyqtSlot(str)
    def _on_model_error(self, msg: str):
        self.download_model_btn.setText("Download Model (~550 MB)")
        self.download_model_btn.setEnabled(True)
        self.sync_status_label.setText("Status: Model download failed")
        QMessageBox.warning(self, "Download Failed", f"Failed to download model:\n{msg}")
        self._model_download_signal.disconnect(self._on_model_progress)
        self._model_download_done_signal.disconnect(self._on_model_done)
        self._model_download_error_signal.disconnect(self._on_model_error)

    def closeEvent(self, event):
        self.mic_trigger.stop()
        super().closeEvent(event)
