# Transcription Pipeline

Local transcription pipeline using OpenAI Whisper on Apple Silicon MPS. Transcribes HiDock recordings to Markdown files with optional speaker diarization.

## Setup

```bash
./setup-venv.sh
```

This creates a Python 3.13 venv, installs PyTorch + Whisper, and verifies MPS availability.

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

### Voice library (speaker identification)

Enroll speakers and auto-identify them in diarized transcripts:

```bash
.venv/bin/python voice_library.py enroll "James" segment.wav
.venv/bin/python voice_library.py identify segment.wav
.venv/bin/python voice_library.py list
```

## Architecture

```
transcribe.py          CLI entry point (transcribe, transcribe-batch, status)
config.py              Paths and model configuration
state.py               Atomic state management (state.json)
diarize.py             Speaker diarization via pyannote.audio
voice_library.py       Speaker embeddings via SpeechBrain ECAPA-TDNN
setup-venv.sh          Venv creation and dependency installation
requirements.txt       Python dependencies
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

### Without diarization

Plain text transcript saved as `<recording-name>.md`.

### With diarization

```
[00:00-00:45] James: Welcome everyone to the meeting today...
[00:45-01:12] Speaker_02: Thanks, I wanted to discuss the...
```

## Integration

The menu bar app (`hidock-mic-trigger`) calls this pipeline via subprocess:
- `transcribe.py transcribe <path>` for individual files
- `transcribe.py transcribe-batch` for batch processing
- `transcribe.py status` to refresh the UI table

Progress is reported via `PROGRESS:<pct>` lines on stderr.

## Requirements

- macOS with Apple Silicon (MPS acceleration)
- Python 3.11+
- ~5GB disk for the Whisper `large-v3-turbo` model (downloaded on first run)
