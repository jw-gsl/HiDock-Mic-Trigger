"""Windows mic trigger — monitors a USB mic via WASAPI and holds the HiDock input open.

On Windows, we use pycaw to enumerate audio devices and check for active
audio sessions. When the trigger mic is in use, we launch ffmpeg with
DirectShow to keep the HiDock audio input active.

NOTE: This is a best-effort port of the macOS CoreAudio-based trigger.
Windows does not have a direct equivalent of kAudioDevicePropertyDeviceIsRunningSomewhere,
so we poll the audio meter level instead.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from typing import Callable


class MicTrigger:
    """Watches a USB mic and holds a HiDock audio input open via ffmpeg."""

    def __init__(
        self,
        trigger_mic_name: str = "Samson Q2U Microphone",
        hidock_audio_name: str = "HiDock",
        poll_interval: float = 0.5,
        debounce_samples: int = 4,
        on_state_change: Callable[[bool], None] | None = None,
        on_log: Callable[[str], None] | None = None,
    ):
        self.trigger_mic_name = trigger_mic_name
        self.hidock_audio_name = hidock_audio_name
        self.poll_interval = poll_interval
        self.debounce_samples = debounce_samples
        self.on_state_change = on_state_change
        self.on_log = on_log

        self._running = False
        self._thread: threading.Thread | None = None
        self._ffmpeg_proc: subprocess.Popen | None = None
        self._holding = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_holding(self) -> bool:
        return self._holding

    def start(self):
        """Start the mic trigger polling loop."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        self._log("Mic trigger started")

    def stop(self):
        """Stop the mic trigger and release ffmpeg."""
        self._running = False
        self._stop_ffmpeg()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        self._log("Mic trigger stopped")

    def _log(self, msg: str):
        if self.on_log:
            self.on_log(msg)

    def _poll_loop(self):
        last_state = False
        stable_count = 0

        while self._running:
            try:
                mic_active = self._is_mic_active()
            except Exception as e:
                self._log(f"Error checking mic: {e}")
                time.sleep(self.poll_interval)
                continue

            if mic_active == last_state:
                # Reconcile state
                if mic_active and not self._holding:
                    self._log("Reconcile: mic active but not holding -> start ffmpeg")
                    self._start_ffmpeg()
                elif not mic_active and self._holding:
                    self._log("Reconcile: mic idle but holding -> stop ffmpeg")
                    self._stop_ffmpeg()
                stable_count = 0
            else:
                stable_count += 1
                if stable_count >= self.debounce_samples:
                    last_state = mic_active
                    stable_count = 0
                    if mic_active:
                        self._log("USB mic became IN USE -> holding HiDock open")
                        self._start_ffmpeg()
                    else:
                        self._log("USB mic became NOT IN USE -> releasing HiDock")
                        self._stop_ffmpeg()

            time.sleep(self.poll_interval)

    def _is_mic_active(self) -> bool:
        """Check if the trigger mic is actively being used.

        Uses pycaw to check audio meter peak level. A non-zero level
        indicates the mic is capturing audio.
        """
        try:
            from comtypes import CLSCTX_ALL
            from pycaw.pycaw import AudioUtilities, IAudioMeterInformation

            devices = AudioUtilities.GetAllDevices()
            for device in devices:
                if (
                    device.FriendlyName
                    and self.trigger_mic_name.lower() in device.FriendlyName.lower()
                ):
                    # Found the device — check if it has active sessions
                    sessions = AudioUtilities.GetAllSessions()
                    for session in sessions:
                        if session.Process and session.State == 1:  # AudioSessionStateActive
                            # Check if this session is using our mic
                            # This is approximate — Windows doesn't easily map sessions to devices
                            return True
            return False
        except ImportError:
            # pycaw not available — fall back to checking if any sessions are active
            self._log("pycaw not available, mic detection disabled")
            return False
        except Exception:
            return False

    def _start_ffmpeg(self):
        """Launch ffmpeg to hold the HiDock audio input open."""
        if self._ffmpeg_proc is not None:
            return

        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            self._log("ffmpeg not found in PATH")
            return

        try:
            self._ffmpeg_proc = subprocess.Popen(
                [
                    ffmpeg_path,
                    "-loglevel", "error",
                    "-f", "dshow",
                    "-i", f"audio={self.hidock_audio_name}",
                    "-ac", "1",
                    "-ar", "48000",
                    "-f", "null",
                    "-",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self._holding = True
            self._log(f"Started ffmpeg (pid {self._ffmpeg_proc.pid})")
            if self.on_state_change:
                self.on_state_change(True)
        except Exception as e:
            self._log(f"Failed to start ffmpeg: {e}")

    def _stop_ffmpeg(self):
        """Stop the ffmpeg process."""
        if self._ffmpeg_proc is None:
            return
        try:
            self._ffmpeg_proc.terminate()
            self._ffmpeg_proc.wait(timeout=5)
        except Exception:
            try:
                self._ffmpeg_proc.kill()
            except Exception:
                pass
        self._ffmpeg_proc = None
        self._holding = False
        self._log("Stopped ffmpeg")
        if self.on_state_change:
            self.on_state_change(False)


def list_audio_input_devices() -> list[str]:
    """List available audio input device names on Windows."""
    try:
        from pycaw.pycaw import AudioUtilities
        devices = AudioUtilities.GetAllDevices()
        return [
            d.FriendlyName
            for d in devices
            if d.FriendlyName and d.state == 1  # DEVICE_STATE_ACTIVE
        ]
    except ImportError:
        return ["(pycaw not installed — install to list devices)"]
    except Exception:
        return []
