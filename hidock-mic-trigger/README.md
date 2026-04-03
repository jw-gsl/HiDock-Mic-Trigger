# HiDock Mic Trigger — macOS App

macOS desktop app that provides a unified interface for the HiDock mic trigger, USB sync, and transcription pipeline. Lives in the menu bar for quick access, with a full-featured window for managing recordings, transcriptions, and device settings.

## Downloads

| Download | Build Status |
|----------|-------------|
| [Download HiDock Mic Trigger.app](https://nightly.link/jw-gsl/HiDock-Mic-Trigger/workflows/build-macos/main/HiDock-Mic-Trigger-macOS.zip) (~40 MB zip) | [![Build macOS App](https://github.com/jw-gsl/HiDock-Mic-Trigger/actions/workflows/build-macos.yml/badge.svg)](https://github.com/jw-gsl/HiDock-Mic-Trigger/actions/workflows/build-macos.yml) |

> The downloadable .app is self-contained — it includes bundled Python venvs for USB sync and transcription. No additional setup required. The speech recognition model (~550 MB) is downloaded on first use.

## Features

- **Mic Trigger** — Start/Stop the trigger CLI, select trigger mic from dropdown, auto-start on launch
- **USB Sync** — Pair HiDock devices, browse recordings, download over USB, auto-download new recordings
- **Transcription** — Transcribe recordings using whisper.cpp, real-time progress in the table
- **Device detection** — Shows which app holds the HiDock if unavailable (e.g. "Device held by Microsoft Edge")
- **Device icons** — H1 (dock) and P1 (recorder) shown with distinct icons in the window
- **Speaker diarization** — Identify speakers in recordings with voice library support
- **Notification preferences** — Configurable notifications for transcription, downloads, and mic changes with click-to-open actions
- **Onboarding wizard** — First-run setup guides device pairing and model download
- **Dark/Light/Auto appearance** — Follows system or user preference
- **Auto-restart** — CLI auto-restarts on crash (up to 3 retries)
- **Auto-start** — Configurable via LaunchAgent to start at login
- **Menu bar presence** — Status item shows trigger state and connected devices; full window for all controls

## Build

### Release (production)

```bash
xcodegen generate
xcodebuild -scheme hidock-mic-trigger -configuration Release -derivedDataPath /tmp/hidock-build
```

The post-build script auto-deploys to `/Applications/`, re-signs, and relaunches.

### Debug (dev)

```bash
xcodegen generate
xcodebuild -scheme hidock-mic-trigger -configuration Debug -derivedDataPath /tmp/hidock-build-dev
```

Debug builds deploy to `~/Applications/HiDock Mic Trigger Dev.app` with an orange icon and "HiDock DEV" title, running side-by-side with production.

## CI Build (self-contained .app)

GitHub Actions (`build-macos.yml`) builds a fully self-contained `.app` bundle on Apple Silicon:

1. Builds the Swift app
2. Bundles `usb-extractor/` and `transcription-pipeline/` scripts into `Contents/Resources/`
3. Creates Python venvs inside the bundle with all dependencies
4. Patches venvs for relocatability
5. Code signs everything
6. Uploads as a downloadable artifact

The bundled app uses `transcribe_cpp.py` (whisper.cpp) instead of PyTorch for transcription, keeping the bundle at ~40 MB compressed.

## Window layout

The app window contains:

1. **Mic Trigger strip** (top) — status dot, Start/Stop, trigger mic dropdown, auto-start checkbox
2. **Sync status** — connection status with device icons, folder paths, recording summary
3. **Toolbar rows** — Pair/Unpair, Choose Folder, Refresh, Download, Transcribe buttons
4. **Download / Transcription progress bars** — real-time progress with cancel support
5. **Recording table** — columns for device, status, transcribed, recording name, created date, length, size, output path, and reveal in Finder
6. **Footer bar** — Notifications menu, appearance toggle, update checker, Models, Voice Library, Feedback

## Configuration

- **Repo root**: `defaults write com.hidock.mic-trigger hidockRepoRoot /path/to/hidock-tools`
- **Trigger mic** and **auto-start** preference are saved in UserDefaults
- **Paired devices** and **output folder** are saved in UserDefaults
- The app expects `mic-trigger/hidock-mic-trigger` CLI binary relative to repo root (builds it automatically if missing)
- Override CLI path: set `HIDOCK_MIC_TRIGGER_PATH` environment variable
- When running from a bundled .app, paths resolve from `Bundle.main.resourcePath` automatically

## Dependencies

- `usb-extractor/` — Python venv with pyusb + libusb-package for USB communication
- `transcription-pipeline/` — Python venv with whisper.cpp (pywhispercpp) for transcription
- `mic-trigger/` — Swift CLI binary for CoreAudio mic watching
