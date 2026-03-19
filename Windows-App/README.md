# HiDock Tools — Windows App

Windows desktop application for HiDock USB docking stations. Provides USB sync, transcription, and mic trigger functionality in a single window.

## Downloads

| Download | Build Status |
|----------|-------------|
| [Download HiDock.exe](https://nightly.link/jw-gsl/HiDock-Mic-Trigger/workflows/build-windows/main/HiDock-Windows.zip) (~50 MB) | [![Build Windows App](https://github.com/jw-gsl/HiDock-Mic-Trigger/actions/workflows/build-windows.yml/badge.svg)](https://github.com/jw-gsl/HiDock-Mic-Trigger/actions/workflows/build-windows.yml) |

> Download the .exe, double-click to run. No Python or other setup needed. The speech recognition model (~550 MB) is downloaded within the app on first use.

## Features

- **USB Sync** — Pair HiDock devices, browse recordings, download over USB
- **Transcription** — Local transcription using whisper.cpp (no GPU required)
- **Mic Trigger** — Monitor a USB mic and keep the HiDock input open via ffmpeg
- **Dark theme** — Modern dark UI with card-based layout
- **System tray** — Minimizes to system tray, toast notifications on events
- **In-app model download** — One-click download of the speech recognition model
- **Keyboard shortcuts** — Ctrl+R refresh, Ctrl+D download, Ctrl+T transcribe, F5
- **Auto-download** — Automatically download new recordings when detected

## For end users

Just download `HiDock.exe` from the link above and run it. Everything is self-contained.

On first use, click "Download Model" to get the speech recognition model (~550 MB one-time download). After that, transcription works offline.

## For developers

### Prerequisites

- Windows 10/11 (64-bit or ARM64)
- Python 3.11+ — `setup.bat` will install it automatically if not found
- [Zadig](https://zadig.akeo.ie/) — for WinUSB driver (see [../Windows-Script/README.md](../Windows-Script/README.md) for setup)
- [ffmpeg](https://www.gyan.dev/ffmpeg/builds/) — add to PATH for mic trigger

### Setup

```cmd
setup.bat
```

This creates a Python venv and installs all dependencies. If Python is not installed, it downloads and installs it automatically (ARM64 or x64 detected).

### Run

```cmd
run.bat
```

### Build standalone .exe

```cmd
build.bat
```

Produces `dist\HiDock.exe` (~50 MB). The .exe is also built automatically by GitHub Actions on every push to `main`.

## Architecture

```
Windows-App/
  app.py                 # Entry point, theme loading, system tray
  resources/
    theme.qss            # Dark theme stylesheet
    icon.ico             # App icon
  ui/
    main_window.py       # Main window (3-card layout, menu bar, shortcuts)
    recording_model.py   # Table model with status icons and tooltips
  core/
    config.py            # Configuration (paths, whisper.cpp model settings)
    state.py             # Transcription state management
    usb_sync.py          # USB sync (wraps ../Windows-Script/extractor.py)
    transcription.py     # Transcription via whisper.cpp (pywhispercpp)
    mic_trigger.py       # Windows mic trigger (WASAPI via pycaw)
    model_download.py    # In-app model download with progress and SSL handling
  requirements.txt       # Python dependencies (pywhispercpp, no PyTorch)
  setup.bat              # Auto-installs Python + venv
  run.bat                # Launch app
  build.bat              # PyInstaller build
  hidock.spec            # PyInstaller spec file
  PORTING.md             # macOS -> Windows porting guide
```

## Relationship to macOS app

| macOS (primary) | Windows (this port) |
|-----------------|---------------------|
| `hidock-mic-trigger/` — Swift/AppKit menu bar app | `Windows-App/` — Python/PyQt6 desktop app |
| `usb-extractor/` — Python USB extractor | `Windows-Script/` — Windows-adapted USB extractor |
| `transcription-pipeline/` — whisper.cpp via `transcribe_cpp.py` | `core/transcription.py` — whisper.cpp via pywhispercpp |
| `mic-trigger/` — Swift CLI, CoreAudio | `core/mic_trigger.py` — Python, WASAPI |
| Model: `ggml-large-v3-turbo-q5_0.bin` | Same model, same whisper.cpp backend |

## Known limitations vs macOS

- Mic trigger uses WASAPI polling instead of CoreAudio (approximate detection)
- USB requires WinUSB driver via Zadig (one-time setup)
- System tray instead of macOS menu bar
- No speaker diarization support
