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
| `transcription-pipeline/` | macOS | Transcription pipeline — Whisper (bundled whisper.cpp or Python MPS), with Parakeet TDT v2 (MLX) and Cohere Transcribe + forced alignment as alternative backends |
| `Windows-App/` | Windows | Desktop app (PyQt6) — Windows port of the macOS app |
| `Windows-Script/` | Windows | Python extractor and background watcher — HiDock USB and volume device support |
| `shared/` | Cross-platform | Python modules for structured transcripts, LLM summarization, knowledge graph, Obsidian sync, config, and hooks |
| `mcp-server/` | Cross-platform | MCP server exposing meeting knowledge to AI agents (Claude Desktop, Cursor, etc.) |
| `docs/` | — | Gap analysis, evolution plan, and architecture documentation |

> **macOS is the primary development platform.** The Windows app is a secondary port. See [Windows-App/PORTING.md](Windows-App/PORTING.md) for the porting workflow.

## How it works

At a high level:

1. **Mic Trigger** — watches your USB mic (e.g. Samson Q2U) via CoreAudio. When it detects the mic is in use, it silently opens the HiDock's audio input using `ffmpeg`, causing the HiDock to auto-record.
2. **Device Sync** — pairs with HiDock devices over USB, generic USB volumes (audio recorders, SD cards), and Plaud cloud accounts, then downloads/imports recordings to a local folder. Device providers must obey the shared contract in [`docs/ARCHITECTURE-device-providers.md`](docs/ARCHITECTURE-device-providers.md).
3. **Transcription pipeline** — a multi-stage processing chain detailed below. All local, no network, no API keys.
4. **Integrations** — optional Knowledge Graph indexing, Obsidian vault sync, custom shell hooks, and an MCP server that exposes meeting knowledge to AI agents.

## The transcription pipeline

Every recording runs through nine stages. Some stages use downloadable models (shown with size); others are pure-logic steps (rule-based filters, clustering, file writing). Only a handful of stages have swappable alternatives — most are fixed infrastructure.

```
┌─ 1. AUDIO LOAD ────────────────────────────────────────────┐
│   shared/audio_utils.py — ffmpeg-based mp3 → 16kHz mono    │
└────────────────────────────────────────────────────────────┘
        │
┌─ 2. AUDIO PREP (rule-based, no model) ─────────────────────┐
│   Silence stripping (_replace_silence_with_padding) —      │
│   cuts long dead-air sections before Whisper sees them,    │
│   saving compute and preventing silence-hallucination      │
│   loops. RMS + peak-normalisation fallback for quiet audio.│
└────────────────────────────────────────────────────────────┘
        │
┌─ 3. VOICE ACTIVITY DETECTION ──────────────────────────────┐
│  ● Silero VAD                          2 MB   (default)    │
│  ○ TEN VAD                             306 KB (planned)    │
│   Identifies speech vs non-speech frames. Re-used in       │
│   stage 6 for diarization speech boundaries.               │
└────────────────────────────────────────────────────────────┘
        │
┌─ 4. SPEECH-TO-TEXT ────────────────────────────────────────┐
│  ● Whisper large-v3-turbo              547 MB              │
│    whisper.cpp (bundled) or Python Whisper on MPS (dev)    │
│    99 languages, auto-detect, per-segment timestamps       │
│  ○ Parakeet TDT v2 (MLX)               1.2 GB (prototype)  │
│    Apple Silicon native, ~60× real-time, English only      │
│  ○ Cohere Transcribe 03-2026           4.0 GB (prototype)  │
│    14 languages, #1 HF leaderboard (5.42% WER)             │
│    No timestamps — requires stage 4.5                      │
└────────────────────────────────────────────────────────────┘
        │
┌─ 4.5. FORCED ALIGNMENT (only for Cohere) ──────────────────┐
│  ☐ wav2vec2-CTC per language           1.2 GB each         │
│    torchaudio.functional.forced_align reconstructs word    │
│    timestamps from Cohere's plain-text output.             │
└────────────────────────────────────────────────────────────┘
        │
┌─ 5. TEXT CLEANUP (rule-based, no model) ───────────────────┐
│   Whisper-Guard — 7-layer text hallucination filter:       │
│     • Consecutive dedup  (A→A→A)                           │
│     • Interleaved dedup  (A→B→A→B)                         │
│     • Foreign-script stripping                             │
│     • Noise-phrase removal ("amara.org", "thanks for…")    │
│     • Trailing-noise trimming                              │
│     • Minimum-word-count sanity check                      │
│     • Repetition-density hallucination flag                │
│   Corrections dictionary (user-configured vocab swaps,     │
│   e.g. "volaris" → "Volaris")                              │
└────────────────────────────────────────────────────────────┘
        │
┌─ 6. SPEAKER DIARIZATION (optional but usually on) ─────────┐
│   Re-uses Silero VAD from stage 3 for speech boundaries    │
│   Speech-segment merging & filtering                       │
│  ● TitaNet Small (speaker embeddings)       10 MB          │
│  ○ CAM++         (speaker embeddings)       28 MB          │
│   Speaker-count estimation (VAD density + embedding        │
│   spread + silhouette scoring with bell-curve penalty)     │
│   Hierarchical clustering (scipy, no model)                │
│   Post-cluster centroid merge                              │
│   Voice Library matching — identifies known speakers       │
│   across meetings using cached embeddings                  │
└────────────────────────────────────────────────────────────┘
        │
┌─ 7. OUTPUT WRITING (no models) ────────────────────────────┐
│   Markdown transcript with YAML frontmatter                │
│   _whisper.json — raw Whisper/ASR segments                 │
│   _diarized.json — speaker-labelled segments               │
│   state.json — pipeline state for resume / re-queue        │
└────────────────────────────────────────────────────────────┘
        │
┌─ 8. LLM SUMMARISATION (optional, uses existing CLI) ───────┐
│   Auto-detects local CLI in PATH:                          │
│     claude → codex → gemini → ollama                       │
│   Map-reduce chunking for long transcripts                 │
│   Produces: title, action items, decisions, key points,    │
│   tags, attendees — added to the frontmatter               │
│   No API keys — uses your existing AI subscription.        │
└────────────────────────────────────────────────────────────┘
        │
┌─ 9. INTEGRATIONS (optional, user-configured) ──────────────┐
│   Knowledge graph indexing → SQLite + FTS5 for search      │
│   Obsidian vault sync     → [[wikilinks]], person notes    │
│   Custom shell hook        → Slack/email/anything          │
│   MCP server exposure      → AI agents query your meetings │
└────────────────────────────────────────────────────────────┘
```

**Stages with swappable models** (selectable in the Models Manager): 3, 4, 6, and 4.5 (auto-added when Cohere is selected in stage 4).

**Stages that are pure logic** (no model, always the same code): 1, 2, 5, 7, 8 (CLI detection, not a model), 9.

All processing happens locally on a single desktop app with menu bar integration (macOS) or system tray (Windows). LLM summarisation is optional and uses your existing CLI subscriptions. The pipeline is detailed further in [`docs/PLAN-asr-model-evaluation.md`](docs/PLAN-asr-model-evaluation.md).

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
  Recordings/                       # Downloaded MP3 files
  Raw Transcripts/                  # Transcription output for each recording:
    <basename>.md                   #   Markdown with YAML frontmatter
    <basename>_whisper.json         #   Raw ASR segments (Whisper/Parakeet/Cohere)
    <basename>_diarized.json        #   Speaker-labelled segments
  Summaries/                        # LLM-generated structured summaries (if enabled)
  Transcriptions/                   # Enriched human-readable meeting notes
  Speech-to-Text/                   # Downloaded model weights (Whisper, TitaNet, VAD)
  Voice Library/                    # Cross-meeting speaker embeddings
  transcription-pipeline/
    state.json                      # Pipeline state for resume/re-queue
    .transcribe.lock                # Advisory lock preventing concurrent runs
  knowledge.db                      # SQLite knowledge graph (rebuildable)
```

Parakeet weights, when enabled, live under `~/.cache/huggingface/hub/` rather than `Speech-to-Text/` — the parakeet-mlx library manages its own cache.

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
