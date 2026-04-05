"""Device Manager dialog — manage paired HiDock and volume devices.

Mirrors the macOS DeviceManagerView. Shows all paired devices with
type, connection status, metadata, and forget (unpair) controls.
Supports searching, filtering by type, and sorting.
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.models import DeviceType, PairedDevice


class DeviceRowWidget(QWidget):
    """A single row showing one paired device."""

    forgetRequested = pyqtSignal(str)  # device_id

    def __init__(self, device: PairedDevice, connected: bool = False, parent=None):
        super().__init__(parent)
        self.device = device
        self._connected = connected

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)

        # Left: icon
        self.icon_label = QLabel()
        self.icon_label.setFixedWidth(24)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.icon_label)

        # Center: text content
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)

        # Name row
        name_row = QHBoxLayout()
        self.name_label = QLabel(device.display_name)
        self.name_label.setStyleSheet("font-weight: bold; font-size: 13px;")
        name_row.addWidget(self.name_label)

        if connected:
            conn_badge = QLabel("Connected")
            conn_badge.setStyleSheet(
                "color: #a6e3a1; font-size: 10px; background: rgba(166,227,161,0.15); "
                "padding: 1px 5px; border-radius: 3px;"
            )
            name_row.addWidget(conn_badge)

        name_row.addStretch()

        type_badge = QLabel("HiDock" if device.device_type == DeviceType.HIDOCK else "Volume")
        type_badge.setStyleSheet(
            "color: gray; font-size: 10px; background: rgba(128,128,128,0.15); "
            "padding: 1px 5px; border-radius: 3px;"
        )
        name_row.addWidget(type_badge)
        text_layout.addLayout(name_row)

        # Detail row
        details = []
        if device.device_type == DeviceType.HIDOCK:
            details.append(f"Product ID: {device.product_id}")
        if device.volume_name:
            details.append(f"Volume: {device.volume_name}")
        if device.subpath:
            details.append(f"Folder: {device.subpath}")
        if device.paired_at:
            details.append(f"Paired: {device.paired_at[:10]}")

        self.detail_label = QLabel(" · ".join(details))
        self.detail_label.setStyleSheet("color: gray; font-size: 11px;")
        text_layout.addWidget(self.detail_label)

        layout.addLayout(text_layout, stretch=1)

        # Right: forget button
        btn_layout = QVBoxLayout()
        btn_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        forget_btn = QPushButton("Forget")
        forget_btn.setFixedWidth(70)
        forget_btn.setStyleSheet("color: #f38ba8;")
        forget_btn.clicked.connect(lambda: self.forgetRequested.emit(device.device_id))
        btn_layout.addWidget(forget_btn)
        layout.addLayout(btn_layout)

        self._update_icon()

    def _update_icon(self):
        if self.device.device_type == DeviceType.VOLUME:
            icon = "\U0001F4BE"  # floppy disk / external drive
        elif "h1" in self.device.display_name.lower() or "dock" in self.device.display_name.lower():
            icon = "\U0001F50A"  # speaker
        elif "p1" in self.device.display_name.lower():
            icon = "\U0001F399"  # studio microphone
        else:
            icon = "\U0001F50C"  # plug
        self.icon_label.setText(icon)

    def update_device(self, device: PairedDevice, connected: bool):
        self.device = device
        self._connected = connected
        self.name_label.setText(device.display_name)
        self._update_icon()


class PairVolumeWidget(QWidget):
    """Inline widget for pairing a new USB volume."""

    pairRequested = pyqtSignal(str, str)  # volume_name, subpath
    scanRequested = pyqtSignal()  # emitted when user clicks Scan

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.scan_btn = QPushButton("Scan")
        self.scan_btn.setToolTip("Scan for mounted volumes with audio files")
        self.scan_btn.clicked.connect(self._on_scan)
        layout.addWidget(self.scan_btn)

        self.volume_combo = QComboBox()
        self.volume_combo.setEditable(True)
        self.volume_combo.setPlaceholderText("e.g. ZOOM_H1 or D")
        self.volume_combo.setFixedWidth(180)
        layout.addWidget(self.volume_combo)

        layout.addWidget(QLabel("Subfolder:"))
        self.subpath_input = QLineEdit()
        self.subpath_input.setPlaceholderText("(optional)")
        self.subpath_input.setFixedWidth(120)
        layout.addWidget(self.subpath_input)

        self.pair_btn = QPushButton("Pair Volume")
        self.pair_btn.clicked.connect(self._on_pair)
        layout.addWidget(self.pair_btn)

    def _on_scan(self):
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText("Scanning...")
        self.scanRequested.emit()

    def set_scan_results(self, volumes: list[dict]):
        """Populate combo box with scan results. Each dict has volumeName, audioFileCount."""
        self.volume_combo.clear()
        for vol in volumes:
            name = vol.get("volumeName", "")
            count = vol.get("audioFileCount", 0)
            self.volume_combo.addItem(f"{name} ({count} files)", name)
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("Scan")

    def _on_pair(self):
        # Get the volume name from the combo's current data or text
        idx = self.volume_combo.currentIndex()
        if idx >= 0:
            name = self.volume_combo.itemData(idx) or self.volume_combo.currentText().strip()
        else:
            name = self.volume_combo.currentText().strip()
        if not name:
            return
        sub = self.subpath_input.text().strip()
        self.pairRequested.emit(name, sub)
        self.volume_combo.setCurrentText("")
        self.subpath_input.clear()


class DeviceManagerDialog(QDialog):
    """Dialog for managing paired devices."""

    deviceForgotten = pyqtSignal(str)   # device_id — emitted so parent can update state
    volumePaired = pyqtSignal(str, str)  # volume_name, subpath

    def __init__(self, devices: list[PairedDevice], connected_ids: set[str] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Device Manager")
        self.setMinimumSize(580, 420)
        self.resize(620, 480)

        self._devices = list(devices)
        self._connected_ids = connected_ids or set()
        self._rows: dict[str, DeviceRowWidget] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QHBoxLayout()
        header.setContentsMargins(16, 12, 16, 8)
        title = QLabel("Device Manager")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        header.addWidget(title)
        header.addStretch()
        count_label = QLabel(f"{len(devices)} device{'s' if len(devices) != 1 else ''}")
        count_label.setStyleSheet("color: gray;")
        self._count_label = count_label
        header.addWidget(count_label)
        layout.addLayout(header)

        # Toolbar: search + filter + sort
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(16, 4, 16, 4)
        toolbar.setSpacing(8)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search devices...")
        self._search.setFixedWidth(180)
        self._search.textChanged.connect(self._rebuild)
        toolbar.addWidget(self._search)

        toolbar.addWidget(QLabel("Type:"))
        self._type_filter = QComboBox()
        self._type_filter.addItems(["All", "HiDock", "Volume"])
        self._type_filter.currentIndexChanged.connect(self._rebuild)
        toolbar.addWidget(self._type_filter)

        toolbar.addWidget(QLabel("Sort:"))
        self._sort = QComboBox()
        self._sort.addItems(["Name", "Type", "Paired"])
        self._sort.currentIndexChanged.connect(self._rebuild)
        toolbar.addWidget(self._sort)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        # Scrollable content area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(0)
        self._content_layout.addStretch()
        scroll.setWidget(self._content)
        layout.addWidget(scroll, stretch=1)

        # Empty state label
        self._empty_label = QLabel("No devices paired.\nUse \"Pair\" in the toolbar to connect a HiDock,\nor pair a USB volume below.")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setStyleSheet("color: gray; font-size: 13px;")
        self._content_layout.insertWidget(0, self._empty_label)

        # Footer: pair volume + close
        footer = QHBoxLayout()
        footer.setContentsMargins(16, 8, 16, 12)

        self.pair_widget = PairVolumeWidget()
        self.pair_widget.pairRequested.connect(self._on_pair_volume)
        footer.addWidget(self.pair_widget)

        footer.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        footer.addWidget(close_btn)
        layout.addLayout(footer)

        self._rebuild()

    def set_devices(self, devices: list[PairedDevice], connected_ids: set[str] | None = None):
        """Update the device list (e.g. after pairing/forgetting)."""
        self._devices = list(devices)
        self._connected_ids = connected_ids or self._connected_ids
        self._rebuild()

    def _rebuild(self):
        """Rebuild the device rows based on current filters."""
        # Clear existing rows
        for key in list(self._rows.keys()):
            w = self._rows.pop(key)
            self._content_layout.removeWidget(w)
            w.deleteLater()

        devices = self._filtered_devices()
        self._empty_label.setVisible(len(devices) == 0)
        self._count_label.setText(f"{len(self._devices)} device{'s' if len(self._devices) != 1 else ''}")

        for i, device in enumerate(devices):
            connected = device.device_id in self._connected_ids
            row = DeviceRowWidget(device, connected=connected)
            row.forgetRequested.connect(self._on_forget)
            self._rows[device.device_id] = row
            self._content_layout.insertWidget(i, row)

    def _filtered_devices(self) -> list[PairedDevice]:
        devices = list(self._devices)

        # Type filter
        type_idx = self._type_filter.currentIndex()
        if type_idx == 1:
            devices = [d for d in devices if d.device_type == DeviceType.HIDOCK]
        elif type_idx == 2:
            devices = [d for d in devices if d.device_type == DeviceType.VOLUME]

        # Search
        query = self._search.text().strip().lower()
        if query:
            devices = [d for d in devices if query in d.display_name.lower() or query in (d.volume_name or "").lower() or query in d.device_id.lower()]

        # Sort
        sort_idx = self._sort.currentIndex()
        if sort_idx == 0:  # Name
            devices.sort(key=lambda d: d.display_name.lower())
        elif sort_idx == 1:  # Type
            devices.sort(key=lambda d: (d.device_type.value, d.display_name.lower()))
        elif sort_idx == 2:  # Paired
            devices.sort(key=lambda d: d.paired_at or "", reverse=True)

        return devices

    @pyqtSlot(str)
    def _on_forget(self, device_id: str):
        self._devices = [d for d in self._devices if d.device_id != device_id]
        self.deviceForgotten.emit(device_id)
        self._rebuild()

    @pyqtSlot(str, str)
    def _on_pair_volume(self, volume_name: str, subpath: str):
        self.volumePaired.emit(volume_name, subpath)
        # The parent should call set_devices() to refresh
