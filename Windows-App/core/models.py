"""Device models — shared data classes for paired device management.

Mirrors the macOS Models.swift DeviceType/HiDockPairedDevice types.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


def _stable_hash(s: str) -> int:
    """Deterministic hash stable across Python runs (unlike hash())."""
    return int(hashlib.md5(s.encode()).hexdigest()[:8], 16) & 0x7FFFFFFF


class DeviceType(str, Enum):
    HIDOCK = "hidock"
    VOLUME = "volume"
    PLAUD = "plaud"


@dataclass
class PairedDevice:
    """A remembered device — either a HiDock USB dock or a mass-storage volume."""

    device_type: DeviceType
    display_name: str
    product_id: int = 0
    volume_name: str | None = None
    subpath: str | None = None
    paired_at: str | None = None
    # Plaud cloud account fields (only populated for DeviceType.PLAUD).
    plaud_account_id: str | None = None
    plaud_email: str | None = None
    plaud_region: str | None = None

    @property
    def device_id(self) -> str:
        if self.device_type == DeviceType.HIDOCK:
            return f"hidock:{self.product_id}"
        if self.device_type == DeviceType.PLAUD:
            return f"plaud:{self.plaud_account_id or self.product_id}"
        return f"volume:{self.volume_name or self.product_id}"

    @property
    def short_name(self) -> str:
        name = self.display_name
        if name.startswith("HiDock "):
            return name[len("HiDock "):]
        return name

    def to_dict(self) -> dict:
        return {
            "device_type": self.device_type.value,
            "display_name": self.display_name,
            "product_id": self.product_id,
            "volume_name": self.volume_name,
            "subpath": self.subpath,
            "paired_at": self.paired_at,
            "plaud_account_id": self.plaud_account_id,
            "plaud_email": self.plaud_email,
            "plaud_region": self.plaud_region,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PairedDevice:
        return cls(
            device_type=DeviceType(d.get("device_type", "hidock")),
            display_name=d.get("display_name", ""),
            product_id=d.get("product_id", 0),
            volume_name=d.get("volume_name"),
            subpath=d.get("subpath"),
            paired_at=d.get("paired_at"),
            plaud_account_id=d.get("plaud_account_id"),
            plaud_email=d.get("plaud_email"),
            plaud_region=d.get("plaud_region"),
        )

    @classmethod
    def hidock(cls, product_id: int, display_name: str) -> PairedDevice:
        return cls(
            device_type=DeviceType.HIDOCK,
            display_name=display_name,
            product_id=product_id,
            paired_at=datetime.now(timezone.utc).isoformat(),
        )

    @classmethod
    def volume(cls, volume_name: str, display_name: str, subpath: str | None = None) -> PairedDevice:
        return cls(
            device_type=DeviceType.VOLUME,
            display_name=display_name,
            product_id=_stable_hash(volume_name),
            volume_name=volume_name,
            subpath=subpath,
            paired_at=datetime.now(timezone.utc).isoformat(),
        )

    @classmethod
    def plaud(
        cls,
        account_id: str,
        display_name: str,
        email: str | None = None,
        region: str = "us",
    ) -> PairedDevice:
        return cls(
            device_type=DeviceType.PLAUD,
            display_name=display_name,
            product_id=_stable_hash(account_id),
            plaud_account_id=account_id,
            plaud_email=email,
            plaud_region=region,
            paired_at=datetime.now(timezone.utc).isoformat(),
        )


def load_paired_devices(settings) -> list[PairedDevice]:
    """Load paired devices from QSettings."""
    raw = settings.value("pairedDevices", "[]")
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else []
        items = parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        items = []
    return [PairedDevice.from_dict(d) for d in items if isinstance(d, dict)]


def save_paired_devices(settings, devices: list[PairedDevice]) -> None:
    """Save paired devices to QSettings."""
    settings.setValue("pairedDevices", json.dumps([d.to_dict() for d in devices]))
