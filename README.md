# HiDock Mic Trigger

Automatically trigger HiDock auto-recording when you're using an external USB microphone.

## The problem

The [HiDock](https://www.hidock.com) has a built-in mic and can auto-record meetings when it detects audio input. But if you use a higher-quality USB mic (like a Samson Q2U, Blue Yeti, etc.), the HiDock never sees any audio on its own input — so auto-recording doesn't kick in.

## How this solves it

This tool watches your USB mic via CoreAudio. When it detects the mic is in use (e.g. you joined a call), it silently opens the HiDock's audio input using `ffmpeg`. The HiDock sees activity on its mic and triggers auto-recording. When the USB mic goes idle, the tool releases the HiDock input and recording stops.

The result: you get HiDock auto-recording while using whatever mic you prefer.

## What's included

| Folder | Description |
|---|---|
| `mic-trigger/` | Swift CLI that does the actual watching and ffmpeg control |
| `hidock-mic-trigger/` | macOS menu bar app that wraps the CLI with Start/Stop controls |

## Setup

### Prerequisites

- macOS 13+
- [Homebrew](https://brew.sh)
- [ffmpeg](https://formulae.brew.sh/formula/ffmpeg): `brew install ffmpeg`
- [XcodeGen](https://github.com/yonaskolb/XcodeGen): `brew install xcodegen`
- Xcode Command Line Tools: `xcode-select --install`

### 1. Find your device names

List your audio input devices:

```bash
ffmpeg -f avfoundation -list_devices true -i "" 2>&1 | grep -A 20 "audio devices"
```

Note two things:
- The **name** of your USB mic (e.g. `Samson Q2U Microphone`)
- The **index number** of your HiDock audio input (e.g. `1`)

### 2. Configure the CLI

Edit `mic-trigger/MicTrigger.swift` and update the constants near the top:

```swift
let usbMicName = "Samson Q2U Microphone"  // your USB mic name
let hiDockAudioIndex = 1                   // HiDock's audio index from ffmpeg
```

### 3. Build

```bash
# Build the CLI
cd mic-trigger
swiftc MicTrigger.swift -o hidock-mic-trigger

# Build the menu bar app
cd ../hidock-mic-trigger
xcodegen generate
xcodebuild -project hidock-mic-trigger.xcodeproj -scheme hidock-mic-trigger -configuration Release build
```

### 4. Install the menu bar app

```bash
cp -R ~/Library/Developer/Xcode/DerivedData/hidock-mic-trigger-*/Build/Products/Release/hidock-mic-trigger.app \
  ~/Applications/"HiDock Mic Trigger.app"
```

Then open `HiDock Mic Trigger.app` from `~/Applications`. It will:
- Appear in the menu bar with a waveform icon
- Auto-start the trigger on launch (configurable)
- Show Start/Stop controls, uptime, and log access in its window

### 5. (Optional) Start at login

Open **System Settings > General > Login Items** and add `HiDock Mic Trigger.app`, or use the app — it will add itself.

## How it works (under the hood)

1. The CLI polls CoreAudio every 250ms to check if your USB mic's `kAudioDevicePropertyDeviceIsRunningSomewhere` flag is set
2. When the mic becomes active (debounced over 1 second), it launches `ffmpeg` to silently capture from the HiDock's audio input and discard it (`-f null -`)
3. This makes the HiDock think its mic is in use, which triggers auto-recording
4. When the USB mic goes idle, ffmpeg is stopped and the HiDock input is released

## Permissions

The app needs **Microphone** access. macOS will prompt you on first launch. This is required for `ffmpeg` to open the HiDock audio input.
