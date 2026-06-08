# HiDock USB Extractor

Python tool for downloading recordings from HiDock devices over USB, importing audio files from generic USB volumes (recorders, SD cards, external drives), and syncing Plaud cloud recordings.

All recording sources are app device providers. New provider work should follow [`../docs/ARCHITECTURE-device-providers.md`](../docs/ARCHITECTURE-device-providers.md), especially the shared status JSON, date/duration formats, provider-scoped state keys, and offline cache behavior.

## Setup

```bash
cd usb-extractor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

The extractor is primarily called by the desktop app, but can be used standalone:

### List connected devices

```bash
.venv/bin/python extractor.py list-devices
```

### Check device status and recordings

```bash
.venv/bin/python extractor.py status
```

### Download a specific recording

```bash
.venv/bin/python extractor.py download 2026Mar10-102848-Rec41
```

### Download all new recordings

```bash
.venv/bin/python extractor.py download-new
```

### Mark recordings as downloaded (without downloading)

```bash
.venv/bin/python extractor.py mark-downloaded 2026Mar10-102848-Rec41
```

### Multi-device support

Use `--product-id` to target a specific HiDock when multiple are connected:

```bash
.venv/bin/python extractor.py --product-id 45068 status
```

### Volume device commands

For generic USB volumes (audio recorders, SD cards mounted as drives):

```bash
# Scan for mounted volumes with audio files
.venv/bin/python extractor.py scan-volumes

# Check recordings on a specific volume
.venv/bin/python extractor.py volume-status --volume-name ZOOM_H1

# Import a specific audio file from a volume
.venv/bin/python extractor.py volume-import recording.wav --volume-name ZOOM_H1

# Import all new audio files from a volume
.venv/bin/python extractor.py volume-import-new --volume-name ZOOM_H1

# Mark volume recordings as downloaded
.venv/bin/python extractor.py mark-downloaded --volume-name ZOOM_H1 recording.wav
```

Optional `--subpath` flag scopes scanning to a subfolder on the volume.

### Plaud cloud commands

Plaud auth is owned by the desktop app and passed to the extractor through environment variables. The extractor must not persist Plaud access tokens.

```bash
# Check Plaud recordings for a paired account
PLAUD_ACCESS_TOKEN=... PLAUD_REFRESH_TOKEN=... PLAUD_REGION=us \
  .venv/bin/python extractor.py plaud-status --account-id <account-id>

# Download a specific Plaud recording
PLAUD_ACCESS_TOKEN=... PLAUD_REFRESH_TOKEN=... PLAUD_REGION=us \
  .venv/bin/python extractor.py plaud-download <recording-id> --account-id <account-id>

# Download all new Plaud recordings
PLAUD_ACCESS_TOKEN=... PLAUD_REFRESH_TOKEN=... PLAUD_REGION=us \
  .venv/bin/python extractor.py plaud-download-new --account-id <account-id>
```

`plaud-status` caches the last successful Plaud catalog in `state.json` and returns those cached rows with `connected:false` when Plaud is signed out or unreachable, matching HiDock's disconnected-device behavior.

## USB protocol

- Device USB ID: `4310:45068` (HiDock H1) or `4310:45069` (HiDock P1)
- Commands sent via endpoint 1 (outbound), data received via endpoint 2 (inbound)
- Transfer protocol: filename-based requests for `.hda` files, streamed as MP3 frames
- Header: `12 34`, followed by command byte, request ID, payload length, and payload

## Output

Downloaded recordings are saved as MP3 files to the configured output directory (default `~/HiDock/Recordings/`). State is tracked in `state.json` to avoid re-downloading.

## Requirements

- macOS or Linux
- Python 3.11+
- `pyusb` (installed via requirements.txt)
- USB access to the HiDock device (may require running without SIP restrictions on macOS)
