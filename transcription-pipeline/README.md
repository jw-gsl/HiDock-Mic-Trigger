# Transcription Pipeline

Local transcription pipeline for HiDock recordings. Two backends available:
- **`transcribe.py`** — OpenAI Whisper on Apple Silicon MPS (development, requires PyTorch)
- **`transcribe_cpp.py`** — whisper.cpp via pywhispercpp (bundled builds, lightweight, no PyTorch)

Both produce identical output (Markdown transcripts with YAML frontmatter) and use the same CLI protocol. Both support optional `--summarize` for LLM-powered intelligence extraction and `--diarize` for speaker identification.

## Setup

### Development (PyTorch + MPS)

```bash
./setup-venv.sh
```

This creates a Python 3.13 venv, installs PyTorch + Whisper, and verifies MPS availability.

### Bundled builds (whisper.cpp)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-bundle.txt
```

The GGML model (~550 MB) is downloaded automatically on first transcription.

## Usage

### Transcribe a single file

```bash
.venv/bin/python transcribe.py transcribe ~/HiDock/Recordings/2026Mar10-102848-Rec41.mp3
```

### Transcribe all un-transcribed recordings

```bash
.venv/bin/python transcribe.py transcribe-batch
```

### Check transcription status

```bash
.venv/bin/python transcribe.py status
```

### Enable speaker diarization

```bash
.venv/bin/python transcribe.py transcribe --diarize ~/HiDock/Recordings/file.mp3
```

Diarization requires a HuggingFace token with access to `pyannote/speaker-diarization-3.1`. Store the token in `config.json`:

```json
{
  "huggingface_token": "hf_...",
  "diarize_enabled": true,
  "voice_library_enabled": false
}
```

### Enable LLM summarization

```bash
.venv/bin/python transcribe.py transcribe --summarize ~/HiDock/Recordings/file.mp3
.venv/bin/python transcribe.py transcribe-batch --summarize
```

Summarization requires an LLM CLI tool installed on the system. Detection priority: `claude` > `codex` > `gemini` > `ollama`. No API keys needed — uses your existing AI subscriptions. If no CLI is found, summarization is skipped and transcription completes normally.

The LLM extracts: title, action items (with assignee/due/status), decisions, key points, tags, and a summary paragraph. All structured data is stored in the transcript's YAML frontmatter.

### Combine diarization + summarization

```bash
.venv/bin/python transcribe.py transcribe --diarize --summarize ~/HiDock/Recordings/file.mp3
```

### Voice library (speaker identification)

Enroll speakers and auto-identify them in diarized transcripts:

```bash
.venv/bin/python voice_library.py enroll "James" segment.wav
.venv/bin/python voice_library.py identify segment.wav
.venv/bin/python voice_library.py list
```

## Architecture

```
transcribe.py          CLI entry point — PyTorch/MPS backend (dev)
transcribe_cpp.py      CLI entry point — whisper.cpp backend (bundled builds)
config.py              Paths and model configuration
state.py               Atomic state management (state.json)
diarize.py             Speaker diarization via pyannote.audio
voice_library.py       Speaker embeddings via SpeechBrain ECAPA-TDNN
setup-venv.sh          Venv creation and dependency installation
requirements.txt       Python dependencies (PyTorch + Whisper)
requirements-bundle.txt  Lightweight deps (whisper.cpp only, no PyTorch)
config.json            Runtime config (gitignored) — HuggingFace token, feature flags
```

## Configuration

Paths are defined in `config.py`:

| Path | Default | Description |
|---|---|---|
| Recordings | `~/HiDock/Recordings/` | Input MP3 files |
| Raw Transcripts | `~/HiDock/Raw Transcripts/` | Output .md transcripts |
| Models | `~/HiDock/Speech-to-Text/` | Whisper model cache |
| Voice Library | `~/HiDock/Voice Library/` | Speaker embeddings |
| State | `~/HiDock/transcription-pipeline/state.json` | Transcription state |

## Output format

All transcripts are saved as `<recording-name>.md` with YAML frontmatter containing structured metadata (title, date, duration, speakers, model, source file). When `--summarize` is used, the frontmatter also includes action items, decisions, key points, and tags.

### Without diarization

```markdown
---
title: "Discussion about Q2 roadmap"
type: meeting
date: 2026-04-05T14:00:00+00:00
duration: 234.5
speakers: []
model: large-v3-turbo
action_items: []
---

## Transcript

Welcome everyone to the meeting today...
```

### With diarization

```markdown
---
title: "Weekly sync with Sarah"
speakers: [James, Sarah Chen]
action_items:
  - task: "Review roadmap draft"
    assignee: Sarah Chen
    status: open
---

## Transcript

[00:00-00:45] **James:** Welcome everyone to the meeting today...

[00:45-01:12] **Sarah Chen:** Thanks, I wanted to discuss the...
```

### Post-transcription pipeline

After transcription (and optional summarization), the pipeline automatically:
1. Runs any configured **post-transcription hook** (shell command from `~/.config/hidock/config.toml`)
2. Syncs to **Obsidian vault** if configured (with `[[wikilinks]]` for speakers)
3. Both are non-fatal — failures are logged but don't block the workflow

## Integration

The desktop app (`hidock-mic-trigger`) calls this pipeline via subprocess:
- `transcribe.py transcribe <path>` (dev) or `transcribe_cpp.py transcribe <path>` (bundled)
- `transcribe.py transcribe-batch` for batch processing
- `transcribe.py status` to refresh the UI table

The bundled macOS .app (built by CI) uses `transcribe_cpp.py` with whisper.cpp. The development setup uses `transcribe.py` with PyTorch/MPS.

Progress is reported via `PROGRESS:<pct>` lines on stderr.

## Requirements

### Development
- macOS with Apple Silicon (MPS acceleration)
- Python 3.11+
- ~5 GB disk for the Whisper `large-v3-turbo` PyTorch model

### Bundled (whisper.cpp)
- macOS 13+ (Apple Silicon)
- Python 3.11+
- ~550 MB for the GGML quantized model (downloaded on first run)
