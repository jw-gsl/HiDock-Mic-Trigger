# HiDock Tools - Agent Instructions

## Build & Deploy (macOS Menu Bar App)

The main app is `hidock-mic-trigger/` — a Swift menu bar app built with XcodeGen.

### Build

```bash
cd hidock-mic-trigger
xcodebuild -project hidock-mic-trigger.xcodeproj -scheme hidock-mic-trigger -configuration Release -derivedDataPath /tmp/hidock-build
```

### Deploy after ANY code change

After building, you MUST complete these steps to avoid stale app copies running:

1. **Kill running instances:**
   ```bash
   pkill -f "hidock-mic-trigger" || true
   ```

2. **Remove ALL old copies** (the app has historically ended up in multiple locations):
   ```bash
   rm -rf "/Applications/HiDock Mic Trigger.app"
   rm -rf "/Applications/hidock-mic-trigger.app"
   rm -rf ~/Applications/"HiDock Mic Trigger.app"
   ```

3. **Install the fresh build to /Applications/ only:**
   ```bash
   cp -R "/tmp/hidock-build/Build/Products/Release/HiDock Mic Trigger.app" "/Applications/HiDock Mic Trigger.app"
   ```

4. **Relaunch:**
   ```bash
   open -a "/Applications/HiDock Mic Trigger.app"
   ```

**IMPORTANT:** The canonical install location is `/Applications/HiDock Mic Trigger.app`. Never install to `~/Applications/` or use the old bundle name `hidock-mic-trigger.app`. The LaunchAgent (`~/Library/LaunchAgents/com.hidock.tools.mic-trigger.plist`) is configured to launch from `/Applications/` at login.

## Project Structure

- `hidock-mic-trigger/` — macOS menu bar app (Swift, Xcode). Unified UI for mic trigger, USB sync, transcription.
- `mic-trigger/` — Swift CLI that watches USB mic and keeps HiDock input open via ffmpeg.
- `usb-extractor/` — Python USB extractor for downloading recordings from HiDock.
- `transcription-pipeline/` — Python transcription pipeline using OpenAI Whisper with Apple MPS acceleration.
- `Windows-App/` — PyQt6 desktop app (Windows port).
- `Windows-Script/` — Python USB extractor and background watcher for Windows.

## Testing

Swift tests: `xcodebuild test -project hidock-mic-trigger/hidock-mic-trigger.xcodeproj -scheme hidock-mic-trigger`
Python tests run automatically as a pre-build script (requires venvs in `usb-extractor/.venv` and `transcription-pipeline/.venv`).
