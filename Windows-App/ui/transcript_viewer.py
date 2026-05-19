"""Transcript viewer dialog — shows diarized transcript with speaker management."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

# Fixed color palette matching macOS
SPEAKER_COLORS = [
    QColor("#4A90D9"),  # blue
    QColor("#4CAF50"),  # green
    QColor("#FF9800"),  # orange
    QColor("#9C27B0"),  # purple
    QColor("#E91E63"),  # pink
    QColor("#009688"),  # teal
    QColor("#3F51B5"),  # indigo
    QColor("#00BCD4"),  # mint/cyan
]


def _speaker_color(speaker_id: int) -> QColor:
    return SPEAKER_COLORS[abs(speaker_id) % len(SPEAKER_COLORS)]


def _format_time(seconds: float) -> str:
    total = int(seconds)
    m, s = divmod(total, 60)
    return f"{m:02d}:{s:02d}"


class TranscriptViewerDialog(QDialog):
    """Dialog showing a diarized transcript with speaker rename and enrollment."""

    def __init__(self, json_path: str, audio_path: str, parent=None):
        super().__init__(parent)
        self.json_path = json_path
        self.audio_path = audio_path
        self.transcript: dict = {}
        self._history: list[dict] = []

        # Audio playback
        self._player = QMediaPlayer()
        self._audio_output = QAudioOutput()
        self._player.setAudioOutput(self._audio_output)
        self._playing_seg_id: str | None = None
        self._stop_timer = None

        self._load_transcript()

        self.setWindowTitle(f"Transcript — {self.transcript.get('audio_file', '')}")
        self.setMinimumSize(700, 500)
        self.resize(800, 600)

        self._init_ui()

    def _load_transcript(self):
        try:
            with open(self.json_path, "r", encoding="utf-8") as f:
                self.transcript = json.load(f)
        except Exception as e:
            self.transcript = {"version": 1, "audio_file": "", "segments": [], "speaker_names": {}}
            print(f"Failed to load transcript: {e}")

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Top bar
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(12, 8, 12, 8)
        audio_label = QLabel(self.transcript.get("audio_file", ""))
        audio_label.setFont(QFont("", -1, QFont.Weight.Bold.value))
        top_bar.addWidget(audio_label)
        top_bar.addStretch()

        self._undo_btn = QPushButton("Undo")
        self._undo_btn.clicked.connect(self._undo_merge)
        self._undo_btn.setEnabled(False)
        self._undo_btn.setShortcut("Ctrl+Z")
        top_bar.addWidget(self._undo_btn)

        top_bar.addSpacing(8)

        top_bar.addWidget(QLabel("Speakers:"))
        self._speaker_spin = QSpinBox()
        self._speaker_spin.setRange(2, 8)
        self._speaker_spin.setValue(2)
        self._speaker_spin.setFixedWidth(50)
        top_bar.addWidget(self._speaker_spin)

        rediarize_btn = QPushButton("Re-diarize")
        rediarize_btn.clicked.connect(self._rediarize)
        top_bar.addWidget(rediarize_btn)

        top_bar.addSpacing(8)

        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save_transcript)
        top_bar.addWidget(save_btn)

        top_widget = QWidget()
        top_widget.setLayout(top_bar)
        top_widget.setStyleSheet("background: rgba(128,128,128,0.08);")
        layout.addWidget(top_widget)

        # Speaker legend
        legend_layout = QHBoxLayout()
        legend_layout.setContentsMargins(12, 6, 12, 6)
        legend_layout.setSpacing(6)

        speaker_ids = sorted(set(
            seg.get("speaker_id", 0) for seg in self.transcript.get("segments", [])
        ))
        speaker_names = self.transcript.get("speaker_names", {})
        self._has_speakers = len(speaker_ids) > 1 or bool(speaker_names)

        self._speaker_ids = speaker_ids
        if self._has_speakers:
            for sid in speaker_ids:
                name = speaker_names.get(str(sid), f"Speaker {sid + 1}")
                color = _speaker_color(sid)
                btn = QPushButton(name)
                btn.setStyleSheet(
                    f"QPushButton {{ background: {color.name()}; color: white; "
                    f"border-radius: 10px; padding: 3px 10px; font-size: 12px; border: none; }}"
                    f"QPushButton:hover {{ background: {color.lighter(120).name()}; }}"
                )
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(lambda checked, s=sid: self._rename_speaker(s))
                btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                btn.customContextMenuRequested.connect(lambda pos, s=sid, b=btn: self._show_merge_menu(s, b, pos))
                legend_layout.addWidget(btn)

        legend_layout.addStretch()
        legend_widget = QWidget()
        legend_widget.setLayout(legend_layout)
        if self._has_speakers:
            layout.addWidget(legend_widget)

        # Segments scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.segments_widget = QWidget()
        self.segments_layout = QVBoxLayout(self.segments_widget)
        self.segments_layout.setContentsMargins(12, 8, 12, 8)
        self.segments_layout.setSpacing(4)
        self.segments_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._populate_segments()

        scroll.setWidget(self.segments_widget)
        layout.addWidget(scroll, stretch=1)

    def _populate_segments(self):
        speaker_names = self.transcript.get("speaker_names", {})

        for seg in self.transcript.get("segments", []):
            row = QHBoxLayout()
            row.setSpacing(8)

            # Play button
            start = seg.get("start", 0.0)
            end = seg.get("end", start + 5.0)
            seg_id = f"{seg.get('speaker_id', 0)}-{start}"
            play_btn = QPushButton("▶")
            play_btn.setFixedSize(22, 22)
            play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            play_btn.setStyleSheet("QPushButton { border: none; font-size: 12px; }")
            play_btn.clicked.connect(lambda checked, s=start, e=end, sid=seg_id: self._play_segment(s, e, sid))
            row.addWidget(play_btn)

            # Timestamp
            ts_label = QLabel(f"[{_format_time(start)}]")
            ts_label.setFont(QFont("Menlo, Consolas, monospace", 11))
            ts_label.setStyleSheet("color: gray;")
            ts_label.setFixedWidth(55)
            row.addWidget(ts_label)

            # Speaker pill (only for diarized transcripts)
            if self._has_speakers:
                sid = seg.get("speaker_id", 0)
                name = speaker_names.get(str(sid), f"Speaker {sid + 1}")
                color = _speaker_color(sid)
                pill = QPushButton(name)
                pill.setFixedHeight(22)
                pill.setStyleSheet(
                    f"QPushButton {{ background: {color.name()}; color: white; "
                    f"border-radius: 10px; padding: 2px 8px; font-size: 11px; border: none; }}"
                    f"QPushButton:hover {{ background: {color.lighter(120).name()}; }}"
                )
                pill.setCursor(Qt.CursorShape.PointingHandCursor)
                pill.clicked.connect(lambda checked, s=sid: self._rename_speaker(s))
                row.addWidget(pill)

            # Text
            text_label = QLabel(seg.get("text", ""))
            text_label.setWordWrap(True)
            text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row.addWidget(text_label, stretch=1)

            container = QWidget()
            container.setLayout(row)
            self.segments_layout.addWidget(container)

    def _rename_speaker(self, speaker_id: int):
        speaker_names = self.transcript.get("speaker_names", {})
        current = speaker_names.get(str(speaker_id), f"Speaker {speaker_id}")

        name, ok = QInputDialog.getText(
            self, "Rename Speaker",
            f"Enter name for Speaker {speaker_id}:",
            text=current,
        )
        if not ok or not name.strip():
            return

        name = name.strip()
        if "speaker_names" not in self.transcript:
            self.transcript["speaker_names"] = {}
        self.transcript["speaker_names"][str(speaker_id)] = name

        # Save and refresh UI
        self._save_transcript()
        self._refresh_ui()

        # Enroll speaker
        seg = next(
            (s for s in self.transcript.get("segments", []) if s.get("speaker_id") == speaker_id),
            None,
        )
        if seg:
            self._enroll_speaker(name, self.audio_path, seg.get("start", 0.0), seg.get("end", 0.0))

    def _enroll_speaker(self, name: str, audio_path: str, start: float, end: float):
        """Shell out to voice_library_lite.py to enroll the speaker."""
        try:
            shared_dir = Path(__file__).resolve().parent.parent.parent / "shared"
            script = shared_dir / "voice_library_lite.py"
            if not script.exists():
                return
            cmd = [
                sys.executable, str(script),
                "enroll", "--name", name,
                "--audio", audio_path,
                "--start", str(start),
                "--end", str(end),
            ]
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            print(f"Failed to enroll speaker: {e}")

    def _save_transcript(self):
        try:
            with open(self.json_path, "w", encoding="utf-8") as f:
                json.dump(self.transcript, f, indent=2, ensure_ascii=False)
        except Exception as e:
            QMessageBox.warning(self, "Save Failed", f"Could not save transcript:\n{e}")

    def _refresh_ui(self):
        """Rebuild the entire dialog UI after changes."""
        # Clear existing segments
        while self.segments_layout.count():
            child = self.segments_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self._populate_segments()
        self._undo_btn.setEnabled(bool(self._history))

    def _play_segment(self, start: float, end: float, seg_id: str):
        """Play audio for a segment's time range."""
        if self._playing_seg_id == seg_id:
            self._player.stop()
            self._playing_seg_id = None
            return

        self._player.stop()
        self._player.setSource(QUrl.fromLocalFile(self.audio_path))
        self._player.setPosition(int(start * 1000))
        self._player.play()
        self._playing_seg_id = seg_id

        # Stop after segment duration
        from PyQt6.QtCore import QTimer
        duration_ms = int((end - start) * 1000)
        if self._stop_timer:
            self._stop_timer.stop()
        self._stop_timer = QTimer(self)
        self._stop_timer.setSingleShot(True)
        self._stop_timer.timeout.connect(lambda: self._player.stop())
        self._stop_timer.start(duration_ms)

    def _show_merge_menu(self, source_id: int, button: QPushButton, pos):
        """Show context menu to merge this speaker into another."""
        menu = QMenu(self)
        speaker_names = self.transcript.get("speaker_names", {})
        for target_id in self._speaker_ids:
            if target_id == source_id:
                continue
            target_name = speaker_names.get(str(target_id), f"Speaker {target_id + 1}")
            action = menu.addAction(f"Merge into {target_name}")
            action.triggered.connect(lambda checked, t=target_id: self._merge_speaker(source_id, t))
        menu.exec(button.mapToGlobal(pos))

    def _merge_speaker(self, source_id: int, target_id: int):
        """Merge all segments from source speaker into target."""
        self._history.append(copy.deepcopy(self.transcript))

        segments = self.transcript.get("segments", [])
        for seg in segments:
            if seg.get("speaker_id") == source_id:
                seg["speaker_id"] = target_id

        # Remove source speaker name
        speaker_names = self.transcript.get("speaker_names", {})
        speaker_names.pop(str(source_id), None)

        # Merge consecutive same-speaker segments
        merged = []
        for seg in segments:
            if not seg.get("text", "").strip():
                continue
            if merged and merged[-1].get("speaker_id") == seg.get("speaker_id"):
                merged[-1]["end"] = seg["end"]
                merged[-1]["text"] += " " + seg.get("text", "").strip()
            else:
                merged.append(dict(seg))
        self.transcript["segments"] = merged

        self._speaker_ids = sorted(set(s.get("speaker_id", 0) for s in merged))
        self._save_transcript()
        self._refresh_ui()

    def _undo_merge(self):
        """Undo the last merge operation."""
        if not self._history:
            return
        self.transcript = self._history.pop()
        self._speaker_ids = sorted(set(
            s.get("speaker_id", 0) for s in self.transcript.get("segments", [])
        ))
        self._save_transcript()
        self._refresh_ui()

    def _rediarize(self):
        """Re-run speaker diarization without re-transcribing."""
        n_speakers = self._speaker_spin.value()
        try:
            repo_root = Path(__file__).resolve().parent.parent.parent
            script = repo_root / "transcription-pipeline" / "transcribe.py"
            if not script.exists():
                QMessageBox.warning(self, "Error", "Transcription script not found")
                return

            self.setWindowTitle("Re-diarizing…")
            import threading

            def _run():
                try:
                    cmd = [
                        sys.executable, str(script),
                        "rediarize", self.json_path,
                        "--n-speakers", str(n_speakers),
                    ]
                    subprocess.run(cmd, capture_output=True, check=True)
                    # Reload and refresh
                    self._load_transcript()
                    self._speaker_ids = sorted(set(
                        s.get("speaker_id", 0) for s in self.transcript.get("segments", [])
                    ))
                    self._refresh_ui()
                    self.setWindowTitle(f"Transcript — {self.transcript.get('audio_file', '')}")
                except Exception as e:
                    print(f"Re-diarize failed: {e}")
                    self.setWindowTitle("Re-diarize failed")

            threading.Thread(target=_run, daemon=True).start()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Re-diarize failed:\n{e}")
