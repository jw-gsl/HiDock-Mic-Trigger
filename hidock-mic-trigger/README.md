# HiDock Mic Trigger

Menu bar app that launches and controls the `hidock-mic-trigger` CLI.

## What it does

- Start/Stop the CLI from the menu bar or the app window
- Select the trigger mic from a dropdown (window or menu bar submenu)
- macOS notifications when recording starts and stops
- Auto-restart on crash (up to 3 retries)
- Auto-start on launch (configurable)
- Uptime display
- Dock icon hides when the window is closed; menu bar icon stays
- Open log files from the window
- Builds the CLI automatically if the binary is missing

## Build (Xcode)

Open the project:

`hidock-mic-trigger.xcodeproj`

Then Build & Run.

## Build (CLI)

```bash
xcodegen generate
xcodebuild -project hidock-mic-trigger.xcodeproj -scheme hidock-mic-trigger -configuration Release build
```

## Run

If you've copied the app to `~/Applications`:

```bash
open "$HOME/Applications/HiDock Mic Trigger.app"
```

Or double-click:

`Run Menubar.command`

## Notes

- The app looks for the CLI binary at `mic-trigger/hidock-mic-trigger` relative to the repo root
- You can override the path with the environment variable `HIDOCK_MIC_TRIGGER_PATH`
- Trigger mic selection and auto-start preference are saved in UserDefaults
