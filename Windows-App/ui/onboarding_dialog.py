"""First-run onboarding wizard for HiDock Tools (Windows)."""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


class OnboardingDialog(QDialog):
    """Five-step onboarding wizard shown on first launch."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Welcome to HiDock")
        self.setFixedSize(560, 480)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        self.selected_mic: str = ""
        self.completed: bool = False
        self._hidock_connected = False
        self._model_downloading = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Stacked widget for pages
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_welcome_page())
        self._stack.addWidget(self._build_connect_page())
        self._stack.addWidget(self._build_mic_page())
        self._stack.addWidget(self._build_model_page())
        self._stack.addWidget(self._build_done_page())
        root.addWidget(self._stack, stretch=1)

        # Step indicator dots
        dots_row = QHBoxLayout()
        dots_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dots: list[QLabel] = []
        for _ in range(5):
            dot = QLabel("\u25cf")
            dot.setFixedWidth(18)
            dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
            dots_row.addWidget(dot)
            self._dots.append(dot)
        root.addLayout(dots_row)

        # Navigation bar
        nav = QHBoxLayout()
        nav.setContentsMargins(24, 8, 24, 16)

        self._back_btn = QPushButton("Back")
        self._back_btn.clicked.connect(self._go_back)
        nav.addWidget(self._back_btn)

        nav.addStretch()

        self._skip_btn = QPushButton("Skip")
        self._skip_btn.clicked.connect(self._go_next)
        nav.addWidget(self._skip_btn)

        nav.addStretch()

        self._next_btn = QPushButton("Get Started")
        self._next_btn.setObjectName("accentButton")
        self._next_btn.clicked.connect(self._on_next_clicked)
        nav.addWidget(self._next_btn)

        root.addLayout(nav)

        # Poll timer for HiDock connection (step 2)
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_hidock)
        self._poll_timer.start(2000)

        self._update_nav()

    # ── Page builders ──────────────────────────────────────────────────

    @staticmethod
    def _make_page(icon_text: str, title: str, description: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 24, 40, 8)
        layout.setSpacing(12)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()

        icon = QLabel(icon_text)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_font = QFont()
        icon_font.setPointSize(48)
        icon.setFont(icon_font)
        layout.addWidget(icon)

        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_font = QFont()
        title_font.setPointSize(18)
        title_font.setBold(True)
        title_label.setFont(title_font)
        layout.addWidget(title_label)

        desc = QLabel(description)
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc.setWordWrap(True)
        desc.setMaximumWidth(420)
        desc.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        desc.setStyleSheet("color: #888;")
        layout.addWidget(desc, alignment=Qt.AlignmentFlag.AlignCenter)

        return page, layout

    def _build_welcome_page(self) -> QWidget:
        page, layout = self._make_page(
            "\U0001f3a4",  # microphone emoji
            "Welcome to HiDock",
            "HiDock Tools helps you get the most from your HiDock device. "
            "It can sync your recordings, transcribe them with AI, and monitor "
            "your microphone so recordings start and stop automatically.",
        )
        layout.addStretch()
        return page

    def _build_connect_page(self) -> QWidget:
        page, layout = self._make_page(
            "\U0001f50c",  # plug emoji
            "Connect your HiDock",
            "Plug your HiDock into this computer using a USB cable. "
            "We'll detect it automatically.",
        )

        self._connect_status = QLabel("Waiting for HiDock...")
        self._connect_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._connect_status.setStyleSheet("color: #888;")
        layout.addWidget(self._connect_status)

        layout.addStretch()
        return page

    def _build_mic_page(self) -> QWidget:
        page, layout = self._make_page(
            "\U0001f399\ufe0f",  # studio microphone emoji
            "Choose your microphone",
            "Select the microphone you use for meetings and calls. "
            "HiDock Tools watches this mic to know when you're recording, "
            "so it can start and stop automatically.",
        )

        self._mic_combo = QComboBox()
        self._mic_combo.setMinimumWidth(280)
        try:
            from core.mic_trigger import list_audio_input_devices
            devices = list_audio_input_devices()
            self._mic_combo.addItems(devices)
        except Exception:
            self._mic_combo.addItem("(No microphones found)")
        layout.addWidget(self._mic_combo, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()
        return page

    def _build_model_page(self) -> QWidget:
        page, layout = self._make_page(
            "\u2b07\ufe0f",  # down arrow emoji
            "Speech Recognition",
            "To transcribe your recordings, HiDock Tools needs to download a "
            "speech recognition model. This is about 550 MB and only needs to "
            "happen once. You can skip this and do it later.",
        )

        self._model_status = QLabel("")
        self._model_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._model_status)

        self._model_progress = QProgressBar()
        self._model_progress.setMaximumWidth(300)
        self._model_progress.setVisible(False)
        layout.addWidget(self._model_progress, alignment=Qt.AlignmentFlag.AlignCenter)

        self._download_btn = QPushButton("Download Now")
        self._download_btn.clicked.connect(self._start_model_download)
        layout.addWidget(self._download_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # Check if model already exists
        try:
            from core.config import whisper_model_ready
            if whisper_model_ready():
                self._model_status.setText("\u2705 Speech recognition model is ready!")
                self._model_status.setStyleSheet("color: #a6e3a1;")
                self._download_btn.setVisible(False)
        except Exception:
            pass

        layout.addStretch()
        return page

    def _build_done_page(self) -> QWidget:
        page, layout = self._make_page(
            "\u2705",  # checkmark emoji
            "You're all set!",
            "Here's what's ready:",
        )

        self._done_hidock_label = QLabel("")
        self._done_mic_label = QLabel("")
        self._done_model_label = QLabel("")

        for label in (self._done_hidock_label, self._done_mic_label, self._done_model_label):
            label.setStyleSheet("font-size: 13px;")
            layout.addWidget(label, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()
        return page

    # ── Navigation ─────────────────────────────────────────────────────

    def _current_index(self) -> int:
        return self._stack.currentIndex()

    def _go_back(self):
        idx = self._current_index()
        if idx > 0:
            self._stack.setCurrentIndex(idx - 1)
            self._update_nav()

    def _go_next(self):
        idx = self._current_index()
        if idx < self._stack.count() - 1:
            if idx == 2:
                # Save mic selection from step 3
                self.selected_mic = self._mic_combo.currentText()
            if idx == 3:
                # Moving past model page — update done page
                pass
            self._stack.setCurrentIndex(idx + 1)
            if idx + 1 == 4:
                self._update_done_page()
            self._update_nav()

    def _on_next_clicked(self):
        idx = self._current_index()
        if idx == 4:
            # Final step — finish
            self.selected_mic = self._mic_combo.currentText()
            self.completed = True
            self.accept()
        else:
            self._go_next()

    def _update_nav(self):
        idx = self._current_index()
        total = self._stack.count()

        self._back_btn.setVisible(idx > 0)
        self._skip_btn.setVisible(idx > 0 and idx < total - 1)

        if idx == 0:
            self._next_btn.setText("Get Started")
        elif idx == total - 1:
            self._next_btn.setText("Start Using HiDock")
        else:
            self._next_btn.setText("Next")

        # Update dots
        for i, dot in enumerate(self._dots):
            if i == idx:
                dot.setStyleSheet("color: #89b4fa; font-size: 14px;")
            else:
                dot.setStyleSheet("color: #444; font-size: 14px;")

    # ── HiDock polling ─────────────────────────────────────────────────

    def _poll_hidock(self):
        if self._hidock_connected:
            return
        if self._current_index() != 1:
            return
        try:
            from core.usb_sync import extractor_ready
            ready, _ = extractor_ready()
            if ready:
                self._hidock_connected = True
                self._connect_status.setText("\u2705 HiDock connected!")
                self._connect_status.setStyleSheet("color: #a6e3a1; font-weight: bold;")
                # Auto-advance after 1 second
                QTimer.singleShot(1000, self._auto_advance_from_connect)
        except Exception:
            pass

    def _auto_advance_from_connect(self):
        if self._current_index() == 1:
            self._go_next()

    # ── Model download ─────────────────────────────────────────────────

    def _start_model_download(self):
        if self._model_downloading:
            return
        self._model_downloading = True
        self._download_btn.setEnabled(False)
        self._download_btn.setText("Downloading...")
        self._model_progress.setVisible(True)
        self._model_progress.setMaximum(100)
        self._model_progress.setValue(0)

        import threading

        def _worker():
            try:
                from core.model_download import download_model

                def _on_progress(downloaded: int, total: int):
                    if total > 0:
                        pct = int(downloaded * 100 / total)
                        QTimer.singleShot(0, lambda p=pct, d=downloaded, t=total: self._update_model_progress(p, d, t))

                def _on_complete():
                    QTimer.singleShot(0, self._on_model_complete)

                def _on_error(msg: str):
                    QTimer.singleShot(0, lambda: self._on_model_error(msg))

                download_model(
                    on_progress=_on_progress,
                    on_complete=_on_complete,
                    on_error=_on_error,
                )
            except Exception as e:
                QTimer.singleShot(0, lambda: self._on_model_error(str(e)))

        threading.Thread(target=_worker, daemon=True).start()

    def _update_model_progress(self, pct: int, downloaded: int = 0, total: int = 0):
        self._model_progress.setValue(pct)
        if total > 0:
            mb_done = downloaded / (1024 * 1024)
            mb_total = total / (1024 * 1024)
            self._download_btn.setText(f"{mb_done:.0f} / {mb_total:.0f} MB — {pct}%")
        else:
            self._download_btn.setText(f"Downloading... {pct}%")

    def _on_model_complete(self):
        self._model_downloading = False
        self._model_progress.setValue(100)
        self._download_btn.setVisible(False)
        self._model_status.setText("\u2705 Speech recognition model is ready!")
        self._model_status.setStyleSheet("color: #a6e3a1;")

    def _on_model_error(self, msg: str):
        self._model_downloading = False
        self._download_btn.setEnabled(True)
        self._download_btn.setText("Download Now")
        self._model_progress.setVisible(False)
        self._model_status.setText(f"Download failed: {msg}")
        self._model_status.setStyleSheet("color: #f38ba8;")

    # ── Done page ──────────────────────────────────────────────────────

    def _update_done_page(self):
        if self._hidock_connected:
            self._done_hidock_label.setText("\u2705  HiDock connected")
            self._done_hidock_label.setStyleSheet("color: #a6e3a1; font-size: 13px;")
        else:
            self._done_hidock_label.setText("\u25cb  HiDock not connected (you can connect later)")
            self._done_hidock_label.setStyleSheet("color: #888; font-size: 13px;")

        mic = self._mic_combo.currentText()
        if mic and mic != "(No microphones found)":
            self._done_mic_label.setText(f"\u2705  Microphone: {mic}")
            self._done_mic_label.setStyleSheet("color: #a6e3a1; font-size: 13px;")
        else:
            self._done_mic_label.setText("\u25cb  No microphone selected (you can choose later)")
            self._done_mic_label.setStyleSheet("color: #888; font-size: 13px;")

        try:
            from core.config import whisper_model_ready
            if whisper_model_ready():
                self._done_model_label.setText("\u2705  Speech recognition ready")
                self._done_model_label.setStyleSheet("color: #a6e3a1; font-size: 13px;")
            else:
                self._done_model_label.setText("\u25cb  Speech model not downloaded (you can download later)")
                self._done_model_label.setStyleSheet("color: #888; font-size: 13px;")
        except Exception:
            self._done_model_label.setText("\u25cb  Speech model not downloaded (you can download later)")
            self._done_model_label.setStyleSheet("color: #888; font-size: 13px;")
