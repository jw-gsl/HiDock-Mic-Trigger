# HiDock USB Extractor — Windows

Windows-compatible version of the HiDock USB recording extractor. Pulls `.hda` recordings directly from the HiDock over USB and saves them as `.mp3` files.

## Prerequisites

- Windows 10/11
- Python 3.10+ (https://www.python.org/downloads/) — check "Add Python to PATH" during install
- Zadig (https://zadig.akeo.ie/) — for installing the WinUSB driver

## Setup

### 1. Install the WinUSB driver

The HiDock uses a custom USB protocol. Windows needs the WinUSB driver bound to it:

1. Plug in your HiDock
2. Open Zadig
3. Select **HiDock_H1** from the device dropdown (enable "List All Devices" if needed)
4. Set the target driver to **WinUSB**
5. Click **Replace Driver** (or **Install Driver**)

### 2. Run setup

Double-click `setup.bat` or run from Command Prompt:

```cmd
setup.bat
```

This creates a Python virtual environment and installs `pyusb` + `libusb-package`.

### 3. Download recordings

Double-click `run.bat` or:

```cmd
run.bat
```

This downloads all new recordings from the HiDock into the `out\` folder.

## Background watcher

For a machine where the HiDock is always docked, the watcher runs persistently and auto-downloads completed recordings.

### How it works

1. Polls the device every 5 minutes (configurable) for its file list
2. If new recordings are found, waits 30 seconds then takes a second snapshot
3. Compares file sizes between both snapshots — only downloads files whose size is **stable** (i.e. the recording has finished)
4. Files still growing (actively recording) are skipped and picked up on the next cycle
5. If the device is unreachable or busy, backs off exponentially and retries

This means it will **never** try to download a recording that is still in progress.

### Running the watcher

Double-click `watcher.bat` or:

```cmd
watcher.bat
```

Options:

```cmd
watcher.bat --poll-interval 600      # poll every 10 minutes instead of 5
watcher.bat --stabilise-delay 60     # wait 60s between snapshots instead of 30
watcher.bat --verbose                # debug-level logging
```

Logs are written to `watcher.log` (rotates at 5 MB, keeps 3 backups).

### Auto-start on login

To have the watcher start automatically when you log in:

1. Press `Win+R`, type `shell:startup`, press Enter
2. Right-click in the folder > New > Shortcut
3. Location: `C:\path\to\Windows-Script\watcher.bat`
4. Name it "HiDock Watcher"

Or via Task Scheduler for headless/background operation:

```cmd
schtasks /create /tn "HiDock Watcher" /tr "\"%CD%\watcher.bat\"" /sc onlogon /rl highest
```

## CLI usage

Activate the venv first:

```cmd
.venv\Scripts\activate.bat
```

Then use any command from `extractor.py`:

```cmd
python extractor.py status
python extractor.py list-files
python extractor.py download 2026Mar09-131439-Rec39.hda
python extractor.py download-new
python extractor.py set-output C:\Users\you\HiDock-Recordings
```

## AI agent setup

If you're using an AI coding agent (Claude Code, Codex, Cursor, Windsurf, Devin, etc.) to set up this tool, see **[AGENTS.md](AGENTS.md)** for step-by-step instructions covering environment setup, driver installation guidance, running the extractor, troubleshooting, and Task Scheduler (cron) configuration.

## Differences from macOS version

- Uses `libusb-package` to bundle libusb DLLs (no manual libusb install needed)
- Skips kernel driver detach (not applicable on Windows)
- Temp files use `%TEMP%` instead of `/tmp`
- Requires WinUSB driver via Zadig instead of macOS kext permissions
- Includes background watcher with recording-safe polling

## Known Windows issues and debugging

This section documents every potential problem we've identified. When first setting up on a Windows machine, work through these in order.

### 1. Python not found or wrong version

**Symptom:** `'python' is not recognized` or `SyntaxError` on startup.

**Cause:** Python not installed, not on PATH, or version is below 3.10. This codebase uses `int | None` union type syntax which requires Python 3.10+.

**Fix:**
```cmd
python --version
```
If this doesn't show 3.10+, install from https://www.python.org/downloads/. During installation, **check "Add Python to PATH"**. If Python is installed but not on PATH, the installer has an option to modify PATH, or add it manually via System > Environment Variables.

### 2. Zadig driver not installed / wrong driver selected

**Symptom:** `HiDock device 4310:45068 not found` even though the dock is plugged in.

**Cause:** Windows assigned its default driver to the HiDock instead of WinUSB. Without WinUSB, libusb cannot communicate with the device.

**Fix:**
1. Open Zadig
2. Go to Options > **List All Devices**
3. Find **HiDock_H1** in the dropdown
4. Ensure the target driver (right side) says **WinUSB (v6.x.x.x)**
5. Click **Replace Driver**

**Watch out for:** If the device shows up with a different name (not "HiDock_H1"), check the USB IDs in Zadig — they should be `10D6:B00C` (hex for 4310:45068). If the device shows multiple interfaces, apply WinUSB to **Interface 0**.

### 3. Another application has the USB device open

**Symptom:** `usb.core.USBError: [Errno 13] Access denied` or `[Errno 16] Resource busy`.

**Cause:** HiNotes (in Chrome/Edge) or another application has already claimed the USB interface. Only one application can talk to the device at a time.

**Fix:** Close the HiNotes browser tab, then retry. If you're not sure what's using it, check Task Manager for any Chrome/Edge processes that might have WebUSB access.

### 4. libusb DLL not found

**Symptom:** `usb.core.NoBackendError: No backend available` or `OSError: cannot load libusb`.

**Cause:** The `libusb-package` pip package should bundle the DLL, but occasionally the bundled DLL doesn't match the system architecture or fails to load.

**Fix (in order of escalation):**

a. Reinstall the package:
```cmd
pip install --force-reinstall libusb-package
```

b. Verify the DLL is there:
```cmd
python -c "import libusb_package; print(libusb_package.find_library())"
```
This should print a path to `libusb-1.0.dll`. If it prints `None`, the package is broken.

c. Manual libusb install as fallback: Download `libusb-1.0.dll` from https://github.com/libusb/libusb/releases, place it in `C:\Windows\System32\` (64-bit) or alongside `python.exe` in the venv.

### 5. USB read size too large

**Symptom:** `usb.core.USBError: [Errno 5] Input/Output Error` or timeouts during file transfer that work fine on macOS.

**Cause:** Some Windows USB host controllers or WinUSB configurations reject bulk reads larger than 64 KB. The default `USB_READ_SIZE` is 512000 (500 KB).

**Fix:** Open `extractor.py` and change line ~57:
```python
USB_READ_SIZE = 512000
```
to:
```python
USB_READ_SIZE = 65536
```
If that still fails, try `16384`. This will make transfers slower but more compatible.

### 6. PowerShell execution policy blocks venv activation

**Symptom:** `Activate.ps1 cannot be loaded because running scripts is disabled on this system.`

**Cause:** PowerShell's default execution policy blocks `.ps1` scripts.

**Fix:**
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```
Or just use `cmd.exe` instead of PowerShell — `activate.bat` works without this issue.

### 7. Device works once then stops responding

**Symptom:** First `status` or `download` works, subsequent calls fail with timeouts or IO errors.

**Cause:** The USB interface was not properly released after the previous session, or the device needs a moment between connections.

**Fix:**
- Unplug and replug the HiDock, then retry
- If using the watcher, increase `--poll-interval` to give the device more breathing room
- Check that only one instance of the extractor/watcher is running at a time

### 8. Antivirus blocks USB access

**Symptom:** Extractor worked during setup but randomly fails later, or `Access denied` errors appear intermittently.

**Cause:** Some antivirus software (especially Windows Defender with "Controlled folder access" or third-party AV) monitors USB device access and may block or throttle it.

**Fix:**
- Add the `Windows-Script` folder and the Python venv to your antivirus exclusion list
- In Windows Security > Virus & threat protection > Ransomware protection, add the folder to "allowed apps" if Controlled folder access is enabled

### 9. HiDock firmware differences

**Symptom:** `list-files` returns empty or garbled data, or downloads produce corrupted MP3 files.

**Cause:** The USB protocol was reverse-engineered from a specific HiDock H1 firmware version. A different firmware version may use slightly different framing, command IDs, or file formats.

**Fix:** This is harder to diagnose remotely. Steps:
1. Run `python extractor.py status --timeout-ms 10000` with a longer timeout
2. If `list-files` returns data but downloads fail, try the alternative download methods: `pull-block`, `pull-partial`, `pull-transfer`
3. Note the exact error and file it as an issue — the protocol may need updating

### 10. Long file paths on Windows

**Symptom:** `FileNotFoundError` or `OSError` when writing to deeply nested output directories.

**Cause:** Windows has a default 260-character path limit.

**Fix:** Either use a short output path:
```cmd
python extractor.py set-output C:\HiDock
```
Or enable long paths in Windows (requires admin):
```cmd
reg add "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled /t REG_DWORD /d 1 /f
```

### 11. Watcher uses too much CPU or memory

**Symptom:** `watcher.py` consuming noticeable resources while idle.

**Cause:** Shouldn't happen — the watcher sleeps between polls. If it does, it's likely stuck in a tight retry loop.

**Fix:** Check `watcher.log` for repeated errors. Increase the poll interval:
```cmd
watcher.bat --poll-interval 600
```
