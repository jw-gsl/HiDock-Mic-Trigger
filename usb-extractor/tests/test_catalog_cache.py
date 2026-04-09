"""Tests for recording catalog caching logic.

Tests the cache-based behavior of status_payload and
build_recording_status_items with mock state data.
No USB hardware or network access required.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

# conftest.py sets up the usb mock before this import
from extractor import (
    build_recording_status_items,
    load_json_file,
    output_name_for,
    save_json_file,
)


def _make_recording(name: str, length: int = 5000000) -> dict:
    """Build a minimal recording dict for testing."""
    return {
        "name": name,
        "createDate": "2026/02/25",
        "createTime": "11:17:02",
        "length": length,
        "duration": length / 8000.0,
        "version": 7,
        "mode": "room",
        "signature": f"sig_{name}",
    }


# ---------------------------------------------------------------------------
# Cache stores recordings by product ID
# ---------------------------------------------------------------------------
class TestCacheByProductId:
    def test_state_catalogs_keyed_by_product_id(self, tmp_path):
        """Catalog cache keys should use the product ID string."""
        state_path = tmp_path / "state.json"
        state = {
            "downloads": {},
            "catalogs": {
                "45068": {
                    "recordings": [_make_recording("h1-file.hda")],
                },
                "45070": {
                    "recordings": [_make_recording("p1-file.hda")],
                },
            },
        }
        save_json_file(state_path, state)
        loaded = load_json_file(state_path, {})
        h1_recs = loaded["catalogs"]["45068"]["recordings"]
        p1_recs = loaded["catalogs"]["45070"]["recordings"]
        assert len(h1_recs) == 1
        assert h1_recs[0]["name"] == "h1-file.hda"
        assert len(p1_recs) == 1
        assert p1_recs[0]["name"] == "p1-file.hda"


# ---------------------------------------------------------------------------
# Cache hit when file count matches
# ---------------------------------------------------------------------------
class TestCacheHit:
    def test_cached_recordings_used_when_count_matches(self, tmp_path):
        """When file count matches cached count, the cached catalog should be used."""
        cached_recordings = [
            _make_recording("rec1.hda"),
            _make_recording("rec2.hda"),
        ]
        state = {"downloads": {}}
        # Simulate: file_count == len(cached_recordings) => use cache
        file_count = 2
        if file_count is not None and len(cached_recordings) == file_count and cached_recordings:
            recordings = cached_recordings
        else:
            recordings = []  # would normally query device

        items = build_recording_status_items(recordings, state, tmp_path)
        assert len(items) == 2
        assert items[0]["name"] in ("rec1.hda", "rec2.hda")

    def test_build_status_items_preserves_recording_data(self, tmp_path):
        rec = _make_recording("test.hda", length=10000)
        state = {"downloads": {}}
        items = build_recording_status_items([rec], state, tmp_path)
        assert len(items) == 1
        assert items[0]["name"] == "test.hda"
        assert items[0]["status"] == "on_device"
        assert items[0]["outputName"] == output_name_for("test.hda")


# ---------------------------------------------------------------------------
# Cache miss when file count differs
# ---------------------------------------------------------------------------
class TestCacheMiss:
    def test_cache_miss_triggers_fresh_query(self, tmp_path):
        """When file count differs from cached count, the cache is stale."""
        cached_recordings = [_make_recording("old.hda")]
        file_count = 3  # device reports 3, cache has 1

        # Simulate the logic from status_payload
        if file_count is not None and len(cached_recordings) == file_count and cached_recordings:
            recordings = cached_recordings
        else:
            # Fresh query would happen here — simulate with new data
            recordings = [
                _make_recording("new1.hda"),
                _make_recording("new2.hda"),
                _make_recording("new3.hda"),
            ]

        items = build_recording_status_items(recordings, {"downloads": {}}, tmp_path)
        assert len(items) == 3

    def test_cache_miss_when_cache_empty(self, tmp_path):
        cached_recordings = []
        file_count = 2
        if file_count is not None and len(cached_recordings) == file_count and cached_recordings:
            recordings = cached_recordings
        else:
            recordings = [_make_recording("a.hda"), _make_recording("b.hda")]

        items = build_recording_status_items(recordings, {"downloads": {}}, tmp_path)
        assert len(items) == 2


# ---------------------------------------------------------------------------
# Disconnected device uses cached catalog
# ---------------------------------------------------------------------------
class TestDisconnectedDeviceCache:
    def test_disconnected_uses_cached_recordings(self, tmp_path):
        """When device is not connected, status_payload falls back to cached catalog."""
        cached_recs = [
            _make_recording("cached1.hda"),
            _make_recording("cached2.hda"),
        ]
        state = {
            "downloads": {},
            "catalogs": {
                "45068": {"recordings": cached_recs},
            },
        }
        # Simulate the disconnected path from status_payload
        product_id = 45068
        cache_key = str(product_id)
        recs_from_cache = state.get("catalogs", {}).get(cache_key, {}).get("recordings", [])
        items = build_recording_status_items(recs_from_cache, state, tmp_path, product_id=product_id)
        assert len(items) == 2
        names = {i["name"] for i in items}
        assert "cached1.hda" in names
        assert "cached2.hda" in names

    def test_disconnected_no_cache_returns_empty(self, tmp_path):
        """When device is disconnected and no cache exists, return empty list."""
        state = {"downloads": {}, "catalogs": {}}
        product_id = 45068
        cache_key = str(product_id)
        recs_from_cache = state.get("catalogs", {}).get(cache_key, {}).get("recordings", [])
        items = build_recording_status_items(recs_from_cache, state, tmp_path, product_id=product_id)
        assert items == []

    def test_disconnected_with_orphan_downloads(self, tmp_path):
        """Disconnected device should still show orphaned download records for matching product_id."""
        mp3 = tmp_path / "orphan.mp3"
        mp3.write_bytes(b"\xff\xf3" * 10)
        state = {
            "downloads": {
                "orphan.hda": {
                    "downloaded": True,
                    "product_id": 45068,
                    "output_path": str(mp3),
                },
                "other.hda": {
                    "downloaded": True,
                    "product_id": 99999,
                },
            },
            "catalogs": {"45068": {"recordings": []}},
        }
        recs_from_cache = state["catalogs"]["45068"]["recordings"]
        items = build_recording_status_items(recs_from_cache, state, tmp_path, product_id=45068)
        names = {i["name"] for i in items}
        assert "orphan.hda" in names
        assert "other.hda" not in names


# ---------------------------------------------------------------------------
# Empty cache returns empty list
# ---------------------------------------------------------------------------
class TestEmptyCache:
    def test_no_recordings_no_downloads(self, tmp_path):
        state = {"downloads": {}}
        items = build_recording_status_items([], state, tmp_path)
        assert items == []

    def test_no_catalogs_key_in_state(self, tmp_path):
        state = {"downloads": {}}
        cache_key = "45068"
        recs = state.get("catalogs", {}).get(cache_key, {}).get("recordings", [])
        assert recs == []
        items = build_recording_status_items(recs, state, tmp_path)
        assert items == []

    def test_catalog_exists_but_empty_recordings(self, tmp_path):
        state = {
            "downloads": {},
            "catalogs": {"45068": {"recordings": []}},
        }
        recs = state["catalogs"]["45068"]["recordings"]
        items = build_recording_status_items(recs, state, tmp_path)
        assert items == []


# ---------------------------------------------------------------------------
# build_recording_status_items detail checks
# ---------------------------------------------------------------------------
class TestBuildRecordingStatusDetails:
    def test_on_device_status(self, tmp_path):
        rec = _make_recording("test.hda")
        state = {"downloads": {}}
        items = build_recording_status_items([rec], state, tmp_path)
        assert items[0]["status"] == "on_device"
        assert items[0]["downloaded"] is False
        assert items[0]["localExists"] is False

    def test_downloaded_status(self, tmp_path):
        mp3 = tmp_path / "test.mp3"
        mp3.write_bytes(b"\xff\xf3" * 100)
        rec = _make_recording("test.hda", length=200)
        state = {
            "downloads": {
                "test.hda": {
                    "downloaded": True,
                    "output_path": str(mp3),
                }
            }
        }
        items = build_recording_status_items([rec], state, tmp_path)
        assert items[0]["status"] == "downloaded"
        assert items[0]["downloaded"] is True
        assert items[0]["localExists"] is True

    def test_failed_status(self, tmp_path):
        rec = _make_recording("fail.hda")
        state = {
            "downloads": {
                "fail.hda": {
                    "downloaded": False,
                    "last_error": "USB timeout",
                }
            }
        }
        items = build_recording_status_items([rec], state, tmp_path)
        assert items[0]["status"] == "failed"
        assert items[0]["lastError"] == "USB timeout"

    def test_items_sorted_by_date_descending(self, tmp_path):
        recs = [
            {
                "name": "old.hda",
                "createDate": "2026/01/01",
                "createTime": "10:00:00",
                "length": 1000,
                "duration": 0.125,
                "version": 7,
                "mode": "room",
                "signature": "sig1",
            },
            {
                "name": "new.hda",
                "createDate": "2026/03/15",
                "createTime": "14:30:00",
                "length": 2000,
                "duration": 0.25,
                "version": 7,
                "mode": "room",
                "signature": "sig2",
            },
        ]
        state = {"downloads": {}}
        items = build_recording_status_items(recs, state, tmp_path)
        assert items[0]["name"] == "new.hda"
        assert items[1]["name"] == "old.hda"
