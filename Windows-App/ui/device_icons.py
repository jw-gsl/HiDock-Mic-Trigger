"""Device icon loader — maps a HiDock SKU to a bespoke glyph or recording image.

Mirrors ``hidockDeviceImage()`` in ``hidock-mic-trigger/Sources/Helpers.swift`` so
device rendering is consistent across platforms. Callers should fall back to
their existing emoji icon when this returns ``None`` (unknown SKU, missing
asset, or SVG plugin not available).
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # Avoid importing PyQt6 at module load so the SKU matcher
    from PyQt6.QtGui import QPixmap  # can be unit-tested without Qt installed.

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "resources" / "device-images"


class HiDockSKU(Enum):
    P1 = "P1"
    H1 = "H1"
    H1E = "H1e"


def hidock_sku(display_name: str, is_volume: bool) -> HiDockSKU | None:
    """Return the SKU matched from the display name, or None for unknown/volume.

    The name is matched against an explicit model token so brand-name noise
    ("HiDock P1" → "hidock" contains "dock") doesn't false-match the H1 rule.
    Check H1e before H1 since "h1e" also contains "h1".
    """
    if is_volume:
        return None
    name = (display_name or "").lower()
    if "h1e" in name:
        return HiDockSKU.H1E
    if "h1" in name:
        return HiDockSKU.H1
    if "p1" in name:
        return HiDockSKU.P1
    return None


def _load_pixmap(filename: str, size: int) -> "QPixmap | None":
    # Imported lazily so the SKU matcher is usable in unit tests that mock PyQt6.
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QPixmap

    path = _ASSETS_DIR / filename
    if not path.exists():
        return None
    pm = QPixmap(str(path))
    if pm.isNull():
        return None
    return pm.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def device_glyph_pixmap(
    display_name: str,
    is_volume: bool,
    size: int = 22,
    *,
    recording: bool = False,
) -> "QPixmap | None":
    """Load a pixmap for a HiDock device glyph (or its recording-state variant).

    Returns ``None`` when the SKU is unknown or the asset fails to load, so
    callers can fall back to their existing emoji icon.
    """
    sku = hidock_sku(display_name, is_volume)
    if sku is None:
        return None
    if recording:
        filename = f"{sku.value}_recording.png"
    else:
        # H1 and H1e share the same line-art glyph; only the recording shot differs.
        filename = "H1_glyph.svg" if sku is HiDockSKU.H1E else f"{sku.value}_glyph.svg"
    return _load_pixmap(filename, size)


def connected_badge_pixmap(size: int = 12) -> "QPixmap | None":
    """Small green tick used next to the 'Connected' label."""
    return _load_pixmap("connected_glyph.svg", size)
