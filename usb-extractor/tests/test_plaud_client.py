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
