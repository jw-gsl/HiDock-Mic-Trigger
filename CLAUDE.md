# HiDock Tools - Agent Instructions

## Development Workflow — IMPORTANT

**NEVER commit directly to `main`.** All changes must go through feature branches and pull requests.

### For any code change:
1. Create a feature branch: `git checkout -b feature/<short-description>`
2. Make changes and commit to the feature branch
3. Push and create a PR: `gh pr create`
4. The user reviews and merges when ready

### BOTH PLATFORMS — ALWAYS
**Every UI or feature change must be applied to BOTH the macOS app AND the Windows app at the same time.** Do not make changes to one platform without updating the other. The Windows app (`Windows-App/`) should always match the macOS app in layout, features, and behavior.

**After any feature change, update `PARITY.md`** — the cross-platform feature checklist. This is the source of truth for what exists on each platform. The PR template includes a parity checkbox as a reminder.

### For testing on Mac:
- Build with **Debug** configuration — deploys to `~/Applications/HiDock Mic Trigger Dev.app` (orange icon, "DEV" label)
- Debug builds never touch the production app at `/Applications/`
- The production app continues running while you test

### For testing Windows changes:
- Push the feature branch, then the user can build locally or wait for PR merge to trigger CI

### CI/CD:
- `build-macos.yml` — builds self-contained .app on push to `main`
- `build-windows.yml` — builds HiDock.exe on push to `main`
- `release.yml` — manual trigger to create a GitHub Release with both platform builds
- `test.yml` — Python tests, Swift tests, and linting on push to `main` and PRs

## Build & Deploy (macOS App)

The main app is `hidock-mic-trigger/` — a Swift desktop app with menu bar integration, built with XcodeGen.

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
   # Note: the build output may be named "hidock-mic-trigger.app" — rename it
   cp -R "/tmp/hidock-build/Build/Products/Release/hidock-mic-trigger.app" "/Applications/HiDock Mic Trigger.app"
   ```

4. **Re-sign the app:**
   ```bash
   codesign --force --sign - "/Applications/HiDock Mic Trigger.app/Contents/MacOS/hidock-mic-trigger"
   codesign --force --sign - "/Applications/HiDock Mic Trigger.app"
   ```

5. **Register with Launchpad:**
   ```bash
   /System/Library/Frameworks/CoreServices.framework/Versions/Current/Frameworks/LaunchServices.framework/Versions/Current/Support/lsregister -f "/Applications/HiDock Mic Trigger.app"
   ```

6. **Relaunch:**
   ```bash
   open -a "/Applications/HiDock Mic Trigger.app"
   ```

**IMPORTANT:** The canonical install location is `/Applications/HiDock Mic Trigger.app`. Never install to `~/Applications/` or use the old bundle name `hidock-mic-trigger.app`. The LaunchAgent (`~/Library/LaunchAgents/com.hidock.tools.mic-trigger.plist`) is configured to launch from `/Applications/` at login.

## Project Structure

- `hidock-mic-trigger/` — macOS desktop app (Swift, Xcode) with menu bar presence. Unified UI for mic trigger, USB sync, transcription.
- `mic-trigger/` — Swift CLI that watches USB mic and keeps HiDock input open via ffmpeg.
- `usb-extractor/` — Python USB extractor for downloading recordings from HiDock devices and importing audio from generic USB volumes (mass-storage recorders, SD cards, etc.).
- `transcription-pipeline/` — Python transcription pipeline using OpenAI Whisper with Apple MPS acceleration.
- `Windows-App/` — PyQt6 desktop app (Windows port). Includes Device Manager for pairing HiDock and volume devices.
- `Windows-Script/` — Python USB extractor and background watcher for Windows. Supports both HiDock and volume devices.

## Build & Run (Windows App)

The Windows app is `Windows-App/` — a PyQt6 desktop app.

### Setup

```cmd
cd Windows-App
setup.bat
```

### Run

```cmd
cd Windows-App
run.bat
```

### Build standalone .exe

```cmd
cd Windows-App
build.bat
```

## Testing

### Swift tests (macOS)

```bash
xcodebuild test -project hidock-mic-trigger/hidock-mic-trigger.xcodeproj -scheme hidock-mic-trigger
```

### Python tests (all platforms)

```bash
# USB extractor tests (88 tests)
python -m pytest usb-extractor/tests/ -q

# Windows app tests (45 tests)
python -m pytest Windows-App/tests/ -q
```

Run both before pushing. CI runs them on PRs via `test.yml`.

## Device Identity System

Devices are identified by string `deviceId` values, not integer product IDs:

- HiDock devices: `"hidock:<productId>"` (e.g. `"hidock:45068"`)
- Volume devices: `"volume:<volumeName>"` (e.g. `"volume:ZOOM_H1"`)

Key types:
- `syncDeviceConnected: [String: Bool]` (macOS) / `dict[str, bool]` (Windows) — keyed by `deviceId`
- `syncFilterDeviceId: String?` / `Optional[str]` — current filter selection

When adding sync features, always branch on device type to call the correct extractor commands:
- HiDock: `status`, `download`, `download-new`, `mark-downloaded`
- Volume: `volume-status`, `volume-import`, `volume-import-new`, `mark-downloaded --volume-name`

## Key Architecture Notes

- **macOS extractor**: `usb-extractor/extractor.py` — called as subprocess by Swift app
- **Windows extractor**: `Windows-Script/extractor.py` — called via `core/usb_sync.py:run_extractor()`
- **`run_extractor()` returns a parsed dict**, not raw JSON — don't double-parse
- **QSettings is NOT thread-safe** — always load paired devices on the main thread before spawning background threads
- **PyQt6 `itemData()` returns Python objects directly**, not QVariant — use `str(raw)` for safety
