# HiDock Mic Trigger — Menu Bar App

macOS menu bar app that provides a unified interface for the HiDock mic trigger, USB sync, and transcription pipeline.

## Features

- **Mic Trigger** — Start/Stop the trigger CLI, select trigger mic from dropdown, auto-start on launch
- **USB Sync** — Pair HiDock devices, browse recordings, download over USB, auto-download new recordings
- **Transcription** — Transcribe selected or all recordings using Whisper, real-time progress in the table
- **Notifications** — macOS notifications when recording starts/stops and transcription completes
- **Auto-restart** — CLI auto-restarts on crash (up to 3 retries)
- **Single window** — All controls in one unified window accessed from the menu bar

## Build

### Xcode

Open `hidock-mic-trigger.xcodeproj` and Build & Run.

### Command line

```bash
xcodegen generate
xcodebuild -scheme hidock-mic-trigger -configuration Release build
```

Or for a debug build:

```bash
xcodebuild -scheme hidock-mic-trigger -configuration Debug build
```

## Run

Double-click `Run Menubar.command`, or:

```bash
open ~/Library/Developer/Xcode/DerivedData/hidock-mic-trigger-*/Build/Products/Release/hidock-mic-trigger.app
```

## Window layout

The unified window contains:

1. **Mic Trigger strip** (top) — status, Start/Stop, trigger mic dropdown, auto-start checkbox
2. **Sync status** — connection status, output folder, recording summary
3. **Toolbar rows** — Pair/Unpair, Choose Folder, Refresh, Download, Transcribe buttons
4. **Recording table** — columns for device, status, transcribed, recording name, created date, length, size, output path, and reveal in Finder

The Transcribed column shows:
- Green **✓** button — click to reveal the transcript in Finder
- **XX%** — real-time transcription progress
- **-** — not yet transcribed

## Configuration

- **Repo root**: `defaults write com.hidock.mic-trigger hidockRepoRoot /path/to/hidock-tools`
- **Trigger mic** and **auto-start** preference are saved in UserDefaults
- **Paired devices** and **output folder** are saved in UserDefaults
- The app expects `mic-trigger/hidock-mic-trigger` CLI binary relative to repo root (builds it automatically if missing)
- Override CLI path: set `HIDOCK_MIC_TRIGGER_PATH` environment variable

## Dependencies

- `usb-extractor/` — Python venv with pyusb for USB communication
- `transcription-pipeline/` — Python venv with Whisper and PyTorch for transcription
- `mic-trigger/` — Swift CLI binary for CoreAudio mic watching
