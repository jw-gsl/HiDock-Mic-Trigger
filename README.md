# HiDock Tools

A suite of tools for working with [HiDock](https://www.hidock.com) USB docking stations. Automatically trigger recording with an external mic, download recordings over USB, and transcribe them locally using Whisper.

## Downloads

| Platform | Download | Build Status |
|----------|----------|-------------|
| **Windows** | [Download HiDock.exe](https://nightly.link/jw-gsl/HiDock-Mic-Trigger/workflows/build-windows/main/HiDock-Windows.zip) | [![Build Windows App](https://github.com/jw-gsl/HiDock-Mic-Trigger/actions/workflows/build-windows.yml/badge.svg)](https://github.com/jw-gsl/HiDock-Mic-Trigger/actions/workflows/build-windows.yml) |
| **macOS** | [Download HiDock Mic Trigger.app](https://nightly.link/jw-gsl/HiDock-Mic-Trigger/workflows/build-macos/main/HiDock-Mic-Trigger-macOS.zip) | [![Build macOS App](https://github.com/jw-gsl/HiDock-Mic-Trigger/actions/workflows/build-macos.yml/badge.svg)](https://github.com/jw-gsl/HiDock-Mic-Trigger/actions/workflows/build-macos.yml) |

> Download links always point to the latest successful build via [nightly.link](https://nightly.link). No GitHub login required. The speech recognition model (~550 MB) is downloaded within the app on first use.

## Components

| Folder | Platform | Description |
|---|---|---|
| `hidock-mic-trigger/` | macOS | Menu bar app — unified UI for mic trigger, USB sync, and transcription |
| `mic-trigger/` | macOS | Swift CLI that watches a USB mic and keeps the HiDock input open via ffmpeg |
| `usb-extractor/` | macOS | Python USB extractor that downloads recordings directly from HiDock over USB |
| `transcription-pipeline/` | macOS | Python transcription pipeline using OpenAI Whisper on Apple MPS |
| `Windows-App/` | Windows | PyQt6 desktop app — Windows port of the macOS menu bar app |
| `Windows-Script/` | Windows | Python USB extractor and background watcher for Windows |

> **macOS is the primary development platform.** The Windows app is a secondary port. See [Windows-App/PORTING.md](Windows-App/PORTING.md) for the porting workflow.

## How it works

1. **Mic Trigger** — watches your USB mic (e.g. Samson Q2U) via CoreAudio. When it detects the mic is in use, it silently opens the HiDock's audio input using `ffmpeg`, causing the HiDock to auto-record.
2. **USB Sync** — pairs with one or more HiDock devices over USB and downloads recordings as MP3 files to a local folder.
3. **Transcription** — runs OpenAI Whisper (`large-v3-turbo`) on Apple Silicon MPS to transcribe downloaded recordings to Markdown files. Optional speaker diarization via pyannote.audio.

All three are controlled from a single menu bar app with a unified window.

## Prerequisites

- macOS 13+
- Apple Silicon (M1/M2/M3/M4) for MPS-accelerated transcription
- [Homebrew](https://brew.sh)
- [ffmpeg](https://formulae.brew.sh/formula/ffmpeg): `brew install ffmpeg`
- [XcodeGen](https://github.com/yonaskolb/XcodeGen): `brew install xcodegen`
- Xcode Command Line Tools: `xcode-select --install`
- Python 3.11+ (for USB extractor and transcription)

## Quick start

### 1. Build the menu bar app

```bash
cd hidock-mic-trigger
xcodegen generate
xcodebuild -scheme hidock-mic-trigger -configuration Release build
```

### 2. Set up the USB extractor

```bash
cd usb-extractor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Set up the transcription pipeline

```bash
cd transcription-pipeline
./setup-venv.sh
```

This creates a Python venv with PyTorch, OpenAI Whisper, and verifies MPS availability.

### 4. Find your HiDock audio index

```bash
ffmpeg -f avfoundation -list_devices true -i "" 2>&1 | grep -A 20 "audio devices"
```

Note the index number of your HiDock audio input (e.g. `1`).

### 5. Install and run

```bash
cp -R ~/Library/Developer/Xcode/DerivedData/hidock-mic-trigger-*/Build/Products/Release/hidock-mic-trigger.app \
  ~/Applications/"HiDock Mic Trigger.app"
open ~/Applications/"HiDock Mic Trigger.app"
```

The app opens a unified window with:
- **Mic Trigger** controls at the top (Start/Stop, mic selector, auto-start)
- **Sync** controls and recording table below (pair devices, download, transcribe)

### 6. (Optional) Start at login

Open **System Settings > General > Login Items** and add `HiDock Mic Trigger.app`.

## File output

All files are stored under `~/HiDock/`:

```
~/HiDock/
  Recordings/          # Downloaded MP3 files
  Raw Transcripts/     # Whisper transcription output (.md)
  Speech-to-Text/      # Whisper model cache
  Voice Library/       # Speaker embeddings (when diarization is enabled)
```

## Permissions

The app needs **Microphone** and **Notification** access. macOS will prompt on first launch.
