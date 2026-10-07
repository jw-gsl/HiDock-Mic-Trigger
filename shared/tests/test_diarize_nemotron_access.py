"""The Nemotron client's connection check against a stand-in sidecar.

The real sidecar (ember) checks X-Auth-Token before the profile, so a bogus
profile answers "is the token right?" without running the model: 401 when
the token is missing or wrong, 400 when it's accepted.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from shared import diarize_nemotron

TOKEN = "secret-token"


class _Sidecar(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep test output clean
        pass

    def _reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._reply(200, {"ok": True, "model": "nvidia/Nemotron-3-Diarization"})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.headers.get("X-Auth-Token") != TOKEN:
            self._reply(401, {"detail": "missing or invalid X-Auth-Token"})
        else:
            self._reply(400, {"detail": "profile must be one of [...]"})


@pytest.fixture()
def sidecar(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), _Sidecar)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HIDOCK_NEMOTRON_ENDPOINT", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.delenv("HIDOCK_NEMOTRON_TOKEN", raising=False)
    monkeypatch.setattr(diarize_nemotron, "_nemotron_config", lambda: {})
    yield
    server.shutdown()


def test_endpoint_comes_from_the_app(monkeypatch):
    monkeypatch.setenv("HIDOCK_NEMOTRON_ENDPOINT", "http://spark.example:8890/")
    assert diarize_nemotron.endpoint() == "http://spark.example:8890/diarize"


def test_no_token_is_not_available(sidecar):
    ok, why = diarize_nemotron.available()
    assert not ok and "no token set" in why


def test_wrong_token_is_rejected(sidecar, monkeypatch):
    monkeypatch.setenv("HIDOCK_NEMOTRON_TOKEN", "nope")
    ok, why = diarize_nemotron.available()
    assert not ok and "rejected" in why


def test_right_token_is_available(sidecar, monkeypatch):
    monkeypatch.setenv("HIDOCK_NEMOTRON_TOKEN", TOKEN)
    ok, why = diarize_nemotron.available()
    assert ok and "token accepted" in why
