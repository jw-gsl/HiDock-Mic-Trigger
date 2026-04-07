# HiDock Tools

A suite of tools for working with [HiDock](https://www.hidock.com) USB docking stations. Automatically trigger recording with an external mic, download recordings over USB, transcribe locally using Whisper, and extract structured intelligence from every conversation.

## Downloads

| Platform | Download | Build Status |
|----------|----------|-------------|
| **Windows** | [Download HiDock.exe](https://nightly.link/jw-gsl/HiDock-Mic-Trigger/workflows/build-windows/main/HiDock-Windows.zip) | [![Build Windows App](https://github.com/jw-gsl/HiDock-Mic-Trigger/actions/workflows/build-windows.yml/badge.svg)](https://github.com/jw-gsl/HiDock-Mic-Trigger/actions/workflows/build-windows.yml) |
| **macOS** | [Download HiDock Mic Trigger.app](https://nightly.link/jw-gsl/HiDock-Mic-Trigger/workflows/build-macos/main/HiDock-Mic-Trigger-macOS.zip) | [![Build macOS App](https://github.com/jw-gsl/HiDock-Mic-Trigger/actions/workflows/build-macos.yml/badge.svg)](https://github.com/jw-gsl/HiDock-Mic-Trigger/actions/workflows/build-macos.yml) |

> Download links always point to the latest successful build via [nightly.link](https://nightly.link). No GitHub login required. The speech recognition model (~550 MB) is downloaded within the app on first use.

## Components

| Folder | Platform | Description |
|---|---|---|
| `hidock-mic-trigger/` | macOS | Desktop app (Swift/AppKit) — unified UI for mic trigger, USB sync, and transcription |
| `mic-trigger/` | macOS | Swift CLI that watches a USB mic and keeps the HiDock input open via ffmpeg |
| `usb-extractor/` | macOS | Python extractor — downloads from HiDock over USB and imports audio from generic USB volumes |
| `transcription-pipeline/` | macOS | Transcription pipeline — whisper.cpp (bundled) or OpenAI Whisper on MPS (dev) |
| `Windows-App/` | Windows | Desktop app (PyQt6) — Windows port of the macOS app |
| `Windows-Script/` | Windows | Python extractor and background watcher — HiDock USB and volume device support |
| `shared/` | Cross-platform | Python modules for structured transcripts, LLM summarization, knowledge graph, Obsidian sync, config, and hooks |
| `mcp-server/` | Cross-platform | MCP server exposing meeting knowledge to AI agents (Claude Desktop, Cursor, etc.) |
| `docs/` | — | Gap analysis, evolution plan, and architecture documentation |

> **macOS is the primary development platform.** The Windows app is a secondary port. See [Windows-App/PORTING.md](Windows-App/PORTING.md) for the porting workflow.

## How it works

1. **Mic Trigger** — watches your USB mic (e.g. Samson Q2U) via CoreAudio. When it detects the mic is in use, it silently opens the HiDock's audio input using `ffmpeg`, causing the HiDock to auto-record.
2. **USB Sync** — pairs with HiDock devices over USB or generic USB volumes (audio recorders, SD cards) and downloads/imports recordings to a local folder. The Device Manager supports multiple paired devices of both types.
3. **Transcription** — runs Whisper `large-v3-turbo` (via whisper.cpp) to transcribe downloaded recordings to Markdown files with YAML frontmatter. The ~550 MB model is downloaded on first use.
4. **Summarization** (optional) — sends transcripts to an available LLM CLI (`claude`, `codex`, `gemini`, or `ollama`) to extract titles, action items, decisions, key points, and tags. No API keys needed — uses existing AI subscriptions.
5. **Knowledge Graph** — indexes all transcripts into a SQLite database for full-text search, people tracking, and action item management.
6. **Obsidian Sync** (optional) — syncs transcripts into an Obsidian vault with `[[wikilinks]]`, auto-generated person notes, and daily notes integration.
7. **MCP Server** — exposes meeting knowledge to AI agents via the Model Context Protocol. Ask Claude "what did I promise Sarah last week?" and get an answer.
8. **Post-transcription Hooks** — run custom shell commands after transcription (e.g. send a Slack notification, sync to cloud).

All processing happens locally on a single desktop app with menu bar integration (macOS) or system tray (Windows). LLM summarization is optional and uses your existing subscriptions.

## Prerequisites

- macOS 13+
- Apple Silicon (M1/M2/M3/M4) for MPS-accelerated transcription
- [Homebrew](https://brew.sh)
- [ffmpeg](https://formulae.brew.sh/formula/ffmpeg): `brew install ffmpeg`
- [XcodeGen](https://github.com/yonaskolb/XcodeGen): `brew install xcodegen`
- Xcode Command Line Tools: `xcode-select --install`
- Python 3.11+ (for USB extractor, transcription, and shared modules)

## Quick start

### 1. Build the macOS app

```bash
cd hidock-mic-trigger
xcodegen generate
xcodebuild -scheme hidock-mic-trigger -configuration Release build
```

### 2. Set up the USB extractor

```bash
cd usb-extractor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Set up the transcription pipeline

```bash
cd transcription-pipeline
./setup-venv.sh
```

This creates a Python venv with PyTorch, OpenAI Whisper, and verifies MPS availability.

### 4. Find your HiDock audio index

```bash
ffmpeg -f avfoundation -list_devices true -i "" 2>&1 | grep -A 20 "audio devices"
```

Note the index number of your HiDock audio input (e.g. `1`).

### 5. Install and run

```bash
cp -R ~/Library/Developer/Xcode/DerivedData/hidock-mic-trigger-*/Build/Products/Release/hidock-mic-trigger.app \
  ~/Applications/"HiDock Mic Trigger.app"
open ~/Applications/"HiDock Mic Trigger.app"
```

The app opens a unified window with:
- **Mic Trigger** controls at the top (Start/Stop, mic selector, auto-start)
- **Sync** controls and recording table below (pair devices, download, transcribe)

### 6. (Optional) Start at login

Open **System Settings > General > Login Items** and add `HiDock Mic Trigger.app`.

## File output

All files are stored under `~/HiDock/`:

```
~/HiDock/
  Recordings/          # Downloaded MP3 files
  Raw Transcripts/     # Whisper transcription output (.md with YAML frontmatter)
  Speech-to-Text/      # Whisper model cache
  Voice Library/       # Speaker embeddings (when diarization is enabled)
  knowledge.db         # SQLite knowledge graph index (rebuildable from transcripts)
```

### Transcript format

Transcripts are Markdown files with YAML frontmatter containing structured metadata:

```yaml
---
title: "Weekly sync with Sarah and Dev team"
type: meeting
date: 2026-04-05T14:00:00+00:00
duration: 234.5
speakers: [Sarah Chen, James Walsh]
source_device: HiDock H1
source_file: recording.mp3
model: large-v3-turbo
action_items:
  - task: "Review Q2 roadmap draft"
    assignee: Sarah Chen
    due: 2026-04-10
    status: open
decisions:
  - text: "Ship v2.0 by end of April"
    topic: release
key_points: ["Budget approved", "Moving standup to 10am"]
tags: [engineering, planning]
---

## Transcript

[00:00-00:45] **Sarah Chen:** Let's start with the roadmap...
```

## Configuration

Settings are stored in a TOML config file at `~/.config/hidock/config.toml` (macOS/Linux) or `%APPDATA%\HiDock\config.toml` (Windows):

```toml
[general]
recordings_folder = "~/HiDock/Recordings"
transcripts_folder = "~/HiDock/Raw Transcripts"

[transcription]
model = "large-v3-turbo"
diarization = false

[summarization]
engine = "auto"        # auto | claude | codex | gemini | ollama | none
auto_summarize = false

[obsidian]
enabled = false
vault_path = ""
sync_strategy = "symlink"  # symlink | copy | direct

[hooks]
post_transcription = ""    # shell command to run after each transcription
```

## MCP Server

The MCP server at `mcp-server/server.py` exposes your meeting knowledge to AI agents. See [mcp-server/README.md](mcp-server/README.md) for setup.

**Available tools**: `search_meetings`, `get_meeting`, `get_recent_meetings`, `get_person_profile`, `list_people`, `list_action_items`, `search_by_person`, `search_by_tag`, `get_stats`, `rebuild_index`.

## Knowledge Graph CLI

Query your meeting knowledge from the command line:

```bash
python -m shared.knowledge search "budget review"
python -m shared.knowledge person "Sarah"
python -m shared.knowledge actions --status open
python -m shared.knowledge stats
python -m shared.knowledge rebuild
```

## Permissions

The app needs **Microphone** and **Notification** access. macOS will prompt on first launch.
