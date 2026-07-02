"""Tests that Windows-Script/extractor.py threads --product-id through.

The extractor imports pyusb + plaud_client at module level; both are mocked
here (like conftest does for pycaw) so the pure-Python paths can be exercised
on any platform.
"""
import sys
import types
from pathlib import Path

import pytest

# ── Mock USB deps and import the Windows-Script extractor ───────────────────

def _ensure_mock(modname, **attrs):
    mod = sys.modules.get(modname)
    if mod is None:
        mod = types.ModuleType(modname)
        sys.modules[modname] = mod
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


_usb = _ensure_mock("usb")
_usb.core = _ensure_mock(
    "usb.core",
    USBError=type("USBError", (Exception,), {}),
    USBTimeoutError=type("USBTimeoutError", (Exception,), {}),
    find=lambda **kw: None,
)
_usb.util = _ensure_mock("usb.util")
_ensure_mock("plaud_client", pop_refreshed_tokens=lambda account_id: None)

_WINDOWS_SCRIPT = Path(__file__).resolve().parents[2] / "Windows-Script"
if str(_WINDOWS_SCRIPT) not in sys.path:
    sys.path.insert(0, str(_WINDOWS_SCRIPT))

import extractor  # noqa: E402


class TestFindDevice:
    def test_default_product_id(self):
        with pytest.raises(FileNotFoundError) as exc:
            extractor.find_device()
        assert f"{extractor.VENDOR_ID}:{extractor.PRODUCT_ID}" in str(exc.value)

    def test_custom_product_id_reaches_usb_lookup(self):
        """--product-id used to be parsed but never passed to find_device."""
        seen = {}

        def fake_find(idVendor=None, idProduct=None, backend=None):
            seen["pid"] = idProduct
            return None

        orig = extractor.usb.core.find
        extractor.usb.core.find = fake_find
        try:
            with pytest.raises(FileNotFoundError) as exc:
                extractor.find_device(product_id=45069)
        finally:
            extractor.usb.core.find = orig
        assert seen["pid"] == 45069
        assert "45069" in str(exc.value)


class TestCliThreadsProductId:
    def test_status_passes_product_id(self, monkeypatch, capsys):
        called = {}

        def fake_status(timeout_ms=5000, product_id=None):
            called["product_id"] = product_id
            return {"connected": False}

        monkeypatch.setattr(extractor, "status_payload", fake_status)
        monkeypatch.setattr(sys, "argv", ["extractor.py", "--product-id", "45069", "status"])
        assert extractor.main() == 0
        assert called["product_id"] == 45069

    def test_download_new_passes_product_id(self, monkeypatch, capsys):
        called = {}

        def fake_download_new(timeout_ms=5000, product_id=None):
            called["product_id"] = product_id
            return {"connected": False, "downloaded": [], "skipped": []}

        monkeypatch.setattr(extractor, "download_new", fake_download_new)
        monkeypatch.setattr(sys, "argv", ["extractor.py", "--product-id", "45070", "download-new"])
        assert extractor.main() == 0
        assert called["product_id"] == 45070

    def test_download_passes_product_id(self, monkeypatch, capsys):
        called = {}

        def fake_download_one(filename, length=None, timeout_ms=5000, product_id=None):
            called["product_id"] = product_id
            called["filename"] = filename
            return {"filename": filename, "downloaded": True}

        monkeypatch.setattr(extractor, "download_one", fake_download_one)
        monkeypatch.setattr(
            sys, "argv",
            ["extractor.py", "--product-id", "45068", "download", "2026Mar09-131439-Rec39.hda"],
        )
        assert extractor.main() == 0
        assert called["product_id"] == 45068
        assert called["filename"] == "2026Mar09-131439-Rec39.hda"


class TestStateEntryFiltering:
    def _state(self):
        return {
            "downloads": {
                "2026Mar01-090000-Rec01.hda": {
                    "downloaded": True,
                    "product_id": 45068,
                    "length": 100,
                },
                "2026Mar02-090000-Rec02.hda": {
                    "downloaded": True,
                    "product_id": 45070,
                    "length": 100,
                },
                "2026Mar03-090000-Rec03.hda": {
                    "downloaded": True,
                    "length": 100,  # legacy entry, no product_id recorded
                },
            }
        }

    def test_no_product_id_keeps_all_state_entries(self, tmp_path):
        items = extractor.build_recording_status_items([], self._state(), tmp_path)
        assert len(items) == 3

    def test_product_id_filters_other_devices(self, tmp_path):
        items = extractor.build_recording_status_items(
            [], self._state(), tmp_path, product_id=45068
        )
        names = {i["name"] for i in items}
        assert names == {"2026Mar01-090000-Rec01.hda"}

    def _patch_device(self, monkeypatch):
        monkeypatch.setattr(extractor, "find_device", lambda product_id=None: object())
        monkeypatch.setattr(extractor, "prepare_device", lambda dev: 0)
        monkeypatch.setattr(extractor, "release_device", lambda dev, iface: None)

    def test_download_one_records_product_id_on_success(self, tmp_path, monkeypatch):
        self._patch_device(monkeypatch)
        monkeypatch.setattr(
            extractor, "transfer_file_stream_to_path", lambda *a, **kw: 10
        )
        state_path = tmp_path / "state.json"
        result = extractor.download_one(
            "2026Mar09-131439-Rec39.hda",
            length=10,
            output_dir=tmp_path,
            config_path=tmp_path / "config.json",
            state_path=state_path,
            product_id=45069,
        )
        assert result["downloaded"] is True
        record = extractor.load_state(state_path)["downloads"]["2026Mar09-131439-Rec39.hda"]
        assert record["product_id"] == 45069

    def test_download_one_records_product_id_on_failure(self, tmp_path, monkeypatch):
        """Even a failed download stamps the state entry with the device's
        product id so later status filtering attributes it correctly."""
        self._patch_device(monkeypatch)

        def _boom(*a, **kw):
            raise extractor.HiDockProtocolError("boom")

        monkeypatch.setattr(extractor, "transfer_file_stream_to_path", _boom)
        state_path = tmp_path / "state.json"
        with pytest.raises(extractor.HiDockProtocolError):
            extractor.download_one(
                "2026Mar09-131439-Rec39.hda",
                length=10,
                output_dir=tmp_path,
                config_path=tmp_path / "config.json",
                state_path=state_path,
                product_id=45069,
            )
        record = extractor.load_state(state_path)["downloads"]["2026Mar09-131439-Rec39.hda"]
        assert record["product_id"] == 45069
        assert record["downloaded"] is False
