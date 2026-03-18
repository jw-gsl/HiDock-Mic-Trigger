"""HiDock Tools — Windows desktop application.

This is a Python/PyQt6 port of the macOS hidock-mic-trigger menu bar app.
See README.md for setup and PORTING.md for the macOS → Windows workflow.
"""
from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("HiDock")
    app.setOrganizationName("HiDock")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
