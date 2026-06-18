"""Presentational device-card strip for the Windows app.

Mirrors the macOS ``DeviceCardView`` / ``DeviceStripView`` (see
``hidock-mic-trigger/Sources/Views/DeviceCardView.swift``): one compact card per
paired HiDock / volume / Plaud account, plus a lightweight variant for imported
recordings. Each card collapses what used to be scattered widgets (status dot,
storage row, reconnect icon, filter chip) into a single panel so every fact
about a device sits in one place.

The widget is purely presentational. It takes a list of plain dicts via
:meth:`DeviceStrip.set_devices` and emits two signals — ``reconnect_requested``
and ``filter_toggled`` — both carrying the ``device_id``. The caller wires these
into the main window and feeds it fresh card data.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.device_icons import device_glyph_pixmap

# Catppuccin palette (matches the macOS app's state colours).
_GREEN = "#a6e3a1"       # connected
_ACCENT = "#89b4fa"      # active filter / accent
_RED = "#f38ba8"         # recording
_SECONDARY = "#a6adc8"   # muted text / not-connected
_INDIGO = "#cba6f7"      # plaud accent
_GREY = "#585b70"        # borders / inactive

_CARD_HEIGHT = 90
_ICON_SIZE = 40
_COLUMNS = 3

# Emoji fallbacks for SKUs the bespoke glyph loader can't render (and for the
# cloud/file pseudo-devices that have no product art).
_KIND_EMOJI = {
    "hidock": "\U0001F3A4",   # microphone
    "volume": "\U0001F4BE",   # floppy disk / drive
    "plaud": "☁️",  # cloud
    "imported": "\U0001F4C1",  # folder
}


class _DeviceCard(QFrame):
    """A single device card. Clicking the body toggles the filter."""

    reconnect_requested = pyqtSignal(str)
    filter_toggled = pyqtSignal(str)

    def __init__(self, card: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._device_id: str = card.get("device_id", "")
        self._filter_active: bool = bool(card.get("filter_active", False))

        self.setFixedHeight(_CARD_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_card_style()

        self._build(card)

    # -- styling ----------------------------------------------------------

    def _apply_card_style(self) -> None:
        border = _ACCENT if self._filter_active else _GREY
        width = 2 if self._filter_active else 1
        self.setStyleSheet(
            "_DeviceCard {"
            f" border: {width}px solid {border};"
            " border-radius: 8px;"
            " background-color: rgba(69, 71, 90, 0.35);"
            "}"
        )

    # -- construction -----------------------------------------------------

    def _build(self, card: dict) -> None:
        kind = card.get("kind", "hidock")
        name = card.get("name", "Device")
        connected = bool(card.get("connected", False))
        recording = bool(card.get("recording", False))
        recording_count = int(card.get("recording_count", 0))
        downloaded_count = int(card.get("downloaded_count", 0))
        storage_text = card.get("storage_text")

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(10)

        # Icon -----------------------------------------------------------
        icon = QLabel()
        icon.setFixedSize(_ICON_SIZE, _ICON_SIZE)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = device_glyph_pixmap(
            name,
            is_volume=(kind == "volume"),
            size=_ICON_SIZE,
            recording=recording,
        )
        if pixmap is not None:
            icon.setPixmap(pixmap)
        else:
            icon.setText(_KIND_EMOJI.get(kind, _KIND_EMOJI["hidock"]))
            icon.setStyleSheet("font-size: 24px;")
        row.addWidget(icon)

        # Centre column (name, chip, counts, optional storage) -----------
        centre = QVBoxLayout()
        centre.setSpacing(3)
        centre.setContentsMargins(0, 0, 0, 0)

        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        title_row.setContentsMargins(0, 0, 0, 0)
        name_label = QLabel(name)
        name_label.setFont(QFont("", 11, QFont.Weight.Bold.value))
        title_row.addWidget(name_label)
        title_row.addWidget(self._make_chip(connected, recording))
        title_row.addStretch()
        centre.addLayout(title_row)

        counts = QLabel(
            f"{recording_count} recording{'' if recording_count == 1 else 's'}"
            f" · {downloaded_count} downloaded"
        )
        counts.setStyleSheet(f"color: {_SECONDARY}; font-size: 11px;")
        centre.addWidget(counts)

        # The Windows extractor doesn't report storage yet, so storage_text is
        # usually None — omit the line gracefully when so.
        if storage_text:
            storage = QLabel(str(storage_text))
            storage.setStyleSheet(f"color: {_SECONDARY}; font-size: 10px;")
            centre.addWidget(storage)

        centre.addStretch()
        row.addLayout(centre, stretch=1)

        # Actions column (reconnect) -------------------------------------
        # Imported recordings are a virtual device — no hardware to reconnect.
        if kind != "imported":
            reconnect = QPushButton("↻")  # clockwise arrow
            reconnect.setToolTip(f"Reconnect {name}")
            reconnect.setCursor(Qt.CursorShape.PointingHandCursor)
            reconnect.setFixedSize(26, 26)
            reconnect.setStyleSheet(
                "QPushButton {"
                f" color: {_ACCENT}; border: none; background: transparent;"
                " font-size: 15px;"
                "}"
                "QPushButton:hover { color: #ffffff; }"
            )
            reconnect.clicked.connect(self._on_reconnect)
            row.addWidget(reconnect, alignment=Qt.AlignmentFlag.AlignTop)

    def _make_chip(self, connected: bool, recording: bool) -> QLabel:
        if recording:
            text, colour = "Recording", _RED
        elif connected:
            text, colour = "Connected", _GREEN
        else:
            text, colour = "Not connected", _SECONDARY
        chip = QLabel(text)
        chip.setStyleSheet(
            f"color: {colour};"
            f" border: 1px solid {colour};"
            " border-radius: 7px;"
            " padding: 1px 6px;"
            " font-size: 10px;"
            " font-weight: 600;"
        )
        return chip

    # -- interaction ------------------------------------------------------

    def _on_reconnect(self) -> None:
        self.reconnect_requested.emit(self._device_id)

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt override)
        # Whole-card click toggles the table filter. Clicking an already-active
        # card clears the filter (emit empty string).
        if event.button() == Qt.MouseButton.LeftButton:
            self.filter_toggled.emit("" if self._filter_active else self._device_id)
            event.accept()
            return
        super().mousePressEvent(event)


class DeviceStrip(QWidget):
    """Adaptive grid of device cards.

    Public API:
        - ``set_devices(cards: list[dict])`` — rebuild the strip.
        - ``reconnect_requested = pyqtSignal(str)`` — device_id to reconnect.
        - ``filter_toggled = pyqtSignal(str)`` — device_id to filter by, or ""
          to clear the active filter.
    """

    reconnect_requested = pyqtSignal(str)
    filter_toggled = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(8)
        self._cards: list[_DeviceCard] = []
        self.setVisible(False)

    def set_devices(self, cards: list[dict]) -> None:
        """Rebuild the card grid from the given list of card dicts."""
        self._clear()

        if not cards:
            # No devices — take up no space at all.
            self.setVisible(False)
            return

        for index, card in enumerate(cards):
            widget = _DeviceCard(card)
            widget.reconnect_requested.connect(self.reconnect_requested)
            widget.filter_toggled.connect(self.filter_toggled)
            row, col = divmod(index, _COLUMNS)
            self._grid.addWidget(widget, row, col)
            self._cards.append(widget)

        # Keep trailing columns flexible so cards stay left-aligned at the top
        # when a row is partially filled.
        for col in range(_COLUMNS):
            self._grid.setColumnStretch(col, 1)

        self.setVisible(True)

    def _clear(self) -> None:
        for widget in self._cards:
            self._grid.removeWidget(widget)
            widget.deleteLater()
        self._cards.clear()
