"""Voice Training dialog for Windows.

Mirrors the macOS ``VoiceTrainingView.swift``: scans diarized transcripts,
clusters recurring voices across meetings, and presents each cluster for
review. Per cluster you see the AI-suggested name (editable), confidence,
talk time, sample/meeting counts, enrolled state, and an expandable list of
samples. Each sample can be played back, shows its quality score and source
meeting, and can be reassigned to another enrolled speaker. Confirming a
cluster enrolls its samples into the voice library under the chosen name and
marks the cluster reviewed (persisted via the shared review-state file).

Backend (source of truth) lives in ``shared/voice_training.py`` and
``shared/voice_library_lite.py``. The scan is expensive (loads audio +
runs the speaker-embedding ONNX model), so it is executed as a subprocess
inside a daemon thread and the JSON it prints (``export_for_ui``) is parsed
on completion — never blocking the UI. This mirrors transcript_viewer's
``_rediarize`` threading idiom and voice_library_dialog's subprocess idiom.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

from PyQt6.QtCore import Qt, QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

# Catppuccin palette (matching the rest of the Windows app)
COLOR_INDIGO = "#cba6f7"
COLOR_SECONDARY = "#a6adc8"
COLOR_ACCENT = "#89b4fa"
COLOR_GREEN = "#a6e3a1"
COLOR_ORANGE = "#fab387"
COLOR_RED = "#f38ba8"
COLOR_YELLOW = "#f9e2af"


def _shared_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "shared"


def _format_time(seconds: float) -> str:
    total = int(seconds)
    m, s = divmod(total, 60)
    return f"{m}:{s:02d}"


def _format_duration(seconds: float) -> str:
    """Compact talk-time formatting, mirroring the macOS formatDuration."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    m = seconds // 60
    rem = seconds % 60
    return f"{m}min" if rem == 0 else f"{m}m{rem}s"


class _ScanWorker(QObject):
    """Runs the (slow) voice_training scan in a background thread.

    voice_training.py's ``__main__`` block scans meetings, clusters, and
    prints ``export_for_ui(clusters)`` as JSON to stdout. We shell out to it
    so the heavy audio/ONNX work happens out of process and off the UI
    thread, then parse the JSON. Emits ``finished`` with the cluster list
    (or an empty list on failure) plus an optional error string.
    """

    finished = pyqtSignal(list, str)

    def run(self):
        clusters: list[dict] = []
        error = ""
        try:
            script = _shared_dir() / "voice_training.py"
            if not script.exists():
                self.finished.emit([], "voice_training.py not found")
                return
            # Run from the repo root so `from shared...` imports resolve.
            repo_root = _shared_dir().parent
            result = subprocess.run(
                [sys.executable, str(script)],
                capture_output=True,
                text=True,
                cwd=str(repo_root),
                timeout=600,
            )
            if result.returncode != 0:
                error = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "scan failed"
            stdout = result.stdout.strip()
            if stdout:
                try:
                    clusters = json.loads(stdout)
                except json.JSONDecodeError:
                    error = error or "could not parse scan output"
        except subprocess.TimeoutExpired:
            error = "scan timed out"
        except Exception as e:  # pragma: no cover - defensive
            error = str(e)
        self.finished.emit(clusters, error)


def _run_voice_library(args: list[str]) -> str | None:
    """Run voice_library_lite.py and return stdout (None on failure)."""
    script = _shared_dir() / "voice_library_lite.py"
    if not script.exists():
        return None
    try:
        result = subprocess.run(
            [sys.executable, str(script)] + args,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception as e:
        print(f"voice_library_lite error: {e}")
        return None


class VoiceTrainingDialog(QDialog):
    """Voice Training window — review discovered speaker clusters and enroll.

    Public API:
        VoiceTrainingDialog(parent=None)
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Voice Training")
        self.setMinimumSize(700, 500)
        self.resize(700, 600)

        # cluster_id -> dict (live model the UI mutates)
        self.clusters: list[dict] = []
        self._loading = True
        self._scan_error = ""

        # Audio playback (mirrors transcript_viewer)
        self._player = QMediaPlayer()
        self._audio_output = QAudioOutput()
        self._player.setAudioOutput(self._audio_output)
        self._playing_sample_id: str | None = None
        self._stop_timer: QTimer | None = None
        self._play_buttons: dict[str, QPushButton] = {}

        # Background scan worker (kept referenced so it isn't GC'd)
        self._worker: _ScanWorker | None = None
        self._worker_thread: threading.Thread | None = None

        self._build_chrome()
        self._start_scan()

    # ------------------------------------------------------------------
    # Chrome (persistent header + body container)
    # ------------------------------------------------------------------
    def _build_chrome(self):
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)

        # Header bar
        header = QHBoxLayout()
        header.setContentsMargins(16, 10, 16, 10)

        title = QLabel("Voice Training")
        title.setFont(QFont("", 14, QFont.Weight.Bold.value))
        title.setStyleSheet(f"color: {COLOR_INDIGO};")
        header.addWidget(title)
        header.addStretch()

        self._status_label = QLabel("")
        self._status_label.setStyleSheet(f"color: {COLOR_SECONDARY}; font-size: 12px;")
        header.addWidget(self._status_label)

        self._scan_btn = QPushButton("Scan")
        self._scan_btn.clicked.connect(self._start_scan)
        header.addWidget(self._scan_btn)

        header_widget = QWidget()
        header_widget.setLayout(header)
        header_widget.setStyleSheet("background: rgba(128,128,128,0.08);")
        self._root.addWidget(header_widget)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: rgba(128,128,128,0.25);")
        self._root.addWidget(sep)

        # Body — a scroll area whose inner widget we rebuild on each refresh.
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._root.addWidget(self._scroll, stretch=1)

        self._render_body()

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------
    def _start_scan(self):
        self._loading = True
        self._scan_error = ""
        self._scan_btn.setEnabled(False)
        self._render_body()

        self._worker = _ScanWorker()
        self._worker.finished.connect(self._on_scan_finished)
        self._worker_thread = threading.Thread(target=self._worker.run, daemon=True)
        self._worker_thread.start()

    def _on_scan_finished(self, clusters: list, error: str):
        # Merge persisted confirmed-state with whatever the backend reports
        # (the backend already applies it, but be defensive).
        self.clusters = clusters or []
        self._scan_error = error or ""
        self._loading = False
        self._scan_btn.setEnabled(True)
        self._render_body()

    # ------------------------------------------------------------------
    # Body rendering
    # ------------------------------------------------------------------
    def _render_body(self):
        self._play_buttons.clear()
        self._stop_playback()

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.setSpacing(12)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Update header status line
        if self._loading:
            self._status_label.setText("Scanning meetings…")
        else:
            n = len(self.clusters)
            meetings = len({m for c in self.clusters for m in c.get("meetings", [])})
            unconfirmed = sum(1 for c in self.clusters if not c.get("confirmed"))
            parts = [f"{n} voice{'s' if n != 1 else ''}", f"{meetings} meeting{'s' if meetings != 1 else ''}"]
            if unconfirmed:
                parts.insert(0, f"{unconfirmed} need review")
            self._status_label.setText(" · ".join(parts))

        if self._loading:
            msg = QLabel("Scanning meetings…\nExtracting and clustering speaker samples.")
            msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
            msg.setStyleSheet(f"color: {COLOR_SECONDARY}; font-size: 13px;")
            layout.addWidget(msg, stretch=1)
            self._scroll.setWidget(body)
            return

        if not self.clusters:
            text = "No voice samples found.\n\nTranscribe meetings with Speaker Labels enabled, then scan again."
            if self._scan_error:
                text += f"\n\n({self._scan_error})"
            empty = QLabel(text)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet(f"color: {COLOR_SECONDARY}; font-size: 13px;")
            layout.addWidget(empty, stretch=1)
            self._scroll.setWidget(body)
            return

        unconfirmed = [c for c in self.clusters if not c.get("confirmed")]
        confirmed = [c for c in self.clusters if c.get("confirmed")]

        if unconfirmed:
            layout.addWidget(self._section_header("Needs Review", len(unconfirmed), COLOR_ORANGE))
            for cluster in unconfirmed:
                layout.addWidget(self._cluster_card(cluster))
        if confirmed:
            layout.addWidget(self._section_header("Confirmed", len(confirmed), COLOR_GREEN))
            for cluster in confirmed:
                layout.addWidget(self._cluster_card(cluster))

        self._scroll.setWidget(body)

    def _section_header(self, title: str, count: int, color: str) -> QWidget:
        row = QHBoxLayout()
        row.setContentsMargins(0, 6, 0, 0)
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {color}; font-size: 12px;")
        row.addWidget(dot)
        label = QLabel(f"{title}")
        label.setStyleSheet("font-weight: 600; font-size: 13px;")
        row.addWidget(label)
        count_label = QLabel(f"({count})")
        count_label.setStyleSheet(f"color: {COLOR_SECONDARY}; font-size: 12px;")
        row.addWidget(count_label)
        row.addStretch()
        w = QWidget()
        w.setLayout(row)
        return w

    # ------------------------------------------------------------------
    # Cluster card
    # ------------------------------------------------------------------
    def _cluster_card(self, cluster: dict) -> QWidget:
        confirmed = bool(cluster.get("confirmed"))
        accent = COLOR_GREEN if confirmed else COLOR_ORANGE

        card = QWidget()
        card.setStyleSheet(
            f"QWidget#card {{ border: 1px solid {accent}; border-radius: 10px; "
            f"background: rgba(128,128,128,0.04); }}"
        )
        card.setObjectName("card")
        outer = QVBoxLayout(card)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        # ---- top row: status icon, name (editable), stats, confirm ----
        top = QHBoxLayout()
        top.setSpacing(8)

        status = QLabel("✓" if confirmed else "?")
        status.setStyleSheet(f"color: {accent}; font-size: 16px; font-weight: bold;")
        top.addWidget(status)

        suggested = cluster.get("suggested_name") or ""
        name_edit = QLineEdit(suggested)
        name_edit.setPlaceholderText("Unknown Voice")
        name_edit.setFixedWidth(200)
        name_edit.editingFinished.connect(
            lambda c=cluster, e=name_edit: self._set_cluster_name(c, e.text())
        )
        top.addWidget(name_edit)

        # Quick-assign to an already-enrolled speaker
        enrolled_speakers = cluster.get("enrolled_speakers", [])
        if enrolled_speakers:
            assign_btn = QPushButton("Known…")
            assign_btn.setToolTip("Assign this voice to an enrolled speaker")
            assign_btn.clicked.connect(
                lambda checked, c=cluster, b=None: self._show_known_menu(c, name_edit)
            )
            top.addWidget(assign_btn)

        conf = cluster.get("confidence", 0) or 0
        if conf > 0:
            conf_label = QLabel(f"{int(conf * 100)}%")
            conf_label.setToolTip("Match confidence")
            conf_label.setStyleSheet(f"color: {COLOR_SECONDARY}; font-size: 11px;")
            top.addWidget(conf_label)

        top.addStretch()

        stats = QLabel(
            f"{cluster.get('meeting_count', 0)} mtg · "
            f"{cluster.get('sample_count', 0)} samples · "
            f"{_format_duration(cluster.get('total_talk_time', 0))}"
        )
        stats.setStyleSheet(f"color: {COLOR_SECONDARY}; font-size: 11px;")
        top.addWidget(stats)

        if cluster.get("enrolled"):
            enrolled_badge = QLabel("enrolled")
            enrolled_badge.setStyleSheet(f"color: {COLOR_GREEN}; font-size: 11px; font-weight: 600;")
            top.addWidget(enrolled_badge)

        if not confirmed:
            confirm_btn = QPushButton("Confirm")
            confirm_btn.setStyleSheet(
                f"QPushButton {{ color: {COLOR_GREEN}; font-weight: 600; }}"
            )
            confirm_btn.setEnabled(bool(cluster.get("suggested_name")))
            confirm_btn.clicked.connect(lambda checked, c=cluster: self._confirm_cluster(c))
            top.addWidget(confirm_btn)

        top_w = QWidget()
        top_w.setLayout(top)
        outer.addWidget(top_w)

        # ---- samples (scrollable list within the card) ----
        samples = cluster.get("samples", [])
        if samples:
            samples_container = QWidget()
            s_layout = QVBoxLayout(samples_container)
            s_layout.setContentsMargins(20, 0, 0, 0)
            s_layout.setSpacing(4)
            for sample in samples:
                s_layout.addWidget(self._sample_row(sample, cluster))

            # If there are many samples, wrap them in a bounded scroll area.
            if len(samples) > 4:
                inner = QScrollArea()
                inner.setWidgetResizable(True)
                inner.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                inner.setMaximumHeight(180)
                inner.setStyleSheet("QScrollArea { border: none; }")
                inner.setWidget(samples_container)
                outer.addWidget(inner)
            else:
                outer.addWidget(samples_container)

        return card

    # ------------------------------------------------------------------
    # Sample row
    # ------------------------------------------------------------------
    def _sample_row(self, sample: dict, cluster: dict) -> QWidget:
        row = QHBoxLayout()
        row.setSpacing(8)
        row.setContentsMargins(0, 0, 0, 0)

        start = sample.get("start", 0.0)
        end = sample.get("end", start + 5.0)
        meeting_file = sample.get("meeting_file", "")
        sample_id = f"{sample.get('meeting_name', '')}-{start}"

        play_btn = QPushButton("▶")
        play_btn.setFixedSize(24, 24)
        play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        play_btn.setStyleSheet(
            f"QPushButton {{ border: none; font-size: 13px; color: {COLOR_ACCENT}; }}"
        )
        play_btn.clicked.connect(
            lambda checked, p=meeting_file, s=start, e=end, sid=sample_id: self._play_sample(p, s, e, sid)
        )
        self._play_buttons[sample_id] = play_btn
        row.addWidget(play_btn)

        # Meeting / context label + text preview
        info = QVBoxLayout()
        info.setSpacing(1)
        meta = QHBoxLayout()
        meta.setSpacing(6)
        meeting_label = QLabel(sample.get("meeting_name", ""))
        meeting_label.setStyleSheet("font-size: 11px; font-weight: 600;")
        meta.addWidget(meeting_label)
        ts_label = QLabel(f"[{_format_time(start)}]")
        ts_label.setStyleSheet(f"color: {COLOR_SECONDARY}; font-size: 10px;")
        meta.addWidget(ts_label)
        quality = sample.get("quality_score", 0) or 0
        q_label = QLabel(f"q {quality:.2f}")
        q_label.setToolTip("Sample quality score")
        q_color = COLOR_YELLOW if quality > 0.8 else COLOR_SECONDARY
        q_label.setStyleSheet(f"color: {q_color}; font-size: 10px;")
        meta.addWidget(q_label)
        if quality > 0.8:
            star = QLabel("★")
            star.setStyleSheet(f"color: {COLOR_YELLOW}; font-size: 10px;")
            meta.addWidget(star)
        meta.addStretch()
        meta_w = QWidget()
        meta_w.setLayout(meta)
        info.addWidget(meta_w)

        preview = QLabel(sample.get("text_preview", ""))
        preview.setWordWrap(True)
        preview.setStyleSheet(f"color: {COLOR_SECONDARY}; font-size: 11px;")
        info.addWidget(preview)
        info_w = QWidget()
        info_w.setLayout(info)
        row.addWidget(info_w, stretch=1)

        # Reassign control
        reassign_btn = QPushButton("⤳")
        reassign_btn.setFixedSize(24, 24)
        reassign_btn.setToolTip("Reassign this sample")
        reassign_btn.setStyleSheet("QPushButton { border: none; font-size: 13px; }")
        reassign_btn.clicked.connect(
            lambda checked, s=sample, c=cluster, b=reassign_btn: self._show_reassign_menu(s, c, b)
        )
        row.addWidget(reassign_btn)

        w = QWidget()
        w.setLayout(row)
        return w

    # ------------------------------------------------------------------
    # Playback (mirrors transcript_viewer._play_segment)
    # ------------------------------------------------------------------
    def _play_sample(self, audio_path: str, start: float, end: float, sample_id: str):
        if self._playing_sample_id == sample_id:
            self._stop_playback()
            return

        self._stop_playback()
        if not audio_path or not Path(audio_path).exists():
            QMessageBox.information(self, "Audio missing", "The source audio file could not be found.")
            return

        self._player.setSource(QUrl.fromLocalFile(audio_path))
        self._player.setPosition(int(start * 1000))
        self._player.play()
        self._playing_sample_id = sample_id

        btn = self._play_buttons.get(sample_id)
        if btn is not None:
            btn.setText("■")
            btn.setStyleSheet(f"QPushButton {{ border: none; font-size: 13px; color: {COLOR_RED}; }}")

        duration_ms = max(200, int((end - start) * 1000))
        self._stop_timer = QTimer(self)
        self._stop_timer.setSingleShot(True)
        self._stop_timer.timeout.connect(self._stop_playback)
        self._stop_timer.start(duration_ms)

    def _stop_playback(self):
        if self._stop_timer is not None:
            self._stop_timer.stop()
            self._stop_timer = None
        self._player.stop()
        if self._playing_sample_id is not None:
            btn = self._play_buttons.get(self._playing_sample_id)
            if btn is not None:
                btn.setText("▶")
                btn.setStyleSheet(
                    f"QPushButton {{ border: none; font-size: 13px; color: {COLOR_ACCENT}; }}"
                )
        self._playing_sample_id = None

    # ------------------------------------------------------------------
    # Naming / enrolling / reassigning
    # ------------------------------------------------------------------
    def _set_cluster_name(self, cluster: dict, name: str):
        cluster["suggested_name"] = name.strip() or None
        # No re-render needed for the text itself; the Confirm button enabled
        # state would only update on next render, which is acceptable here.

    def _show_known_menu(self, cluster: dict, name_edit: QLineEdit):
        from PyQt6.QtGui import QCursor

        menu = QMenu(self)
        for name in cluster.get("enrolled_speakers", []):
            action = menu.addAction(name)
            action.triggered.connect(
                lambda checked, n=name, c=cluster, e=name_edit: self._assign_known(c, e, n)
            )
        menu.exec(QCursor.pos())

    def _assign_known(self, cluster: dict, name_edit: QLineEdit, name: str):
        name_edit.setText(name)
        cluster["suggested_name"] = name

    def _confirm_cluster(self, cluster: dict):
        name = (cluster.get("suggested_name") or "").strip()
        if not name:
            QMessageBox.information(self, "Name required", "Give this voice a name before confirming.")
            return

        samples = cluster.get("samples", [])
        # Enroll every sample under the chosen name (background, fire-and-forget
        # like transcript_viewer._enroll_speaker so the UI never blocks).
        for sample in samples:
            self._enroll_sample(name, sample.get("meeting_file", ""), sample.get("start", 0.0), sample.get("end", 0.0))

        # Persist review state via the shared backend (confirm_speaker).
        self._mark_confirmed(name)

        cluster["confirmed"] = True
        cluster["enrolled"] = True
        self._render_body()

    def _enroll_sample(self, name: str, audio_path: str, start: float, end: float):
        """Fire-and-forget enrollment via voice_library_lite.py."""
        if not name or not audio_path:
            return
        try:
            script = _shared_dir() / "voice_library_lite.py"
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

    def _mark_confirmed(self, name: str):
        """Persist confirmed review-state via shared.voice_training.confirm_speaker.

        Run in a daemon thread because it touches disk; cheap, but kept off the
        UI thread for consistency. confirm_speaker has no CLI, so we invoke it
        through a tiny python -c shim against the shared module.
        """
        def _run():
            try:
                repo_root = _shared_dir().parent
                subprocess.run(
                    [
                        sys.executable, "-c",
                        "import sys; from shared.voice_training import confirm_speaker; "
                        "confirm_speaker(sys.argv[1])",
                        name,
                    ],
                    cwd=str(repo_root),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                )
            except Exception as e:
                print(f"Failed to persist review state: {e}")

        threading.Thread(target=_run, daemon=True).start()

    def _show_reassign_menu(self, sample: dict, cluster: dict, button: QPushButton):
        menu = QMenu(self)

        enrolled = cluster.get("enrolled_speakers", [])
        if enrolled:
            header = menu.addAction("Assign to…")
            header.setEnabled(False)
            for name in enrolled:
                action = menu.addAction(name)
                action.triggered.connect(
                    lambda checked, n=name, s=sample: self._reassign_sample_to(s, n)
                )
            menu.addSeparator()

        new_action = menu.addAction("New person…")
        new_action.triggered.connect(lambda checked, s=sample: self._reassign_sample_new(s))

        menu.addSeparator()
        wrong_action = menu.addAction("Wrong cluster (remove)")
        wrong_action.triggered.connect(lambda checked, s=sample, c=cluster: self._remove_sample(c, s))

        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _reassign_sample_to(self, sample: dict, name: str):
        self._enroll_sample(name, sample.get("meeting_file", ""), sample.get("start", 0.0), sample.get("end", 0.0))
        QMessageBox.information(self, "Reassigned", f"Sample enrolled under '{name}'.")

    def _reassign_sample_new(self, sample: dict):
        name, ok = QInputDialog.getText(self, "New person", "Speaker name:")
        if not ok or not name.strip():
            return
        self._reassign_sample_to(sample, name.strip())

    def _remove_sample(self, cluster: dict, sample: dict):
        """Visually remove a mis-clustered sample (matches macOS 'Wrong cluster').

        The backend has no per-sample reassignment persistence beyond
        re-enrollment, so this only updates the in-memory model and re-renders.
        """
        sid = (sample.get("meeting_name", ""), sample.get("start"))
        cluster["samples"] = [
            s for s in cluster.get("samples", [])
            if (s.get("meeting_name", ""), s.get("start")) != sid
        ]
        cluster["sample_count"] = len(cluster["samples"])
        self._render_body()

    # ------------------------------------------------------------------
    def closeEvent(self, event):
        self._stop_playback()
        super().closeEvent(event)
