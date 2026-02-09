# mic-trigger

Swift CLI that watches a USB mic being used and keeps the HiDock mic input open via `ffmpeg`, causing the HiDock to auto-record.

## Build

```bash
swiftc MicTrigger.swift -o hidock-mic-trigger
```

## Run

```bash
./hidock-mic-trigger
```

## Requirements

- macOS
- `ffmpeg` installed (Homebrew recommended)
- Microphone permission granted for Terminal

## Configuration

The device names and HiDock audio index are hard-coded in `MicTrigger.swift`:

- USB mic name: `Samson Q2U Microphone`
- HiDock AVFoundation audio index: `1`

Update these if your device names differ.
