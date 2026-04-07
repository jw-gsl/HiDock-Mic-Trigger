# Porting Guide: macOS → Windows

The macOS app (`hidock-mic-trigger/`) is the primary development target. This document describes how to port changes to the Windows app.

## Porting workflow

1. **Develop and test on macOS** — all new features land in the Swift app first
2. **Identify what changed** — check which functional areas were modified:
   - UI layout/columns → update `ui/main_window.py`
   - USB sync logic → usually no change needed (shared via `Windows-Script/extractor.py`)
   - Transcription → update `core/transcription.py` if pipeline args/output changed
   - Mic trigger → update `core/mic_trigger.py` (API translation needed)
   - New buttons/actions → add corresponding PyQt widgets and slots
3. **Port the change** — translate Swift/AppKit to Python/PyQt6
4. **Test on Windows** — run `python app.py` and verify

## API translation reference

### UI (AppKit → PyQt6)

| macOS (AppKit) | Windows (PyQt6) |
|----------------|-----------------|
| `NSWindow` | `QMainWindow` |
| `NSTableView` + `NSTableViewDataSource` | `QTableView` + `QAbstractTableModel` |
| `NSButton(title:target:action:)` | `QPushButton(text)` + `.clicked.connect(slot)` |
| `NSButton(checkboxWithTitle:...)` | `QCheckBox(text)` + `.stateChanged.connect(slot)` |
| `NSPopUpButton` | `QComboBox` |
| `NSTextField(labelWithString:)` | `QLabel(text)` |
| `NSOpenPanel` | `QFileDialog.getExistingDirectory()` |
| `NSStatusItem` (menu bar) | `QSystemTrayIcon` (system tray) |
| `@objc func action()` | `@pyqtSlot() def action(self):` |
| `NSMenuItem` | `QAction` |
| `DispatchQueue.async` | `QThread` / `QRunnable` / `threading.Thread` |

### Audio (CoreAudio → WASAPI)

| macOS | Windows |
|-------|---------|
| `kAudioDevicePropertyDeviceIsRunningSomewhere` | `IAudioMeterInformation.GetPeakValue()` or session enumeration via `pycaw` |
| `AudioObjectGetPropertyData` | `pycaw.AudioUtilities.GetAllDevices()` |
| `kAudioDevicePropertyStreamConfiguration` (input check) | Check device `dataFlow == eCapture` |
| ffmpeg `-f avfoundation -i :<index>` | ffmpeg `-f dshow -i audio="<device name>"` |

### Paths

| macOS | Windows |
|-------|---------|
| `~/HiDock/` | `%USERPROFILE%\HiDock\` |
| `~/Library/Logs/` | `%APPDATA%\HiDock\logs\` |
| `.venv/bin/python` | `.venv\Scripts\python.exe` |

### Process management

| macOS | Windows |
|-------|---------|
| `Process()` (Foundation) | `subprocess.Popen()` |
| `fcntl.flock()` | `msvcrt.locking()` or file-based lock |
| Signal handling (`SIGINT`, `SIGTERM`) | `signal.signal()` (same API, fewer signals) |

### Transcription (whisper.cpp on both platforms)

Both platforms now use whisper.cpp via `pywhispercpp`. The model file is the same (`ggml-large-v3-turbo-q5_0.bin`).

| macOS | Windows |
|-------|---------|
| `transcription-pipeline/transcribe_cpp.py` (CLI) | `core/transcription.py` (library) |
| Invoked as subprocess by Swift app | Called directly from PyQt6 |
| Model at `~/HiDock/Speech-to-Text/` | Model at `%USERPROFILE%\HiDock\Speech-to-Text\` |
| Model download via `transcribe_cpp.py` auto-download | Model download via `core/model_download.py` + UI button |

## What typically does NOT need porting

- USB protocol changes (shared `extractor.py` in `Windows-Script/`)
- Whisper model/language changes (config-only, same model on both platforms)
- State file format changes (JSON, same schema)
- **Shared module changes** (`shared/`) — these are cross-platform Python, imported by both macOS and Windows backends

## Shared modules (`shared/`)

The `shared/` directory contains cross-platform Python modules used by both platforms. These do NOT need porting — they are imported directly:

| Module | Purpose | Import path |
|--------|---------|-------------|
| `shared/transcript_writer.py` | YAML frontmatter + Markdown output | `from shared.transcript_writer import write_transcript` |
| `shared/llm_cli.py` | LLM CLI detection and invocation | `from shared.llm_cli import get_engine` |
| `shared/summarize.py` | LLM summarization of transcripts | `from shared.summarize import summarize` |
| `shared/knowledge.py` | SQLite knowledge graph | `from shared.knowledge import KnowledgeGraph` |
| `shared/config_store.py` | Cross-platform TOML config | `from shared.config_store import get_config` |
| `shared/obsidian.py` | Obsidian vault sync | `from shared.obsidian import VaultSync` |
| `shared/hooks.py` | Post-transcription hooks | `from shared.hooks import run_hooks_pipeline` |
| `shared/migrate.py` | Add frontmatter to old transcripts | `from shared.migrate import migrate` |

Each platform's transcription entry point (`transcribe.py`, `transcribe_cpp.py`, `Windows-App/core/transcription.py`) adds the repo root to `sys.path` and imports these modules lazily inside functions (not at module level) so that missing optional dependencies don't block startup.

**Windows-specific considerations for shared modules:**
- Obsidian symlink strategy fails without admin/Developer Mode — default to "copy"
- `hooks.py` uses `shell=True` which invokes `cmd.exe` on Windows — hook commands must use Windows syntax
- Config path is `%APPDATA%\HiDock\config.toml` (handled automatically by `config_store.py`)

## What always needs manual porting

- New UI columns or buttons
- New toolbar actions or menu items
- Changes to the mic trigger detection logic
- New notification types
- UX changes (dark theme, layout, shortcuts are platform-specific)
- New settings panels (shared modules handle the backend, but UI must be ported)
