"""Tests for core/mic_trigger.py — stop/start race guards and peak detection."""
import threading

from core.mic_trigger import MicTrigger


class TestStartFfmpegGuard:
    def test_refuses_to_start_when_not_running(self):
        """stop() flips _running False; a mid-flight poll iteration must not
        (re)start the ffmpeg holder afterwards."""
        t = MicTrigger()
        t._running = False
        t._start_ffmpeg()
        assert t._ffmpeg_proc is None
        assert t._holding is False


class TestStopOrdering:
    def test_stop_joins_thread_before_stopping_ffmpeg(self, monkeypatch):
        """The poll thread must be joined BEFORE ffmpeg is torn down, or the
        loop can restart ffmpeg after stop() already released it."""
        t = MicTrigger()
        order = []

        class FakeThread:
            def join(self, timeout=None):
                order.append("join")

        t._running = True
        t._thread = FakeThread()
        monkeypatch.setattr(t, "_stop_ffmpeg", lambda: order.append("stop_ffmpeg"))

        t.stop()

        assert order == ["join", "stop_ffmpeg"]
        assert t._running is False
        assert t._thread is None

    def test_stop_does_not_join_current_thread(self, monkeypatch):
        """Calling stop() from the poll thread itself must not self-join."""
        t = MicTrigger()
        t._running = True
        t._thread = threading.current_thread()
        monkeypatch.setattr(t, "_stop_ffmpeg", lambda: None)
        t.stop()  # would raise RuntimeError on a self-join
        assert t._thread is None


class TestIsMicActive:
    def test_uses_capture_peak_when_available(self, monkeypatch):
        t = MicTrigger()
        monkeypatch.setattr(t, "_capture_peak", lambda: 0.5)
        assert t._is_mic_active() is True

    def test_quiet_capture_peak_is_inactive(self, monkeypatch):
        t = MicTrigger()
        monkeypatch.setattr(t, "_capture_peak", lambda: 0.0)
        assert t._is_mic_active() is False

    def test_capture_peak_returns_none_without_pycaw(self):
        """conftest mocks pycaw with an empty module — the meter path must
        degrade to None (falling back to the session heuristic) instead of
        raising."""
        t = MicTrigger()
        assert t._capture_peak() is None

    def test_poll_loop_survives_missing_comtypes(self):
        """_poll_loop wraps COM init in guards; with the conftest's empty
        comtypes mock (no CoInitialize) it must run and exit cleanly."""
        t = MicTrigger(poll_interval=0.01)
        t._running = False  # body loop exits immediately
        t._poll_loop()  # must not raise
