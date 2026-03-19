"""HiDock Tools — Windows desktop application.

This is a Python/PyQt6 port of the macOS hidock-mic-trigger menu bar app.
See README.md for setup and PORTING.md for the macOS -> Windows workflow.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from ui.main_window import MainWindow


def _resource_path(relative: str) -> str:
    """Return absolute path to a bundled resource (works for dev and PyInstaller)."""
    if getattr(sys, "frozen", False):
        base = Path(sys._MEIPASS)  # type: ignore[attr-defined]
    else:
        base = Path(__file__).resolve().parent
    return str(base / relative)


def _load_stylesheet() -> str:
    """Load the QSS theme file."""
    qss_path = _resource_path(os.path.join("resources", "theme.qss"))
    try:
        with open(qss_path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("HiDock")
    app.setOrganizationName("HiDock")
    app.setQuitOnLastWindowClosed(False)

    # Application icon
    icon_path = _resource_path(os.path.join("resources", "icon.ico"))
    app_icon = QIcon(icon_path)
    app.setWindowIcon(app_icon)

    # Dark theme
    stylesheet = _load_stylesheet()
    if stylesheet:
        app.setStyleSheet(stylesheet)

    # System tray icon
    tray_icon = QSystemTrayIcon(app_icon, app)
    tray_menu = QMenu()

    show_action = tray_menu.addAction("Show / Hide")
    tray_menu.addSeparator()
    start_action = tray_menu.addAction("Start Trigger")
    stop_action = tray_menu.addAction("Stop Trigger")
    tray_menu.addSeparator()
    refresh_action = tray_menu.addAction("Refresh")
    tray_menu.addSeparator()
    quit_action = tray_menu.addAction("Quit")

    tray_icon.setContextMenu(tray_menu)
    tray_icon.setToolTip("HiDock Tools")
    tray_icon.show()

    # Main window
    window = MainWindow(tray_icon=tray_icon)
    window.show()

    # Wire tray actions
    show_action.triggered.connect(lambda: window.show() if window.isHidden() else window.hide())
    start_action.triggered.connect(window._start_trigger)
    stop_action.triggered.connect(window._stop_trigger)
    refresh_action.triggered.connect(window._refresh_status)

    def _quit():
        window._force_quit = True
        window.mic_trigger.stop()
        tray_icon.hide()
        app.quit()

    quit_action.triggered.connect(_quit)

    # Double-click tray icon to show
    tray_icon.activated.connect(
        lambda reason: window.show() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None
    )

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
