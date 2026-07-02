"""Embedded CLI / terminal pane.

PyQt6 port of the macOS ``TerminalPaneController`` (SwiftTerm-based). It
hosts a persistent interactive login shell so the user can authenticate
and drive CLIs (e.g. ``claude auth login``, ``claude <transcript>``)
without leaving the app, plus a display-only "summarise activity" feed.

Two backends, picked at runtime:

* **PTY (preferred)** — a real pseudo-terminal via ``pywinpty`` (ConPTY on
  Windows). Interactive prompts work, so ``claude auth login`` and friends
  behave like a normal terminal. ``pywinpty`` also runs on POSIX (it wraps
  ``ptyprocess`` there), so we use it everywhere it imports.
* **QProcess (fallback)** — when ``pywinpty`` isn't importable we fall back
  to a plain ``QProcess`` running the platform shell. stdin still works,
  but fully interactive CLIs that probe for a TTY may degrade.

The module must import cleanly even when ``pywinpty`` is absent — the
import is guarded and the backend is chosen lazily on first use.
"""
from __future__ import annotations

import os
import shlex
import sys
import threading

from PyQt6.QtCore import QProcess, pyqtSignal
from PyQt6.QtGui import QFont, QTextCursor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

# Catppuccin palette (matches transcript_viewer / macOS app)
_MAUVE = "#cba6f7"
_SUBTEXT = "#a6adc8"
_BLUE = "#89b4fa"
_BG = "#1e1e2e"
_FG = "#cdd6f4"

# Detect the preferred PTY backend at import time, but tolerate its
# absence — merely importing this module must never require pywinpty.
try:  # pragma: no cover - depends on platform/availability
    import winpty as _winpty  # type: ignore

    _HAS_PYWINPTY = True
except Exception:  # noqa: BLE001 - any import failure means "no PTY backend"
    _winpty = None  # type: ignore
    _HAS_PYWINPTY = False


def _default_shell() -> tuple[str, list[str]]:
    """The platform login/interactive shell and its args.

    On Windows we use ``cmd.exe`` (no login flags). Elsewhere we use the
    user's ``$SHELL`` (defaulting to zsh) with ``-l -i`` so ~/.zprofile,
    /etc/paths and ~/.zshrc are sourced — the user's full PATH (brew, npm,
    nvm) is needed for ``claude`` to be found, mirroring the macOS pane.
    """
    if sys.platform == "win32":
        return os.environ.get("COMSPEC", "cmd.exe"), []
    shell = os.environ.get("SHELL", "/bin/zsh")
    return shell, ["-l", "-i"]


class TerminalPane(QWidget):
    """Embedded read/write console hosting a persistent shell.

    Public API:
      * ``run_command(command)`` — send a command line to the shell.
      * ``ask_claude(transcript_path)`` — run ``claude "<path>"``.
      * ``append_activity(text)`` — append a display-only feed line.
      * ``shutdown()`` — terminate the shell cleanly.
    """

    # PTY reader threads can't touch widgets directly; marshal output back
    # to the GUI thread via a signal.
    _output_ready = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._started = False
        self._backend: str | None = None  # "pty" | "qprocess" once started
        self._pty = None  # winpty.PtyProcess
        self._proc: QProcess | None = None
        self._reader_thread: threading.Thread | None = None
        self._reader_alive = False

        self._init_ui()
        self._output_ready.connect(self._append_output)

        # Wire cleanup to widget destruction so the shell doesn't outlive
        # the pane.
        self.destroyed.connect(lambda: self.shutdown())

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header strip
        header = QHBoxLayout()
        header.setContentsMargins(10, 6, 10, 6)
        title = QLabel("CLI")
        title.setStyleSheet(f"color: {_MAUVE}; font-weight: 600;")
        header.addWidget(title)
        header.addStretch()
        header_widget = QWidget()
        header_widget.setLayout(header)
        header_widget.setStyleSheet("background: rgba(128,128,128,0.08);")
        layout.addWidget(header_widget)

        # Output area — read-only, monospace, dark.
        self._output = QPlainTextEdit()
        self._output.setReadOnly(True)
        self._output.setFont(QFont("Menlo, Consolas, monospace", 12))
        self._output.setStyleSheet(
            f"QPlainTextEdit {{ background: {_BG}; color: {_FG}; "
            f"border: none; padding: 6px; }}"
        )
        self._output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self._output, stretch=1)

        # Input line.
        self._input = QLineEdit()
        self._input.setFont(QFont("Menlo, Consolas, monospace", 12))
        self._input.setPlaceholderText("Type a command and press Enter…")
        self._input.setStyleSheet(
            f"QLineEdit {{ background: {_BG}; color: {_BLUE}; "
            f"border: none; border-top: 1px solid rgba(128,128,128,0.25); "
            f"padding: 6px; }}"
        )
        self._input.returnPressed.connect(self._on_return_pressed)
        layout.addWidget(self._input)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def run_command(self, command: str) -> None:
        """Send ``command`` to the shell as if typed, echoing it."""
        self._ensure_started()
        line = command if command.endswith("\n") else command + "\n"
        self._send(line)

    def ask_claude(self, transcript_path: str) -> None:
        """Run ``claude "<transcript_path>"`` in the shell.

        Quoting is shell-specific: ``shlex.quote`` produces POSIX single
        quotes, which cmd.exe does not strip — the path would reach claude
        with literal quotes (and backslashes mangled). On Windows the shell
        is cmd.exe, so wrap in double quotes instead.
        """
        if sys.platform == "win32":
            quoted = f'"{transcript_path}"'
        else:
            quoted = shlex.quote(transcript_path)
        self.run_command(f"claude {quoted}")

    def append_activity(self, text: str) -> None:
        """Append a display-only line to the output (NOT shell stdin).

        Used for summarise progress markers. Visually distinguished with a
        dim "· " prefix so it can't be confused with shell output.
        """
        # Lazy start so the shell exists once the pane is in use, matching
        # the macOS controller's appendActivity → ensureStarted.
        self._ensure_started()
        self._append_output(f"· {text}\n", dim=True)

    def shutdown(self) -> None:
        """Terminate the shell/process cleanly. Idempotent."""
        self._reader_alive = False
        if self._pty is not None:
            try:
                if self._pty.isalive():
                    self._pty.terminate(force=True)
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass
            self._pty = None
        if self._proc is not None:
            try:
                self._proc.kill()
                self._proc.waitForFinished(1000)
            except Exception:  # noqa: BLE001
                pass
            self._proc = None
        self._started = False

    # ------------------------------------------------------------------
    # Backend lifecycle
    # ------------------------------------------------------------------
    def _ensure_started(self) -> None:
        """Start the shell lazily on first use (idempotent)."""
        if self._started:
            return
        self._started = True
        if _HAS_PYWINPTY:
            try:
                self._start_pty()
                self._backend = "pty"
                return
            except Exception as e:  # noqa: BLE001 - fall back gracefully
                self._append_output(
                    f"· PTY backend unavailable ({e}); using fallback shell\n",
                    dim=True,
                )
        self._start_qprocess()
        self._backend = "qprocess"

    def _start_pty(self) -> None:
        shell, args = _default_shell()
        env = dict(os.environ)
        env.setdefault("TERM", "xterm-256color")
        cmdline = " ".join([shell, *args]) if args else shell
        # winpty.PtyProcess.spawn takes an argv list.
        self._pty = _winpty.PtyProcess.spawn(  # type: ignore[union-attr]
            [shell, *args],
            env=env,
        )
        self._reader_alive = True
        self._reader_thread = threading.Thread(
            target=self._pty_reader_loop, daemon=True
        )
        self._reader_thread.start()
        _ = cmdline  # documented intent; argv form used above

    def _pty_reader_loop(self) -> None:
        while self._reader_alive and self._pty is not None:
            try:
                data = self._pty.read(1024)
            except EOFError:
                break
            except Exception:  # noqa: BLE001 - pty closed mid-read
                break
            if not data:
                break
            text = data if isinstance(data, str) else data.decode(
                "utf-8", "replace"
            )
            self._output_ready.emit(text)

    def _start_qprocess(self) -> None:
        shell, args = _default_shell()
        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(
            QProcess.ProcessChannelMode.MergedChannels
        )
        self._proc.readyReadStandardOutput.connect(self._on_qprocess_output)
        self._proc.start(shell, args)

    def _on_qprocess_output(self) -> None:
        if self._proc is None:
            return
        data = bytes(self._proc.readAllStandardOutput())
        if data:
            self._append_output(data.decode("utf-8", "replace"))

    # ------------------------------------------------------------------
    # I/O helpers
    # ------------------------------------------------------------------
    def _send(self, text: str) -> None:
        """Write ``text`` to the shell's stdin via the active backend."""
        if self._backend == "pty" and self._pty is not None:
            try:
                self._pty.write(text)
            except Exception:  # noqa: BLE001
                pass
        elif self._backend == "qprocess" and self._proc is not None:
            self._proc.write(text.encode("utf-8"))

    def _on_return_pressed(self) -> None:
        command = self._input.text()
        self._input.clear()
        # Echo to the output so the user sees what they ran (the PTY/shell
        # may also echo; harmless duplication is acceptable and matches the
        # "as if typed" contract).
        self.run_command(command)

    def _append_output(self, text: str, dim: bool = False) -> None:
        """Append text to the output area and scroll to the bottom.

        ``dim=True`` renders the line in the muted subtext colour (used for
        the display-only activity feed).
        """
        cursor = self._output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if dim:
            fmt = cursor.charFormat()
            from PyQt6.QtGui import QColor

            fmt.setForeground(QColor(_SUBTEXT))
            fmt.setFontItalic(True)
            cursor.setCharFormat(fmt)
            cursor.insertText(text)
            # Reset so subsequent shell output uses the default colour.
            fmt.setForeground(QColor(_FG))
            fmt.setFontItalic(False)
            cursor.setCharFormat(fmt)
        else:
            cursor.insertText(text)
        self._output.setTextCursor(cursor)
        self._output.ensureCursorVisible()

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        self.shutdown()
        super().closeEvent(event)
