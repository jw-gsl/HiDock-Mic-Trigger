"""Tests for plaud_client region handling and dead-session detection.

No network access: the signed-out path is exercised with an already-expired
JWT and no refresh token, so `_ensure_fresh_token` raises before any request.
"""
from __future__ import annotations

import base64
import json
import time

import pytest

import plaud_client as pc


def _jwt(exp: int) -> str:
    """Minimal JWT carrying just an `exp` claim (header/sig are placeholders)."""
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).rstrip(b"=").decode()
    return f"aaa.{payload}.bbb"


# --- host validation ------------------------------------------------------

@pytest.mark.parametrize("url,ok", [
    ("https://api.plaud.ai", True),
    ("https://api-euc1.plaud.ai", True),
    ("https://api-apse1.plaud.ai/path", True),
    ("https://plaud.ai", True),
    ("http://api.plaud.ai", False),            # not https
    ("https://plaud.ai.evil.com", False),      # suffix trick
    ("https://evil.com@plaud.ai.evil.com", False),  # userinfo trick
    ("not-a-url", False),
])
def test_is_valid_plaud_api_url(url, ok):
    assert pc._is_valid_plaud_api_url(url) is ok


# --- region resolution ----------------------------------------------------

def test_base_url_known_regions():
    assert pc._base_url("us") == pc.API_US
    assert pc._base_url("eu") == pc.API_EU
    assert pc._base_url("apac") == pc.API_APAC
    assert pc._base_url("zz") == pc.API_US  # unknown -> default


def test_base_url_trusts_valid_full_host_else_default():
    assert pc._base_url("https://api-apse1.plaud.ai/") == pc.API_APAC
    assert pc._base_url("https://evil.example.com") == pc.API_US


@pytest.mark.parametrize("api,expected", [
    ("https://api-euc1.plaud.ai", "eu"),
    ("https://api-apse1.plaud.ai", "apac"),
    ("https://api.plaud.ai/", "us"),
    ("https://api-usw2.plaud.ai", "https://api-usw2.plaud.ai"),  # unknown -> full url
    ("https://evil.example.com", None),
    ("http://api.plaud.ai", None),
])
def test_region_from_redirect(api, expected):
    assert pc._region_from_redirect(api) == expected


# --- expiry detection -----------------------------------------------------

def test_is_expired():
    assert pc._is_expired(_jwt(int(time.time()) - 100)) is True
    assert pc._is_expired(_jwt(int(time.time()) + 10_000)) is False
    assert pc._is_expired("opaque-token") is False  # can't read -> not proven dead


# --- dead-session detection (the masking-bug fix) -------------------------

def test_expired_token_without_refresh_reports_signed_out(monkeypatch):
    """Expired access token + no refresh token must raise a signed-out error
    (containing 'not signed in') rather than returning the dead token, which
    would otherwise surface as 'connected, 0 recordings'."""
    pc._REFRESHED.clear()
    monkeypatch.setenv("PLAUD_ACCESS_TOKEN", _jwt(int(time.time()) - 100))
    monkeypatch.delenv("PLAUD_REFRESH_TOKEN", raising=False)
    monkeypatch.setenv("PLAUD_REGION", "us")
    with pytest.raises(pc.PlaudError) as exc:
        pc._ensure_fresh_token("test-account-signedout")
    assert "not signed in" in str(exc.value).lower()


def test_valid_token_returns_without_network(monkeypatch):
    pc._REFRESHED.clear()
    token = _jwt(int(time.time()) + 10_000)
    monkeypatch.setenv("PLAUD_ACCESS_TOKEN", token)
    monkeypatch.delenv("PLAUD_REFRESH_TOKEN", raising=False)
    monkeypatch.setenv("PLAUD_REGION", "eu")
    access, region = pc._ensure_fresh_token("test-account-valid")
    assert access == token
    assert region == "eu"


# --- offline / signed-out: downloaded files still show --------------------

def _state_with_download(path) -> dict:
    return {
        "downloads": {
            "plaud:acct:rec123": {
                "downloaded": True,
                "output_path": str(path),
                "length": 1024,
                "signature": "rec123",
                "source": "plaud",
                "account_id": "acct",
                "downloaded_at": "2026-06-09T15:00:00+00:00",
            }
        }
    }


def test_recordings_from_downloads_lists_existing_files(tmp_path):
    f = tmp_path / "Plaud" / "2026-06-09" / "2026-06-09 15-03-56.mp3"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"\x00" * 1024)
    recs = pc._recordings_from_downloads(_state_with_download(f), "acct")
    assert len(recs) == 1
    assert recs[0]["name"] == "rec123"
    assert recs[0]["downloaded"] is True
    assert recs[0]["createDate"] == "2026/06/09"  # from the YYYY-MM-DD folder


def test_recordings_from_downloads_skips_missing_files(tmp_path):
    missing = tmp_path / "Plaud" / "gone.mp3"
    assert pc._recordings_from_downloads(_state_with_download(missing), "acct") == []


def test_status_payload_shows_downloaded_when_signed_out(tmp_path, monkeypatch):
    f = tmp_path / "Plaud" / "2026-06-09" / "2026-06-09 15-03-56.mp3"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"\x00" * 1024)

    def _raise(account_id=None):
        raise pc.PlaudError(pc.SIGNED_OUT_MESSAGE)

    monkeypatch.setattr(pc, "list_recordings", _raise)
    payload = pc.status_payload(tmp_path, _state_with_download(f), account_id="acct")
    assert payload["connected"] is False
    assert "not signed in" in payload.get("error", "").lower()
    assert [r["name"] for r in payload["recordings"]] == ["rec123"]
    assert payload.get("cached") is True


def test_cached_status_payload_is_network_free(tmp_path, monkeypatch):
    f = tmp_path / "Plaud" / "2026-06-09" / "2026-06-09 15-03-56.mp3"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"\x00" * 1024)

    # Hard-fail if it touches the network at all.
    def _boom(*a, **k):
        raise AssertionError("cached_status_payload must not hit the network")

    monkeypatch.setattr(pc, "list_recordings", _boom)
    monkeypatch.setattr(pc, "_request_json", _boom)
    payload = pc.cached_status_payload(tmp_path, _state_with_download(f), account_id="acct")
    assert payload["connected"] is False
    assert payload["cached"] is True
    assert [r["name"] for r in payload["recordings"]] == ["rec123"]


def test_cached_status_payload_empty_when_nothing_cached(tmp_path):
    payload = pc.cached_status_payload(tmp_path, {"downloads": {}}, account_id="acct")
    assert payload["recordings"] == []
    assert payload["connected"] is False
