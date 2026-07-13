"""Transcript viewer dialog — shows diarized transcript with speaker management."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
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


def _is_user_named(speaker_id: int, names: dict) -> bool:
    """A speaker counts as user-anchored when their name has been edited
    away from the auto-generated 'Speaker N' default. Mirrors the same
    check in shared/recluster_with_anchors.py so the "Re-cluster from my
    labels" button only appears when there's something to anchor against."""
    raw = names.get(str(speaker_id), "").strip()
    if not raw:
        return False
    if raw == f"Speaker {speaker_id + 1}":
        return False
    return True


class TranscriptViewerDialog(QDialog):
    """Dialog showing a diarized transcript with speaker rename and enrollment."""

    # Re-diarize / re-cluster run in worker threads; widgets must only be
    # touched on the GUI thread, so completion is marshalled through this
    # signal (same pattern as TerminalPane._output_ready).
    _reprocess_done = pyqtSignal(bool, str)  # (success, failure_title)

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

        self._reprocess_done.connect(self._on_reprocess_done)

        self._init_ui()

    def _load_transcript(self):
        try:
            with open(self.json_path, "r", encoding="utf-8") as f:
                self.transcript = json.load(f)
        except Exception as e:
            self.transcript = {"version": 1, "audio_file": "", "segments": [], "speaker_names": {}}
            print(f"Failed to load transcript: {e}")

    # ------------------------------------------------------------------
    # Speaker helpers
    # ------------------------------------------------------------------
    def _speaker_name(self, speaker_id: int) -> str:
        names = self.transcript.get("speaker_names", {})
        return names.get(str(speaker_id), f"Speaker {speaker_id + 1}")

    def _has_user_named_speakers(self) -> bool:
        names = self.transcript.get("speaker_names", {})
        return any(
            _is_user_named(int(sid), names)
            for sid in (s.get("speaker_id", 0) for s in self.transcript.get("segments", []))
        )

    def _next_new_speaker_id(self) -> int:
        """Lowest unused speaker_id — used when the user assigns a word
        range to a brand-new speaker via the inline split bar."""
        used = {s.get("speaker_id", 0) for s in self.transcript.get("segments", [])}
        n = 0
        while n in used:
            n += 1
        return n

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

        # Re-cluster from my labels — only useful once the user has named
        # at least one speaker (those become the anchors). Hidden
        # otherwise, mirroring the macOS viewer's gating.
        self._recluster_btn = QPushButton("Re-cluster from my labels")
        self._recluster_btn.setToolTip(
            "Use the speakers you've named as anchors and re-assign every other "
            "segment to its closest match. Segments you've already corrected stay put."
        )
        self._recluster_btn.clicked.connect(self._recluster)
        self._recluster_btn.setVisible(self._has_user_named_speakers())
        top_bar.addWidget(self._recluster_btn)

        top_bar.addSpacing(8)

        copy_btn = QPushButton("Copy All")
        copy_btn.setShortcut("Ctrl+Shift+C")
        copy_btn.clicked.connect(self._copy_all)
        top_bar.addWidget(copy_btn)

        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save_transcript)
        top_bar.addWidget(save_btn)

        top_widget = QWidget()
        top_widget.setLayout(top_bar)
        top_widget.setStyleSheet("background: rgba(128,128,128,0.08);")
        layout.addWidget(top_widget)

        # Recompute speaker presence
        speaker_ids = sorted(set(
            seg.get("speaker_id", 0) for seg in self.transcript.get("segments", [])
        ))
        speaker_names = self.transcript.get("speaker_names", {})
        self._has_speakers = len(speaker_ids) > 1 or bool(speaker_names)
        self._speaker_ids = speaker_ids

        # Speaker stats header (talk time + segment counts per speaker)
        self._stats_widget = QWidget()
        self._stats_layout = QHBoxLayout(self._stats_widget)
        self._stats_layout.setContentsMargins(12, 6, 12, 6)
        self._stats_layout.setSpacing(14)
        self._populate_stats()
        if self._has_speakers:
            layout.addWidget(self._stats_widget)
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setStyleSheet("color: rgba(128,128,128,0.25);")
            layout.addWidget(sep)

        # Speaker legend
        legend_layout = QHBoxLayout()
        legend_layout.setContentsMargins(12, 6, 12, 6)
        legend_layout.setSpacing(6)

        if self._has_speakers:
            for sid in speaker_ids:
                name = self._speaker_name(sid)
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

    def _populate_stats(self):
        """Per-speaker talk-time (sum of segment durations) and segment
        count, rendered as small color-coded chips in the header row."""
        # Clear
        while self._stats_layout.count():
            child = self._stats_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        if not self._has_speakers:
            return

        segments = self.transcript.get("segments", [])
        talk: dict[int, float] = {}
        counts: dict[int, int] = {}
        for seg in segments:
            sid = seg.get("speaker_id", 0)
            dur = max(0.0, seg.get("end", 0.0) - seg.get("start", 0.0))
            talk[sid] = talk.get(sid, 0.0) + dur
            counts[sid] = counts.get(sid, 0) + 1

        total_talk = sum(talk.values()) or 1.0

        # Total duration label (first segment start -> last segment end)
        if segments:
            total_dur = segments[-1].get("end", 0.0) - segments[0].get("start", 0.0)
        else:
            total_dur = 0.0
        total_label = QLabel(_format_time(total_dur))
        total_label.setStyleSheet("color: gray; font-size: 11px; font-weight: 600;")
        self._stats_layout.addWidget(total_label)

        # Sort speakers by descending talk time, matching macOS.
        for sid in sorted(talk, key=lambda s: talk[s], reverse=True):
            color = _speaker_color(sid)
            pct = talk[sid] / total_talk * 100
            chip = QLabel(
                f"● {self._speaker_name(sid)}  {_format_time(talk[sid])} "
                f"({pct:.0f}%, {counts[sid]} seg)"
            )
            chip.setStyleSheet(f"color: {color.name()}; font-size: 11px;")
            self._stats_layout.addWidget(chip)

        self._stats_layout.addStretch()

    def _populate_segments(self):
        for idx, seg in enumerate(self.transcript.get("segments", [])):
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
                name = self._speaker_name(sid)
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

            # Text — selectable so the user can highlight a word range to split.
            text_label = QLabel(seg.get("text", ""))
            text_label.setWordWrap(True)
            text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row.addWidget(text_label, stretch=1)

            # Split button — appears for diarized transcripts. Lets the
            # user assign a selected word range within this segment to a
            # different (or new) speaker, splitting the segment.
            if self._has_speakers:
                split_btn = QPushButton("✂")
                split_btn.setFixedSize(22, 22)
                split_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                split_btn.setToolTip(
                    "Select a word range in this segment's text, then click to "
                    "reassign that range to a speaker (splits the segment)."
                )
                split_btn.setStyleSheet("QPushButton { border: none; font-size: 12px; }")
                split_btn.clicked.connect(
                    lambda checked, i=idx, lbl=text_label, b=split_btn: self._show_split_menu(i, lbl, b)
                )
                row.addWidget(split_btn)

            container = QWidget()
            container.setLayout(row)
            self.segments_layout.addWidget(container)

    # ------------------------------------------------------------------
    # Word-range split
    # ------------------------------------------------------------------
    def _show_split_menu(self, seg_index: int, text_label: QLabel, button: QPushButton):
        """Show a menu of target speakers to assign the user's selected
        word range to. The selection is read from the segment's text
        QLabel (QLabel TextSelectableByMouse). If nothing is selected we
        prompt the user to select first.

        NOTE on timing granularity: the _diarized.json segments carry only
        start/end/text/speaker_id — there is no per-word timestamp array
        (Whisper produces word timestamps internally, but they are not
        persisted into the diarized JSON). So, exactly like the macOS
        viewer's applyRangeSplit, we approximate the sub-segment time
        boundaries by linear interpolation over word count. TitaNet's
        effective resolution swallows the per-word imprecision.
        """
        segments = self.transcript.get("segments", [])
        if seg_index < 0 or seg_index >= len(segments):
            return
        seg = segments[seg_index]
        full_text = seg.get("text", "")
        selected = text_label.selectedText().strip()

        word_range = self._resolve_word_range(full_text, selected)
        if word_range is None:
            QMessageBox.information(
                self,
                "Select words first",
                "Highlight a contiguous range of words in this segment's text, "
                "then click the split (✂) button to reassign them to a speaker.",
            )
            return

        start_word, end_word = word_range
        count = end_word - start_word + 1

        menu = QMenu(self)
        header = menu.addAction(f"Assign {count} word(s) to:")
        header.setEnabled(False)
        menu.addSeparator()
        for sid in self._speaker_ids:
            action = menu.addAction(self._speaker_name(sid))
            action.triggered.connect(
                lambda checked, i=seg_index, r=word_range, s=sid: self._apply_range_split(i, r, s)
            )
        menu.addSeparator()
        new_action = menu.addAction("New speaker…")
        new_action.triggered.connect(
            lambda checked, i=seg_index, r=word_range: self._apply_range_split(
                i, r, self._next_new_speaker_id()
            )
        )
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _resolve_word_range(self, full_text: str, selected: str) -> tuple[int, int] | None:
        """Map a selected substring back to a contiguous [start, end] word
        index range within full_text. Returns None if there's no usable
        selection. Matching is done on the word lists so it's robust to
        whitespace/newline differences in the selection."""
        if not selected:
            return None
        words = full_text.split()
        sel_words = selected.split()
        if not words or not sel_words:
            return None
        # Find the first contiguous occurrence of sel_words within words.
        n = len(sel_words)
        for i in range(0, len(words) - n + 1):
            if words[i:i + n] == sel_words:
                return (i, i + n - 1)
        # Fallback: if selection text doesn't match cleanly (e.g. partial
        # word), bail rather than guess.
        return None

    def _apply_range_split(self, seg_index: int, word_range: tuple[int, int], new_speaker_id: int):
        """Split segments[seg_index] so the given word range becomes its
        own sub-segment assigned to new_speaker_id. Produces up to three
        pieces: head (original speaker), range (new speaker), tail
        (original speaker). Time boundaries via linear interpolation over
        word count (see _show_split_menu note). Persists by rewriting the
        diarized JSON segments and saving."""
        segments = self.transcript.get("segments", [])
        if seg_index < 0 or seg_index >= len(segments):
            return
        seg = segments[seg_index]
        words = seg.get("text", "").split()
        start_word, end_word = word_range
        if not (0 <= start_word <= end_word < len(words)):
            return

        self._history.append(copy.deepcopy(self.transcript))

        seg_start = seg.get("start", 0.0)
        seg_end = seg.get("end", seg_start)
        duration = max(seg_end - seg_start, 0.001)
        total_words = float(len(words))
        range_start_time = seg_start + (start_word / total_words) * duration
        range_end_time = seg_start + ((end_word + 1) / total_words) * duration

        orig_speaker = seg.get("speaker_id", 0)
        replacement: list[dict] = []
        if start_word > 0:
            replacement.append({
                "start": seg_start,
                "end": range_start_time,
                "speaker_id": orig_speaker,
                "text": " ".join(words[0:start_word]),
            })
        replacement.append({
            "start": range_start_time,
            "end": range_end_time,
            "speaker_id": new_speaker_id,
            "text": " ".join(words[start_word:end_word + 1]),
        })
        if end_word < len(words) - 1:
            replacement.append({
                "start": range_end_time,
                "end": seg_end,
                "speaker_id": orig_speaker,
                "text": " ".join(words[end_word + 1:]),
            })

        new_segments = segments[:seg_index] + replacement + segments[seg_index + 1:]
        self.transcript["segments"] = new_segments

        # Ensure the new speaker has a default name entry if it's brand new.
        names = self.transcript.setdefault("speaker_names", {})
        if str(new_speaker_id) not in names:
            names[str(new_speaker_id)] = f"Speaker {new_speaker_id + 1}"

        self._speaker_ids = sorted(set(s.get("speaker_id", 0) for s in new_segments))

        # Enroll the reassigned range as a voice sample for the speaker —
        # cleaner provenance than a whole-segment sample.
        self._enroll_speaker(
            self._speaker_name(new_speaker_id), self.audio_path,
            range_start_time, range_end_time,
        )

        self._save_transcript()
        self._refresh_ui()

    # ------------------------------------------------------------------
    # Copy All
    # ------------------------------------------------------------------
    def _copy_all(self):
        """Copy the full speaker-labelled transcript to the clipboard."""
        lines: list[str] = []
        for seg in self.transcript.get("segments", []):
            ts = f"[{_format_time(seg.get('start', 0.0))}]"
            text = seg.get("text", "")
            if self._has_speakers:
                name = self._speaker_name(seg.get("speaker_id", 0))
                lines.append(f"{ts} {name}: {text}")
            else:
                lines.append(f"{ts} {text}")
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText("\n\n".join(lines))

    def _rename_speaker(self, speaker_id: int):
        current = self._speaker_name(speaker_id)

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
        self._populate_stats()
        self._undo_btn.setEnabled(bool(self._history))
        if hasattr(self, "_recluster_btn"):
            self._recluster_btn.setVisible(self._has_user_named_speakers())

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
        for target_id in self._speaker_ids:
            if target_id == source_id:
                continue
            target_name = self._speaker_name(target_id)
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
                # Worker thread: run the subprocess only. All widget/state
                # updates happen in _on_reprocess_done on the GUI thread.
                try:
                    cmd = [
                        sys.executable, str(script),
                        "rediarize", self.json_path,
                        "--n-speakers", str(n_speakers),
                    ]
                    subprocess.run(cmd, capture_output=True, check=True)
                    self._reprocess_done.emit(True, "")
                except Exception as e:
                    print(f"Re-diarize failed: {e}")
                    self._reprocess_done.emit(False, "Re-diarize failed")

            threading.Thread(target=_run, daemon=True).start()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Re-diarize failed:\n{e}")

    @pyqtSlot(bool, str)
    def _on_reprocess_done(self, success: bool, failure_title: str):
        """GUI-thread completion for re-diarize / re-cluster workers."""
        if not success:
            self.setWindowTitle(failure_title or "Reprocess failed")
            return
        self._load_transcript()
        self._speaker_ids = sorted(set(
            s.get("speaker_id", 0) for s in self.transcript.get("segments", [])
        ))
        self._refresh_ui()
        self.setWindowTitle(f"Transcript — {self.transcript.get('audio_file', '')}")

    def _recluster(self):
        """Re-cluster the transcript using user-named segments as anchors
        (Layer 2 of the voice-training plan). Treats every segment whose
        speaker has been renamed away from the default 'Speaker N' as an
        anchor centroid and re-assigns the rest to their closest match.

        Mirrors _rediarize's threading + progress + reload pattern, but
        calls transcribe.py's 'recluster-with-anchors' subcommand."""
        if not self._has_user_named_speakers():
            QMessageBox.information(
                self, "Re-cluster",
                "Rename at least one speaker first — your named speakers are used "
                "as anchors to re-assign the rest of the transcript.",
            )
            return
        try:
            repo_root = Path(__file__).resolve().parent.parent.parent
            script = repo_root / "transcription-pipeline" / "transcribe.py"
            if not script.exists():
                QMessageBox.warning(self, "Error", "Transcription script not found")
                return

            self.setWindowTitle("Re-clustering from labels…")
            import threading

            def _run():
                # Worker thread: run the subprocess only. All widget/state
                # updates happen in _on_reprocess_done on the GUI thread.
                try:
                    cmd = [
                        sys.executable, str(script),
                        "recluster-with-anchors", self.json_path,
                    ]
                    subprocess.run(cmd, capture_output=True, check=True)
                    self._reprocess_done.emit(True, "")
                except Exception as e:
                    print(f"Re-cluster failed: {e}")
                    self._reprocess_done.emit(False, "Re-cluster failed")

            threading.Thread(target=_run, daemon=True).start()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Re-cluster failed:\n{e}")
