# hidock-tools

Small utilities for working with the HiDock device.

## hidock-mic-trigger

A macOS Swift CLI that watches a USB mic being used and holds the HiDock mic input open via `ffmpeg`, causing the HiDock to auto-record.

### Build

```bash
cd "mic-trigger"
swiftc MicTrigger.swift -o hidock-mic-trigger
```

### Run

```bash
./hidock-mic-trigger
```

### Requirements

- macOS
- `ffmpeg` installed (Homebrew recommended)
- Microphone permission granted for Terminal
