# HiDock Tools — Complete Step-by-Step Test Plan (April 2026)

92 commits. Work through each scenario in order. Each builds on the previous.

---

## Part A: App Launch & Mic Trigger

### 1. App Launch Without HiDock
1. Unplug HiDock
2. Launch the app
3. Recording list appears **immediately** (not blank)
4. All previously synced recordings visible with correct sizes
5. Status shows "Not connected"
6. **Pass:** Instant list from cache

### 2. Mic Trigger Start/Stop
1. With USB mic connected, mic trigger should auto-start
2. Verify uptime counter ticking
3. Click Stop → trigger stops
4. Click Start → restarts
5. **Pass:** Start/stop works

### 3. Auto-Start Toggle
1. Toggle Auto-start off, relaunch → trigger does NOT start
2. Toggle back on, relaunch → trigger auto-starts
3. **Pass:** Persists across restarts

### 4. Mic Selection
1. Open mic dropdown, all input devices listed
2. Select different mic → trigger restarts with new mic
3. Disconnect preferred mic → switches to fallback
4. **Pass:** Auto-switching works

---

## Part B: Connect & Browse Recordings

### 5. Connect HiDock
1. Plug in HiDock H1
2. Status shows "Connected — H1"
3. Click Refresh → all recordings appear (200+)
4. **Pass:** Full list, correct count

### 6. Column Sorting
1. Click each column header: Device, Status, Recording, Created, Length, Size
2. Each toggles ascending/descending
3. **Pass:** All columns sortable

### 7. Filter by Device
1. Click device filter buttons → only that device's recordings shown
2. Click "All" → resets
3. **Pass:** Filtering works

### 8. Hide Downloaded / Select Controls
1. Toggle Hide Downloaded → downloaded recordings disappear
2. Toggle off → reappear
3. Select All / Select None / Select New → all work
4. **Pass:** Toggles and selection controls work

### 9. Shift+Click Multi-Select
1. Tick one checkbox
2. Hold Shift, tick another further down
3. Entire range between should be selected
4. Shift+click again to deselect a range
5. **Pass:** Range selection works

---

## Part C: Skip & Download

### 10. Skip Recordings
1. Select several un-downloaded recordings
2. Click **Skip** button in toolbar
3. Status changes to "Skipped" for those items
4. Checkboxes clear after action
5. **Pass:** Items marked as skipped

### 11. Unskip Recordings
1. Select items showing "Skipped" status
2. **Unskip** button should appear in toolbar
3. Click Unskip → status returns to "On device"
4. Checkboxes clear after action
5. Verify Unskip does NOT appear for actually downloaded files
6. **Pass:** Only skipped items can be unskipped

### 12. Download a Recording
1. Select an un-downloaded recording, click Download Selected
2. Progress shown in status bar
3. Status changes to "Downloaded"
4. File appears in `~/HiDock/Recordings/`
5. **Pass:** Download works

### 13. Stop Download Mid-Progress
1. Start a large download, click Stop Download
2. Download cancels cleanly
3. **Pass:** Stop works

### 14. Auto-Download Toggle
1. Enable Auto-download checkbox
2. After a mic trigger session ends, new recording should download automatically
3. **Pass:** Auto-download triggers

### 15. Auto-Transcribe Toggle
1. Enable Auto-transcribe checkbox
2. After auto-download completes, transcription should start automatically
3. Disable Auto-transcribe → download completes but does NOT transcribe
4. **Pass:** Independent from auto-download

### 16. Choose Folders
1. Click Recordings folder button → select new folder
2. Click Transcripts folder button → select new folder
3. Verify new files go to new locations
4. Settings persist across restart
5. **Pass:** Folder selection works

---

## Part D: Transcription

### 17. Transcribe with Stage Progress
1. Select a downloaded recording, click Transcribe Selected
2. Status shows stages: "Loading model (1/5)" → "Transcribing (2/5)" → "Applying corrections (3/5)" → "Diarizing speakers (4/5)" → "Writing output (5/5)"
3. Progress bar fills proportionally per stage
4. Transcription completes, icon appears in table
5. **Pass:** Stage-based progress, not stuck at 15%

### 18. Download Then Transcribe
1. Select an un-downloaded recording, click Transcribe Selected
2. Downloads first ("Downloading before transcription...")
3. Automatically starts transcribing after download
4. **Pass:** Seamless chain

### 19. Transcribe All
1. Click Transcribe All
2. All un-transcribed downloaded recordings queued
3. **Pass:** Batch queue

### 20. Re-Transcribe Already Done
1. Select an already-transcribed recording
2. Click Transcribe Selected → alert "Already Transcribed — Re-transcribe?"
3. Re-transcribe → starts; Cancel → does nothing
4. **Pass:** Alert shown, both paths work

### 21. Transcription Queue
1. Select 3+ recordings, click Transcribe Selected
2. Queue indicator appears in toolbar
3. Click it → queue window opens
4. One item transcribing, others "Queued"
5. **Pass:** Queue works

### 22. Queue Pause/Resume
1. Click Pause → current finishes, next doesn't start, shows "PAUSED"
2. Click Resume → next starts
3. **Pass:** Pause/resume works

### 23. Queue Cancel
1. Click Cancel All → remaining items show "Cancelled"
2. Progress timer stops
3. **Pass:** Clean cancel

### 24. Queue Remove/Reorder
1. Click X on a queued item → removed
2. Drag to reorder queued items
3. **Pass:** Remove and reorder work

---

## Part E: Transcript Viewer

### 25. Open Transcript Viewer
1. Click a transcribed recording's transcript icon
2. In-app viewer opens (not Finder)
3. Timestamps on each segment: `[00:00]`, `[01:23]`
4. Coloured speaker pills in legend and per segment
5. **Pass:** Viewer opens with timestamps and speakers

### 26. Stats Header
1. Below the top bar, verify stats line shows:
   - Total duration
   - Per speaker: name, talk time %, words per minute
   - Stacked colour bar showing talk split
2. Rename a speaker → stats update with new name
3. Merge speakers → stats update with new percentages
4. **Pass:** Dynamic stats

### 27. Non-Diarized Transcript
1. Transcribe with Speaker Labels unchecked
2. Open transcript → timestamps shown, no speaker pills or stats
3. **Pass:** Clean timestamp-only view

### 28. Audio Playback
1. Click play (▶) on a segment → audio plays for that time range
2. Click again to stop
3. Click play on different segment → previous stops
4. **Pass:** Per-segment playback

### 29. Rename Speakers
1. Click speaker pill in legend → type name → Enter
2. All instances update throughout transcript
3. Changes auto-save (no manual save needed)
4. **Pass:** All instances renamed, persisted

### 30. Merge Speakers (Right-Click)
1. Right-click a speaker pill → "Merge into Speaker X"
2. All segments from that speaker reassigned
3. Consecutive same-speaker segments merge
4. Stats update
5. **Pass:** Speakers consolidated

### 31. Undo Merge
1. After merging, click Undo (or Cmd+Z)
2. Merge reversed, stats revert
3. Multiple undos work
4. **Pass:** Undo restores previous state

### 32. Re-Diarize
1. Set speaker count stepper (e.g. 2)
2. Click Re-diarize → completes in ~30 seconds
3. Viewer reopens with updated speaker assignments
4. Uses original Whisper micro-segments if available
5. **Pass:** Fast re-diarize, improved assignments

### 33. Copy All
1. Click "Copy All" (or Cmd+Shift+C)
2. Paste into a text editor → full transcript with timestamps and speaker names
3. **Pass:** Clipboard has formatted transcript

### 34. Show File
1. Click "Show File" button
2. Finder opens showing the transcript `.md` file
3. **Pass:** File revealed

---

## Part F: Audio Editing

### 35. Trim (Save as Copy)
1. Select 1 recording, click Trim
2. Enter start/end times, leave "Save as copy" checked
3. `{name}-trimmed.mp3` created
4. **Pass:** Copy created

### 36. Trim (Replace Original)
1. Select 1 recording, click Trim
2. Uncheck "Save as copy"
3. Original replaced with trimmed version
4. **Pass:** Original replaced

### 37. Merge Recordings
1. Select 2+ downloaded recordings, click Merge
2. Status shows "Merging..." then reveals merged file in Finder
3. `Merged-{first}-to-{last}.mp3` appears
4. Play merged file → audio concatenated in order
5. Checkboxes clear after merge
6. **Pass:** Merge works

### 38. Merge Creates Expandable Row
1. After merging, the merged file appears as a row in the table
2. Click the expand arrow (after device name)
3. Original child recordings appear indented below
4. Merge row shows: device, "Merged" badge, earliest date, total duration, total size
5. Collapse → children hide
6. **Pass:** Tree view works

### 39. Merge Row Transcription
1. Tick the merge parent row
2. Click Transcribe Selected → merged file transcribes
3. Transcription icon appears on merge row
4. Click it → transcript viewer opens
5. **Pass:** Merge row transcription works

### 40. Re-Merge (No Duplicates)
1. Select the same recordings and merge again
2. Old merged file is replaced (no `-1`, `-2` suffixes)
3. **Pass:** Clean replacement

### 41. Merge Error Handling
1. Select recordings that aren't downloaded locally
2. Click Merge → warning "Merge requires 2+ downloaded recordings"
3. **Pass:** Error message shown

### 42. Context Menu
1. Right-click a recording:
   - Download, Skip, Unskip, Transcribe, Show in Finder, Open Transcript, Trim, Merge Selected
2. Verify each action works
3. **Pass:** All context menu items functional

---

## Part G: Diarization Quality

### 43. Audio Normalization
1. Transcribe a quiet recording (low volume)
2. Check log for "RMS x.xxxx → 0.0600 (normalized)"
3. Speakers should be detected despite quiet audio
4. **Pass:** Normalization enables VAD on quiet recordings

### 44. Segment Size Cap
1. Open a transcript → no segment should be longer than ~90 seconds
2. Long monologues should be split at sentence/comma boundaries
3. **Pass:** No monster blocks

### 45. Speaker Detection
1. Transcribe a 2-person meeting
2. Verify 2 speakers detected (not 5)
3. Speaker balance should be reasonable (not 99%/1%)
4. **Pass:** Correct speaker count and balance

### 46. Whisper Micro-Segments Saved
1. After transcription, check `~/HiDock/Raw Transcripts/{name}_whisper.json` exists
2. Contains hundreds of small segments (not the merged ones)
3. Re-diarize uses these for better quality
4. **Pass:** Micro-segments preserved

---

## Part H: Corrections Dictionary

### 47. Apply Corrections
1. Create `~/HiDock/corrections.json`:
   ```json
   {"corrections": {"volaris": "VOLARIS", "hidoc": "HiDock"}}
   ```
2. Transcribe a recording containing those words
3. Verify corrections applied in output
4. **Pass:** Words replaced

---

## Part I: Transcript Quality

### 48. Markdown Format
1. Open a `.md` transcript in a text editor
2. YAML frontmatter with title, date, speakers
3. Model: `OpenAI Whisper large-v3-turbo (809M params, multilingual)`
4. Timestamps per segment with speaker labels
5. No hallucinated repeated text at end
6. **Pass:** Clean markdown

### 49. Status Badge Indicators
1. Check the transcription column:
   - Not transcribed: em dash (—)
   - Transcribing: progress spinner with stage
   - Transcribed + needs tagging: orange tag icon
   - Transcribed + tagged: green checkmark
2. **Pass:** All badge states correct

---

## Part J: Model & Voice Library

### 50. Download Whisper Model
1. If model not downloaded, click Download Model
2. Progress shown (~550 MB)
3. **Pass:** Model downloads

### 51. Voice Library
1. Open Voice Library from menu
2. Enrolled speakers listed with sample count
3. Rename/delete works
4. **Pass:** Library functional

---

## Part K: Device Manager

### 52. View/Forget/Re-Pair
1. Open Device Manager → HiDock listed
2. Click Forget → device removed, list empties
3. Click Pair → follow flow → device and recordings return
4. **Pass:** Full cycle works

### 53. Volume Device
1. Plug in USB recorder or SD card
2. Scan Volumes → detected
3. Pair → recordings appear
4. Download a recording
5. **Pass:** Volume device workflow

---

## Part L: Preferences

### 54. Appearance Mode
1. Toggle Dark/Light/Auto → UI updates
2. Persists across restart
3. **Pass:** Theme works

### 55. Notification Preferences
1. Toggle transcription/download/mic notifications
2. Trigger events → notifications respect settings
3. **Pass:** Toggles work

---

## Part M: Disconnect Resilience

### 56. Disconnect Mid-Session
1. Unplug HiDock → status shows "Not connected"
2. Recording list stays visible
3. Downloaded recordings still openable/transcribable
4. Plug back in → "Connected"
5. **Pass:** Graceful disconnect/reconnect

---

## Part N: Feedback & Updates

### 57. Submit Feedback
1. Send Feedback from menu → form submits
2. **Pass:** Creates GitHub issue

### 58. Feedback History
1. Open Feedback History → search/filter/sort work
2. **Pass:** History functional

### 59. Check for Updates
1. Check for Updates from menu
2. **Pass:** Update check runs

---

## Part O: Other

### 60. Cowork Prompt
1. Open Cowork Prompt → dialog with text → copy works
2. **Pass:** Dialog functional

### 61. Onboarding Wizard
1. Reset: `defaults delete com.hidock.tools.hidock-mic-trigger hasCompletedOnboarding`
2. Relaunch → wizard appears → complete all steps
3. **Pass:** Wizard works

---

## Part P: Intelligence Layer (Optional)

### 62. Whisper-Guard
1. Transcribe → check log for Whisper-Guard messages
2. No hallucinations in output
3. **Pass:** Guard runs

### 63. Knowledge Graph
1. Check `~/HiDock/knowledge.db` exists
2. **Pass:** Database created

### 64. LLM Summarization (if CLI available)
1. Transcribe with `--summarize` → "## Summary" section in transcript
2. **Pass:** Summary generated

### 65. Obsidian Sync (if configured)
1. Notes sync with `[[wikilinks]]`
2. **Pass:** Notes appear

### 66. MCP Server (if configured)
1. Query via MCP tools
2. **Pass:** Responds

---

## Part Q: Windows Parity

### 67. Windows Transcript Viewer
1. Audio playback per segment
2. Speaker merge (right-click) + undo (Ctrl+Z)
3. Re-diarize with speaker count
4. **Pass:** All match macOS

### 68. Windows Queue Dialog
1. Queue indicator, progress, pause/resume, cancel
2. **Pass:** Queue works

### 69. Windows Trim/Merge
1. Trim and Merge buttons functional
2. **Pass:** Audio editing works

---

## Part R: CI & Automated Tests

### 70. CI Builds
1. `git push origin main`
2. macOS + Windows builds trigger and pass
3. **Pass:** CI green

### 71. Automated Tests
1. Run: `cd usb-extractor && .venv/bin/python3 -m pytest tests/ -v`
2. Run: `cd transcription-pipeline && PYTHONPATH=.. .venv/bin/python3 -m pytest tests/ -v`
3. Run: `PYTHONPATH=. python3 -m pytest shared/tests/ -v`
4. All 485+ tests pass
5. **Pass:** Full test suite green

---

## Quick Checklist

| # | Test | Status |
|---|------|--------|
| **Launch & Mic** | | |
| 1 | App launches with recordings (no HiDock) | |
| 2 | Mic trigger start/stop | |
| 3 | Auto-start toggle | |
| 4 | Mic selection + fallback | |
| **Browse** | | |
| 5 | HiDock connects, full list (200+) | |
| 6 | Column sorting | |
| 7 | Filter by device | |
| 8 | Hide downloaded / select controls | |
| 9 | Shift+click multi-select | |
| **Skip & Download** | | |
| 10 | Skip recordings | |
| 11 | Unskip recordings | |
| 12 | Download | |
| 13 | Stop download | |
| 14 | Auto-download | |
| 15 | Auto-transcribe toggle | |
| 16 | Choose folders | |
| **Transcription** | | |
| 17 | Stage-based progress | |
| 18 | Download → transcribe chain | |
| 19 | Transcribe All | |
| 20 | Re-transcribe alert | |
| 21 | Queue multiple | |
| 22 | Queue pause/resume | |
| 23 | Queue cancel | |
| 24 | Queue remove/reorder | |
| **Transcript Viewer** | | |
| 25 | Viewer opens in-app | |
| 26 | Stats header (talk %, wpm, bar) | |
| 27 | Non-diarized (timestamps only) | |
| 28 | Audio playback | |
| 29 | Rename speakers | |
| 30 | Merge speakers (right-click) | |
| 31 | Undo merge | |
| 32 | Re-diarize | |
| 33 | Copy All | |
| 34 | Show File | |
| **Audio Editing** | | |
| 35 | Trim (copy) | |
| 36 | Trim (replace) | |
| 37 | Merge recordings | |
| 38 | Merge expandable row | |
| 39 | Merge row transcription | |
| 40 | Re-merge (no duplicates) | |
| 41 | Merge error handling | |
| 42 | Context menu (all items) | |
| **Diarization** | | |
| 43 | Audio normalization | |
| 44 | Segment size cap (90s) | |
| 45 | Speaker detection quality | |
| 46 | Whisper micro-segments saved | |
| **Corrections** | | |
| 47 | Corrections dictionary | |
| **Transcript Quality** | | |
| 48 | Markdown format | |
| 49 | Status badge indicators | |
| **Model & Voice** | | |
| 50 | Download Whisper model | |
| 51 | Voice library | |
| **Device Manager** | | |
| 52 | View/forget/re-pair | |
| 53 | Volume device | |
| **Preferences** | | |
| 54 | Appearance mode | |
| 55 | Notification preferences | |
| **Resilience** | | |
| 56 | Disconnect/reconnect | |
| **Feedback** | | |
| 57 | Submit feedback | |
| 58 | Feedback history | |
| 59 | Check for updates | |
| **Other** | | |
| 60 | Cowork prompt | |
| 61 | Onboarding wizard | |
| **Intelligence** | | |
| 62 | Whisper-Guard | |
| 63 | Knowledge graph | |
| 64 | LLM summarization | |
| 65 | Obsidian sync | |
| 66 | MCP server | |
| **Windows** | | |
| 67 | Transcript viewer parity | |
| 68 | Queue dialog | |
| 69 | Trim/merge | |
| **CI** | | |
| 70 | CI builds pass | |
| 71 | 485+ automated tests pass | |
