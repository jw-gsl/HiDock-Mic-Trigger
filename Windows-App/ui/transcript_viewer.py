"""Transcript viewer dialog — shows diarized transcript with speaker management."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
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

        for sid in speaker_ids:
            name = speaker_names.get(str(sid), f"Speaker {sid}")
            color = _speaker_color(sid)
            btn = QPushButton(name)
            btn.setStyleSheet(
                f"QPushButton {{ background: {color.name()}; color: white; "
                f"border-radius: 10px; padding: 3px 10px; font-size: 12px; border: none; }}"
                f"QPushButton:hover {{ background: {color.lighter(120).name()}; }}"
            )
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, s=sid: self._rename_speaker(s))
            legend_layout.addWidget(btn)

        legend_layout.addStretch()
        legend_widget = QWidget()
        legend_widget.setLayout(legend_layout)
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

            # Timestamp
            start = seg.get("start", 0.0)
            ts_label = QLabel(f"[{_format_time(start)}]")
            ts_label.setFont(QFont("Menlo, Consolas, monospace", 11))
            ts_label.setStyleSheet("color: gray;")
            ts_label.setFixedWidth(55)
            row.addWidget(ts_label)

            # Speaker pill
            sid = seg.get("speaker_id", 0)
            name = speaker_names.get(str(sid), f"Speaker {sid}")
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
        """Rebuild the segments display after a rename."""
        # Clear existing segments
        while self.segments_layout.count():
            child = self.segments_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self._populate_segments()

        # Rebuild legend — close and reopen is simpler for full refresh
        # For now just update segment pills (legend requires full rebuild)
