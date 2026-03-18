# HiDock Tools — Windows App

Windows desktop application for HiDock USB docking stations. Provides USB sync, transcription, and mic trigger functionality in a single window.

> **This is a secondary port of the macOS app.** The primary development happens in `hidock-mic-trigger/` (Swift/AppKit). Changes are ported to this Windows app as needed. See [PORTING.md](PORTING.md) for the porting workflow.

## Features

- **USB Sync** — Pair HiDock devices, browse recordings, download over USB
- **Transcription** — Local transcription using OpenAI Whisper (CUDA or CPU)
- **Mic Trigger** — Monitor a USB mic and keep the HiDock input open via ffmpeg
- **System tray** — Runs in the system tray with a single unified window

## Prerequisites

- Windows 10/11 (64-bit)
- Python 3.11+ ([python.org](https://www.python.org/downloads/)) — check "Add Python to PATH"
- [Zadig](https://zadig.akeo.ie/) — for WinUSB driver (see [../Windows-Script/README.md](../Windows-Script/README.md) for setup)
- [ffmpeg](https://www.gyan.dev/ffmpeg/builds/) — add to PATH for mic trigger
- NVIDIA GPU recommended for fast transcription (CUDA), otherwise uses CPU

## Quick start

### 1. Setup

```cmd
setup.bat
```

This creates a Python venv, installs PyQt6, PyTorch, Whisper, and all dependencies.

### 2. Run

```cmd
run.bat
```

Or directly:

```cmd
.venv\Scripts\python app.py
```

### 3. Build standalone .exe

```cmd
build.bat
```

Produces `dist\HiDock.exe` — a self-contained executable (no Python install needed on the target machine).

## Architecture

```
Windows-App/
  app.py                 # Entry point
  ui/
    main_window.py       # Unified window (mic trigger + sync + transcription)
    recording_model.py   # Table model for recordings
  core/
    config.py            # Windows-adapted configuration
    state.py             # Transcription state management
    usb_sync.py          # USB sync (wraps ../Windows-Script/extractor.py)
    transcription.py     # Whisper transcription (adapted from ../transcription-pipeline/)
    mic_trigger.py       # Windows mic trigger (WASAPI via pycaw)
  resources/
    icon.ico             # App icon
  requirements.txt       # Python dependencies
  setup.bat              # Venv creation
  run.bat                # Launch app
  build.bat              # PyInstaller build
  hidock.spec            # PyInstaller spec file
  PORTING.md             # macOS → Windows porting guide
```

## Relationship to macOS app

| macOS (primary) | Windows (this port) |
|-----------------|---------------------|
| `hidock-mic-trigger/` — Swift/AppKit menu bar app | `Windows-App/` — Python/PyQt6 desktop app |
| `usb-extractor/` — Python USB extractor | `Windows-Script/` — Windows-adapted USB extractor |
| `transcription-pipeline/` — Whisper on MPS | `Windows-App/core/transcription.py` — Whisper on CUDA/CPU |
| `mic-trigger/` — Swift CLI, CoreAudio | `Windows-App/core/mic_trigger.py` — Python, WASAPI |

## Known limitations vs macOS

- Transcription uses CUDA (NVIDIA) or CPU — no MPS equivalent on Windows
- Mic trigger uses WASAPI polling instead of CoreAudio's `kAudioDevicePropertyDeviceIsRunningSomewhere`
- USB requires WinUSB driver via Zadig (one-time setup)
- System tray instead of macOS menu bar
