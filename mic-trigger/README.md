# mic-trigger

Swift CLI that watches a USB mic and keeps the HiDock audio input open via `ffmpeg`, causing the HiDock to auto-record.

## Build

```bash
swiftc MicTrigger.swift -o hidock-mic-trigger
```

## Run

```bash
./hidock-mic-trigger [--mic "Mic Name"] [--audio-index 1] [--list-inputs]
```

### Options

| Flag | Description | Default |
|---|---|---|
| `--mic` | Name of the USB mic to watch | `Samson Q2U Microphone` |
| `--audio-index` | AVFoundation audio index of the HiDock input | `1` |
| `--list-inputs` | List all audio input devices and exit | — |

## How it works

1. Polls CoreAudio every 250ms checking `kAudioDevicePropertyDeviceIsRunningSomewhere` on the trigger mic
2. Debounces state changes over 1 second (4 samples)
3. When the mic becomes active, launches `ffmpeg` to capture from the HiDock audio input and discard it (`-f null -`)
4. When the mic goes idle, stops `ffmpeg` and releases the HiDock input
5. On startup, kills any orphaned `ffmpeg` processes from previous crashed sessions
6. Handles device ID changes (e.g. USB reconnection) by refreshing the CoreAudio device lookup

## Requirements

- macOS 13+
- `ffmpeg` at `/opt/homebrew/bin/ffmpeg` (Homebrew)
- Microphone permission granted for Terminal

## Notes

- Output is line-buffered (`setlinebuf(stdout)`) for reliable piping to the menu bar app
- The CLI is typically launched and managed by the `hidock-mic-trigger` menu bar app rather than run standalone
