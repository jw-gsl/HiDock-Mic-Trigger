# hidock-tools

Utilities for working with the HiDock device.

## Contents

- `mic-trigger/` — Swift CLI that watches a USB mic and holds the HiDock input open via `ffmpeg`.
- `hidock-mic-trigger/` — Menu bar app (HiDock Mic Trigger) that starts/stops the CLI and shows status.

## Quick start

1. Build the CLI:

```bash
cd "mic-trigger"
swiftc MicTrigger.swift -o hidock-mic-trigger
```

2. Run the CLI:

```bash
./hidock-mic-trigger
```

3. For the menu bar app, see `hidock-mic-trigger/README.md`.
