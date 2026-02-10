# HiDock Mic Trigger

Menu bar app that launches and controls the `hidock-mic-trigger` CLI.

## What it does

- Starts/stops the CLI from the menu bar or the app window
- Shows a menu bar icon with status
- Start/Stop buttons in the app window
- Dock icon hides when the window is closed
- Optional auto-start on launch
- Opens logs
- Builds the CLI automatically if missing

## Build (Xcode)

Open the project:

`hidock-mic-trigger.xcodeproj`

Then Build & Run.

## Build (CLI)

```bash
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

- The app expects the CLI binary at:
  `/Users/jameswhiting/_git/hidock-tools/mic-trigger/hidock-mic-trigger`
- You can override the path with the environment variable:
  `HIDOCK_MIC_TRIGGER_PATH`
