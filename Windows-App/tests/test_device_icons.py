"""Tests for ui.device_icons."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make sibling packages importable when pytest is run from the repo root.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ui.device_icons import HiDockSKU, hidock_sku  # noqa: E402


class TestHiDockSKU:
    def test_p1_matches_on_shortname(self):
        assert hidock_sku("HiDock P1", is_volume=False) is HiDockSKU.P1
        assert hidock_sku("P1", is_volume=False) is HiDockSKU.P1

    def test_h1_matches_on_shortname(self):
        assert hidock_sku("HiDock H1", is_volume=False) is HiDockSKU.H1
        assert hidock_sku("H1", is_volume=False) is HiDockSKU.H1

    def test_h1e_matches_before_h1(self):
        # Order matters: "h1e" must win over the generic "h1" rule.
        assert hidock_sku("HiDock H1e", is_volume=False) is HiDockSKU.H1E
        assert hidock_sku("H1E", is_volume=False) is HiDockSKU.H1E

    def test_does_not_match_dock_alone(self):
        # Regression: the word "dock" in "HiDock" must not claim the H1 slot,
        # otherwise "HiDock P1" mis-matches as H1.
        assert hidock_sku("HiDock", is_volume=False) is None
        assert hidock_sku("HiDock P1", is_volume=False) is HiDockSKU.P1

    def test_volume_always_none(self):
        assert hidock_sku("HiDock P1", is_volume=True) is None

    def test_unknown_returns_none(self):
        assert hidock_sku("Something else", is_volume=False) is None

    def test_empty_string(self):
        assert hidock_sku("", is_volume=False) is None

    def test_none_display_name(self):
        # Defensive: shouldn't crash if a device has no name set.
        assert hidock_sku(None, is_volume=False) is None  # type: ignore[arg-type]
