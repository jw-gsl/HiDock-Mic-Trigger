# HiDock Tools — Test Plan

## Current Test Coverage

| Component | Tests | Coverage |
|-----------|-------|----------|
| USB Extractor | 61 | Protocol parsing, filenames, recording status, validation |
| Transcription Pipeline | 28 | State management, CLI parsing, voice library I/O |
| Windows App | 17 | Config, state, USB sync parsing |
| macOS App (Swift) | 1 | Helpers (string formatting) |
| **Shared modules** | **0** | **No tests** |
| **UI (both platforms)** | **0** | **No tests** |

## Gaps and New Tests Needed

### Priority 1: Shared Modules (new, untested)

| Test File | Tests | What it covers |
|-----------|-------|---------------|
| `shared/tests/test_audio_utils.py` | MFCC extraction, audio loading, neural embedding shape, segment extraction | Core signal processing correctness |
| `shared/tests/test_diarize_lite.py` | VAD segment detection, embedding clustering, speaker assignment, diarize() output format | Diarization pipeline end-to-end |
| `shared/tests/test_voice_library_lite.py` | Enroll, identify, rename, delete, cosine similarity, growing library, cross-model compat | Voice library CRUD and matching |
| `shared/tests/test_models.py` | Model registry, status check, download path resolution, delete | Model management without network |

### Priority 2: Integration Tests

| Test | What it covers |
|------|---------------|
| Transcribe + diarize end-to-end | Audio file in → diarized JSON out (requires test audio fixture) |
| Voice enrollment + re-identification | Enroll from one file, identify in another |
| Model download + fallback | Neural embed available vs MFCC fallback |
| Transcript viewer JSON round-trip | Load → rename speaker → save → reload |

### Priority 3: macOS App (Swift)

| Test File | Tests |
|-----------|-------|
| `Tests/UpdateCheckerTests.swift` | Version comparison, API response parsing |
| `Tests/ModelsTests.swift` | Model status detection, path resolution |
| `Tests/HelpersTests.swift` | Already exists — extend with device name, error descriptions |

### Priority 4: Windows App

| Test File | Tests |
|-----------|-------|
| `tests/test_transcription.py` | Transcribe function, diarize parameter, model ready check |
| `tests/test_update_checker.py` | Version comparison, release parsing |
| `tests/test_model_manager.py` | Model registry, status, download path |

## Manual Test Plan

### 1. First Run / Onboarding
- [ ] Fresh install: onboarding wizard appears
- [ ] Skip all steps: app opens, settings preserved
- [ ] Connect HiDock during step 2: auto-detected, auto-advance
- [ ] Select mic in step 3: persisted after onboarding
- [ ] Download model in step 4: progress shown, cancel works
- [ ] Complete onboarding: doesn't show again on next launch
- [ ] Back button works on all steps including final

### 2. USB Sync
- [ ] Pair new device: device appears in status
- [ ] Refresh: recordings listed correctly
- [ ] Download selected: file saved, status updates
- [ ] Download new: only un-downloaded files fetched
- [ ] Auto-download: triggers after recording detected
- [ ] Device not found: shows which app holds it (e.g. "held by Microsoft Edge")
- [ ] Multiple devices: only connected devices shown in menu bar

### 3. Mic Trigger
- [ ] Start trigger: status shows Running, green dot, uptime counting
- [ ] Stop trigger: status shows Stopped, gray dot
- [ ] Auto-start on launch: trigger starts automatically
- [ ] Mic disconnect: falls back to preferred/MacBook/any
- [ ] Mic reconnect: auto-switches to preferred mic

### 4. Transcription
- [ ] Transcribe single file: progress shown, transcript saved
- [ ] Transcribe all: batch progress, all files processed
- [ ] Model not downloaded: shows message, blocks transcription
- [ ] Model download: progress bar with MB/s, cancel works

### 5. Speaker Diarization
- [ ] Enable "Speaker Labels" toggle
- [ ] Transcribe with diarization: segments assigned speaker IDs
- [ ] Open transcript viewer: colored speaker blocks displayed
- [ ] Rename speaker: name persists in JSON, all instances updated
- [ ] Auto-enrollment: voice saved to library on rename
- [ ] Next recording: known speakers auto-identified
- [ ] Voice library grows: sample count increases on re-identification

### 6. Voice Library
- [ ] Open Voice Library: enrolled speakers listed
- [ ] Delete speaker: removed from library
- [ ] Rename speaker: updated across library
- [ ] Empty state: helpful message shown

### 7. Model Manager
- [ ] Open Models: all 3 models listed with correct status
- [ ] Download model: progress bar, completes successfully
- [ ] Delete model: removed, status updates to "Not installed"
- [ ] Whisper model: marked as required

### 8. Feedback
- [ ] Send Feedback: structured form with categories/severities
- [ ] Submit: creates GitHub issue via API
- [ ] My Feedback: history shows with filter/sort/search
- [ ] View on GitHub: opens correct issue URL

### 9. Auto-Update
- [ ] New version available: dialog appears on launch
- [ ] Restart & Update: downloads, installs, relaunches
- [ ] Update on Quit: downloads in background, installs on quit
- [ ] Skip this version: doesn't ask again for same version
- [ ] Check for Updates (manual): shows result either way
- [ ] No update: "You're up to date" message

### 10. Appearance
- [ ] Auto: follows system dark/light
- [ ] Dark: forces dark mode
- [ ] Light: forces light mode
- [ ] macOS: takes effect immediately
- [ ] Windows: takes effect after restart

### 11. Cross-Platform Parity
- [ ] All menu items present on both platforms
- [ ] Footer bar buttons match: Appearance, Models, Voice Library, Check for Updates, My Feedback, Send Feedback
- [ ] Onboarding has same 5 steps
- [ ] Transcript viewer has same features
- [ ] Voice library has same features
- [ ] Model manager has same 3 models
- [ ] Feedback form has same categories/severities

## Automated Test Execution

```bash
# All Python tests
python3 -m pytest transcription-pipeline/tests/ usb-extractor/tests/ Windows-App/tests/ shared/tests/ -v

# Swift tests
cd hidock-mic-trigger && xcodebuild test -scheme hidock-mic-trigger -quiet

# CI runs all on push to main and PRs
```
