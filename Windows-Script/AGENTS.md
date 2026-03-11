# HiDock USB Extractor — Agent Setup Instructions

This file provides instructions for any AI coding agent (Claude Code, Codex, Cursor, Windsurf, Devin, etc.) to fully set up and run the HiDock USB extractor on a Windows machine.

## Overview

This tool extracts audio recordings from a HiDock H1 device over USB. It communicates directly via the USB protocol (pyusb + libusb) and saves `.hda` recordings as `.mp3` files.

## Automated Setup Steps

Run these commands in order from the `Windows-Script` directory. All commands assume Windows with `cmd.exe` or PowerShell.

### Step 1: Verify Python is installed

```cmd
python --version
```

- Requires Python 3.10 or later.
- If Python is not found, tell the user: "Python 3.10+ is required. Download from https://www.python.org/downloads/ and check 'Add Python to PATH' during installation. Then re-run setup."
- Do NOT attempt to install Python silently — the user needs to do this themselves.

### Step 2: Create virtual environment

```cmd
python -m venv .venv
```

### Step 3: Activate the virtual environment

```cmd
.venv\Scripts\activate.bat
```

Or in PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

If PowerShell blocks the script with an execution policy error, run:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Step 4: Install Python dependencies

```cmd
pip install -r requirements.txt
```

This installs:
- `pyusb` — USB communication library
- `libusb-package` — bundles the libusb DLLs so they don't need to be installed separately on Windows

### Step 5: Verify the installation

```cmd
python -c "import usb.core; import libusb_package; print('OK')"
```

If this prints `OK`, the Python side is ready.

### Step 6: WinUSB driver (requires user action)

The HiDock device needs the WinUSB driver bound to it. This CANNOT be done programmatically — it requires the user to use Zadig.

Tell the user:

> Before the extractor can talk to your HiDock, you need to install the WinUSB driver using Zadig:
>
> 1. Download Zadig from https://zadig.akeo.ie/
> 2. Plug in your HiDock via USB
> 3. Open Zadig
> 4. From the device dropdown, select **HiDock_H1** (if it doesn't appear, go to Options > List All Devices)
> 5. Make sure the target driver says **WinUSB**
> 6. Click **Replace Driver** (or **Install Driver**)
>
> This only needs to be done once. After that, the extractor can communicate with the HiDock.

### Step 7: Test device connectivity

Once the user confirms the driver is installed:

```cmd
python extractor.py status
```

- If the output JSON shows `"connected": true`, the device is ready.
- If it shows `"connected": false` with an error about the device not being found, the HiDock is either not plugged in or the WinUSB driver was not installed correctly.

## Running the Extractor

### Download all new recordings

```cmd
python extractor.py download-new
```

This checks the device for recordings, compares against `state.json`, and downloads anything new to the `out\` directory.

### Download a specific recording

```cmd
python extractor.py download <filename>
```

Example: `python extractor.py download 2026Mar09-131439-Rec39.hda`

### List recordings on the device

```cmd
python extractor.py list-files
```

### Check sync status

```cmd
python extractor.py status
```

Returns JSON with device connection state and per-recording download status.

### Change the output directory

```cmd
python extractor.py set-output C:\Users\username\HiDock-Recordings
```

This persists the output directory to `config.json`.

## Background Watcher (Recommended for always-docked setups)

If the HiDock is always plugged in, the watcher is the best approach. It polls safely and avoids interfering with live recordings.

### Start the watcher

```cmd
python watcher.py
```

Or with custom settings:

```cmd
python watcher.py --poll-interval 600 --stabilise-delay 60 --verbose
```

**How it avoids recording conflicts:** The watcher takes two file-list snapshots separated by a configurable delay (default 30 seconds). Only files whose size is identical across both snapshots are downloaded. A file that is still being recorded will have a changing size and will be skipped until the next cycle.

### Auto-start the watcher on login

```cmd
schtasks /create /tn "HiDock Watcher" /tr "\"%CD%\watcher.bat\"" /sc onlogon /rl highest
```

### Watcher logs

Logs are written to `watcher.log` in the same directory. If something fails, read this file first. It rotates at 5 MB and keeps 3 backups.

## File Layout

| File | Purpose |
|---|---|
| `extractor.py` | Main extractor — all USB protocol logic and CLI |
| `watcher.py` | Background watcher — polls for completed recordings and downloads them |
| `extract_filenames.py` | Helper to parse `.hda` filenames from HiNotes exports |
| `requirements.txt` | Python dependencies (pyusb + libusb-package) |
| `config.json` | Persisted config (output directory) |
| `state.json` | Download state tracker (created automatically) |
| `watcher.log` | Watcher log file (created when watcher runs) |
| `setup.bat` | One-click setup for humans (creates venv + installs deps) |
| `run.bat` | One-click run for humans (activates venv + runs download-new) |
| `watcher.bat` | One-click watcher for humans (activates venv + runs watcher) |
| `out\` | Default output directory for downloaded .mp3 files |

## Troubleshooting

For the full list of known Windows issues with symptoms, causes, and fixes, see the **"Known Windows issues and debugging"** section in [README.md](README.md). It covers:

1. Python not found or wrong version (needs 3.10+ for `int | None` syntax)
2. Zadig/WinUSB driver not installed or wrong interface selected
3. Another application (HiNotes/Chrome) holding the USB device
4. libusb DLL not found (`NoBackendError`)
5. USB read size too large for some Windows USB controllers (may need to reduce from 512 KB to 64 KB)
6. PowerShell execution policy blocking venv activation
7. Device works once then stops responding (interface not released)
8. Antivirus blocking USB access
9. HiDock firmware version differences
10. Long file path limits on Windows (260-char default)
11. Watcher CPU/memory usage

Quick-reference table for the most common errors:

| Error | Likely cause | Agent action |
|---|---|---|
| `ModuleNotFoundError: No module named 'usb'` | Venv not activated or deps missing | Run `pip install -r requirements.txt` inside the venv |
| `HiDock device 4310:45068 not found` | WinUSB driver not installed | Tell user to run Zadig (cannot be automated) |
| `usb.core.USBError: [Errno 13] Access denied` | Another app has the device open | Tell user to close HiNotes/Chrome tabs |
| `usb.core.NoBackendError` | libusb DLL missing | Run `pip install --force-reinstall libusb-package` |
| `usb.core.USBError: [Errno 5] Input/Output Error` | USB read size too large | Change `USB_READ_SIZE` to `65536` in `extractor.py` |
| `python` not found | Not installed or not on PATH | Tell user to install Python 3.10+ |
| PowerShell execution policy error | Script execution disabled | Run `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` |
