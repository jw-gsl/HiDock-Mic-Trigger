# HiDock USB Extractor

Python tool for downloading recordings from HiDock devices over USB and importing audio files from generic USB volumes (recorders, SD cards, external drives).

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
