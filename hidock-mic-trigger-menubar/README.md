# hidock-mic-trigger-menubar

Menu bar wrapper app that launches and controls the `hidock-mic-trigger` CLI.

## What it does

- Starts/stops the CLI
- Shows a menu bar item
- Optional auto-start on launch
- Opens logs
- Builds the CLI automatically if missing

## Build (Xcode)

Open the project:

`hidock-mic-trigger-menubar.xcodeproj`

Then Build & Run.

## Build (CLI)

```bash
xcodebuild -project hidock-mic-trigger-menubar.xcodeproj -scheme hidock-mic-trigger-menubar -configuration Debug build
```

## Run

If you’ve copied the app to `~/Applications`:

```bash
open "$HOME/Applications/hidock-mic-trigger-menubar.app"
```

Or double-click:

`Run Menubar.command`

## Notes

- The app expects the CLI binary at:
  `/Users/jameswhiting/_git/hidock-tools/mic-trigger/hidock-mic-trigger`
- You can override the path with the environment variable:
  `HIDOCK_MIC_TRIGGER_PATH`
