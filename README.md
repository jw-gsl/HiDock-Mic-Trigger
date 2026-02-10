# HiDock Mic Trigger

Automatically trigger HiDock auto-recording when you're using an external USB microphone.

## The problem

The [HiDock](https://www.hidock.com) has a built-in mic and can auto-record meetings when it detects audio input. But if you use a higher-quality USB mic (like a Samson Q2U, Blue Yeti, etc.), the HiDock never sees any audio on its own input — so auto-recording doesn't kick in.

## How this solves it

This tool watches your USB mic via CoreAudio. When it detects the mic is in use (e.g. you joined a call), it silently opens the HiDock's audio input using `ffmpeg`. The HiDock sees activity on its mic and triggers auto-recording. When the USB mic goes idle, the tool releases the HiDock input and recording stops.

The result: you get HiDock auto-recording while using whatever mic you prefer.

## Features

- **Mic selector** — pick your trigger mic from a dropdown in the app window or menu bar (no code editing needed)
- **Notifications** — get a macOS notification when recording starts and stops
- **Auto-restart** — if the CLI crashes, the app restarts it automatically (up to 3 retries)
- **Auto-start on launch** — configurable; enabled by default
- **Uptime display** — see how long the trigger has been running
- **Dock icon hiding** — closing the window hides the dock icon; the menu bar icon stays

## What's included

| Folder | Description |
|---|---|
| `mic-trigger/` | Swift CLI that does the actual watching and ffmpeg control |
| `hidock-mic-trigger/` | macOS menu bar app that wraps the CLI with a full UI |

## Setup

### Prerequisites

- macOS 13+
- [Homebrew](https://brew.sh)
- [ffmpeg](https://formulae.brew.sh/formula/ffmpeg): `brew install ffmpeg`
- [XcodeGen](https://github.com/yonaskolb/XcodeGen): `brew install xcodegen`
- Xcode Command Line Tools: `xcode-select --install`

### 1. Find your HiDock audio index

List your audio input devices:

```bash
ffmpeg -f avfoundation -list_devices true -i "" 2>&1 | grep -A 20 "audio devices"
```

Note the **index number** of your HiDock audio input (e.g. `1`). You'll select your trigger mic from the app UI — no need to note its name here.

### 2. Build

```bash
# Build the CLI
cd mic-trigger
swiftc MicTrigger.swift -o hidock-mic-trigger

# Build the menu bar app
cd ../hidock-mic-trigger
xcodegen generate
xcodebuild -project hidock-mic-trigger.xcodeproj -scheme hidock-mic-trigger -configuration Release build
```

### 3. Install the menu bar app

```bash
cp -R ~/Library/Developer/Xcode/DerivedData/hidock-mic-trigger-*/Build/Products/Release/hidock-mic-trigger.app \
  ~/Applications/"HiDock Mic Trigger.app"
```

Then open `HiDock Mic Trigger.app` from `~/Applications`. It will:
- Appear in the menu bar with a waveform icon
- Show a window where you can select your trigger mic, start/stop, and view uptime
- Auto-start the trigger on launch (configurable)
- Send notifications when recording starts and stops

### 4. Select your trigger mic

Use the **Trigger Mic** dropdown in the app window or the menu bar submenu to pick which USB mic triggers the HiDock. Your selection is saved automatically.

### 5. (Optional) Start at login

Open **System Settings > General > Login Items** and add `HiDock Mic Trigger.app`.

## How it works (under the hood)

1. The CLI polls CoreAudio every 250ms to check if your USB mic's `kAudioDevicePropertyDeviceIsRunningSomewhere` flag is set
2. When the mic becomes active (debounced over 1 second), it launches `ffmpeg` to silently capture from the HiDock's audio input and discard it (`-f null -`)
3. This makes the HiDock think its mic is in use, which triggers auto-recording
4. When the USB mic goes idle, ffmpeg is stopped and the HiDock input is released
5. The menu bar app monitors the CLI output and sends macOS notifications on state changes

## Permissions

The app needs **Microphone** and **Notification** access. macOS will prompt you on first launch. Microphone access is required for `ffmpeg` to open the HiDock audio input.
