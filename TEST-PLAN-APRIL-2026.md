# HiDock Tools — Complete Step-by-Step Test Plan (April 2026)

63 commits. 57 user-facing features. Work through each scenario in order.

---

## Part A: App Launch & Mic Trigger

### Scenario 1: App Launch Without HiDock
1. Make sure your HiDock is **unplugged**
2. Open the app (or relaunch if running)
3. The recording list should appear **immediately** — not blank
4. All previously synced recordings should be visible
5. Status bar should show "Not connected"
6. **Pass:** Instant list, disconnected status

### Scenario 2: Mic Trigger — Start and Stop
1. With a USB mic connected (e.g. Samson Q2U)
2. The mic trigger should auto-start on launch (check the status in the Mic Trigger section)
3. Verify uptime counter is ticking
4. Click **Stop** — trigger should stop, uptime resets
5. Click **Start** — trigger should restart
6. **Pass:** Start/stop works, uptime displays

### Scenario 3: Mic Trigger — Auto-Start Toggle
1. Toggle **Auto-start on launch** off
2. Quit and relaunch the app
3. Mic trigger should NOT start automatically
4. Toggle it back on, relaunch — trigger should auto-start
5. **Pass:** Setting persists across restarts

### Scenario 4: Mic Selection
1. In the Mic Trigger section, open the mic dropdown
2. All connected audio input devices should be listed
3. Select a different mic — trigger should restart with the new mic
4. Right-click a mic to set as **preferred** or **fallback**
5. Disconnect the preferred mic — app should switch to fallback
6. Reconnect preferred mic — app should switch back
7. **Pass:** Mic switching, preferred/fallback auto-switch

---

## Part B: HiDock Connection & Recordings

### Scenario 5: Connect HiDock
1. Plug in your HiDock H1 via USB
2. App should auto-detect: "Connected — H1" in status bar
3. Click **Refresh** — recording count should update
4. Scroll through — all recordings visible (200+)
5. **Pass:** Full list, correct count

### Scenario 6: Recording Sorting
1. Click column headers in the table: Name, Date, Duration, Size, Status, Device
2. Each click should toggle ascending/descending sort
3. **Pass:** All 7 columns sortable

### Scenario 7: Filter by Device
1. If multiple devices paired, click device filter buttons in toolbar
2. Click "All" to show all, click a specific device to filter
3. **Pass:** Filtering works, "All" resets

### Scenario 8: Hide Downloaded / Select Controls
1. Toggle **Hide Downloaded** — downloaded recordings should disappear from list
2. Toggle off — they reappear
3. Click **Select All** — all checkboxes tick
4. Click **Select None** — all untick
5. Click **Select New** — only un-downloaded recordings tick
6. **Pass:** All toggles and selection controls work

---

## Part C: Download

### Scenario 9: Download a Recording
1. Tick an un-downloaded recording
2. Click **Download Selected**
3. Watch status bar for download progress
4. When complete, status changes to "Downloaded"
5. **Pass:** MP3 appears in `~/HiDock/Recordings/`

### Scenario 10: Stop Download Mid-Progress
1. Start downloading a large recording
2. Click **Stop Download** while it's in progress
3. Download should cancel, status should indicate stopped
4. **Pass:** Download stops cleanly

### Scenario 11: Auto-Download Toggle
1. Enable **Auto-download** checkbox
2. After a new recording appears on the device (e.g. after a mic trigger session)
3. It should download automatically without clicking anything
4. **Pass:** New recordings auto-download

### Scenario 12: Mark as Downloaded
1. Right-click a recording that exists locally but isn't marked as downloaded
2. Select **Mark as Downloaded**
3. Status should update to "Downloaded"
4. **Pass:** Manual mark works

### Scenario 13: Choose Recordings Folder
1. Click the **Recordings** folder button in toolbar
2. Select a different folder
3. Verify new downloads go to the new folder
4. Relaunch app — folder choice should persist
5. **Pass:** Folder selection works and persists

### Scenario 14: Choose Transcripts Folder
1. Click the **Transcripts** folder button
2. Select a different folder
3. Verify new transcripts go there
4. **Pass:** Folder selection works

---

## Part D: Transcription

### Scenario 15: Transcribe with Smooth Progress
1. Tick a downloaded recording
2. Verify **Speaker Labels** is ticked (default ON)
3. Click **Transcribe Selected**
4. Progress should tick up smoothly every ~3 seconds (not stall at 15%)
5. Wait for completion (~5-7 min for 40 min recording)
6. Transcribed indicator should appear in the table
7. **Pass:** Smooth progress, completes, icon appears

### Scenario 16: Download Then Transcribe in One Step
1. Tick an **un-downloaded** recording
2. Click **Transcribe Selected**
3. Should download first, then auto-transcribe
4. **Pass:** Seamless chain

### Scenario 17: Transcribe All
1. Click **Transcribe All**
2. All un-transcribed downloaded recordings should be queued
3. **Pass:** Batch queued

### Scenario 18: Re-Transcribe Already Done
1. Tick an already-transcribed recording
2. Click **Transcribe Selected**
3. Alert: "Already Transcribed — Re-transcribe?"
4. Click Re-transcribe — starts again
5. Click Cancel on another attempt — does nothing
6. **Pass:** Alert shown, both paths work

### Scenario 19: Transcription Queue
1. Tick 3+ downloaded un-transcribed recordings
2. Click **Transcribe Selected**
3. Queue indicator appears in toolbar with count
4. Click it — queue window opens
5. One item transcribing with progress, others "Queued"
6. **Pass:** Queue works

### Scenario 20: Queue — Pause / Resume
1. While queue is processing, click **Pause**
2. Current finishes, next doesn't start — shows "PAUSED"
3. Click **Resume** — next item starts
4. **Pass:** Pause/resume works

### Scenario 21: Queue — Cancel All
1. Click **Cancel All** in queue window
2. Remaining items show "Cancelled"
3. **Pass:** Queue cancelled

### Scenario 22: Queue — Remove and Reorder
1. Queue several items
2. Click X on a queued item to remove it
3. Drag a queued item to reorder
4. **Pass:** Remove and reorder work

---

## Part E: Transcript Viewer

### Scenario 23: Open Transcript Viewer
1. Click a transcribed recording's transcript
2. In-app viewer opens (not Finder/TextEdit)
3. Verify: timestamps `[00:00]`, `[01:23]` per segment
4. Verify: coloured speaker pills (legend + per segment)
5. **Pass:** Viewer opens with timestamps and speakers

### Scenario 24: Non-Diarized Transcript
1. Transcribe with **Speaker Labels unchecked**
2. Open transcript — timestamps shown, no speaker pills
3. **Pass:** Clean timestamp-only view

### Scenario 25: Audio Playback
1. Click play (▶) on a segment — audio plays for that time range
2. Click again to stop
3. Click play on a different segment — previous stops
4. **Pass:** Playback works per segment

### Scenario 26: Rename Speakers
1. Click a speaker pill in the legend
2. Type a name, press Enter
3. All instances update, click Save
4. **Pass:** All instances renamed, saved

### Scenario 27: Merge Speakers
1. Right-click a speaker pill
2. Select "Merge into Speaker X"
3. All segments reassigned, consecutive same-speaker merged
4. **Pass:** Speakers consolidated

### Scenario 28: Undo Merge
1. After merging, click Undo (or Cmd+Z)
2. Merge reversed — try multiple undos
3. **Pass:** Undo restores previous state

### Scenario 29: Re-Diarize
1. Set speaker count stepper to correct number (e.g. 2)
2. Click **Re-diarize** — completes in ~30 seconds
3. Viewer reopens with updated speaker assignments
4. **Pass:** Fast re-diarize, improved assignments

---

## Part F: Audio Editing

### Scenario 30: Trim (Save as Copy)
1. Tick 1 downloaded recording, click **Trim**
2. Enter start `01:00`, end `05:00`, leave "Save as copy" checked
3. Click Trim — `{name}-trimmed.mp3` created
4. **Pass:** Copy created

### Scenario 31: Trim (Replace Original)
1. Tick 1 recording, click Trim
2. **Uncheck** "Save as copy", enter times, click Trim
3. Original file replaced with trimmed version
4. **Pass:** Original replaced

### Scenario 32: Merge Recordings
1. Tick 2+ downloaded recordings, click **Merge**
2. `Merged-{first}-to-{last}.mp3` appears
3. Play it — audio concatenated in order
4. **Pass:** Merged correctly

### Scenario 33: Merge Filename Collision
1. Merge the same recordings again
2. New file with numbered suffix (e.g. `-1.mp3`)
3. **Pass:** No overwrite

### Scenario 34: Context Menu Actions
1. Right-click a downloaded recording:
   - **Download** (for un-downloaded)
   - **Mark as Downloaded**
   - **Transcribe**
   - **Show in Finder**
   - **Open Transcript** (if transcribed)
   - **Trim…**
   - **Merge Selected** (if 2+ ticked)
2. Verify each action works
3. **Pass:** All context menu items functional

---

## Part G: Corrections Dictionary

### Scenario 35: Apply Corrections
1. Create `~/HiDock/corrections.json`:
   ```json
   {"corrections": {"volaris": "VOLARIS", "hidoc": "HiDock"}}
   ```
2. Transcribe a recording with those words
3. Verify corrections applied in output
4. **Pass:** Words replaced

---

## Part H: Transcript Quality

### Scenario 36: Markdown Format
1. Open a `.md` transcript in a text editor
2. Verify: YAML frontmatter (title, date, speakers, model)
3. Model: `OpenAI Whisper large-v3-turbo (809M params, multilingual)`
4. Timestamps per segment, speaker labels if diarized
5. No hallucinated repeated text at end
6. **Pass:** Clean markdown

### Scenario 37: Status Badge Indicators
1. In the recording table, check the status/transcription column:
   - Un-transcribed: em dash (—)
   - Transcribing: progress spinner
   - Transcribed + speakers tagged: green checkmark (✓)
   - Transcribed + speakers need tagging: orange tag icon
2. **Pass:** All badge states display correctly

---

## Part I: Model Management

### Scenario 38: Download Whisper Model
1. If model not yet downloaded, click **Download Model**
2. Progress bar should show download progress (~550 MB)
3. When complete, model status should show ready
4. **Pass:** Model downloads, status updates

### Scenario 39: Model Manager
1. Open Model Manager from menu
2. Verify model statuses listed (downloaded size, ready state)
3. Try deleting a model, then re-downloading
4. **Pass:** Model management works

---

## Part J: Voice Library

### Scenario 40: View Voice Library
1. Open **Voice Library** from the menu
2. Verify enrolled speakers are listed (from previous speaker tagging)
3. Each entry should show name and sample count
4. **Pass:** Library loads with entries

### Scenario 41: Manage Voice Library
1. Rename a speaker in the voice library
2. Delete a speaker entry
3. **Pass:** Rename and delete work

---

## Part K: Device Manager

### Scenario 42: View Paired Devices
1. Open Device Manager from menu
2. HiDock H1 should be listed
3. **Pass:** Device listed

### Scenario 43: Forget and Re-Pair
1. Click Forget on your device
2. Device disappears, recording list empties
3. Click Pair — follow pairing flow
4. Device reappears, recordings reload
5. **Pass:** Forget/re-pair cycle works

### Scenario 44: Volume Device (USB Recorder / SD Card)
1. Plug in USB audio recorder or SD card
2. Click "Scan Volumes" in Device Manager
3. Volume detected — pair it
4. Recordings appear in sync list
5. Download a recording from the volume
6. **Pass:** Full volume device workflow

---

## Part L: Appearance & Preferences

### Scenario 45: Appearance Mode
1. Click the appearance toggle (footer bar)
2. Switch between Dark, Light, Auto
3. UI should update immediately
4. Relaunch — setting persists
5. **Pass:** Theme changes, persists

### Scenario 46: Notification Preferences
1. Find notification toggles (transcription complete, download complete, mic changes)
2. Toggle each off/on
3. Trigger the event — notification should/shouldn't appear based on setting
4. **Pass:** Toggles control notifications

---

## Part M: Disconnect Resilience

### Scenario 47: Disconnect Mid-Session
1. Unplug HiDock while app is open
2. Status updates to "Not connected"
3. Recording list **remains visible**
4. Downloaded recordings still openable/transcribable
5. Downloads disabled for un-downloaded recordings
6. Plug back in — status returns to "Connected"
7. **Pass:** Graceful disconnect/reconnect

---

## Part N: Feedback & Updates

### Scenario 48: Submit Feedback
1. Click "Send Feedback" from menu
2. Fill in form, submit
3. Should create a GitHub issue
4. **Pass:** Submitted

### Scenario 49: Feedback History
1. Open Feedback History
2. Search, filter (open/closed), sort
3. Click entry — detail panel shows full text
4. **Pass:** All controls work

### Scenario 50: Check for Updates
1. Click "Check for Updates" from menu
2. Status shown in footer bar
3. If update available, dialog should appear
4. **Pass:** Update check runs

---

## Part O: Cowork Prompt

### Scenario 51: Cowork Prompt Dialog
1. Open the Cowork Prompt from menu/button
2. Dialog should appear with prompt text
3. Copy to clipboard should work
4. **Pass:** Dialog opens, copy works

---

## Part P: Onboarding

### Scenario 52: Onboarding Wizard
1. Reset: `defaults delete com.hidock.tools.hidock-mic-trigger hasCompletedOnboarding`
2. Relaunch app
3. Wizard appears — walk through steps: device, model, mic
4. Complete wizard — app functional after
5. **Pass:** Wizard completes

---

## Part Q: Intelligence Layer (Optional)

### Scenario 53: Whisper-Guard
1. Transcribe a recording
2. Check `~/Library/Logs/hidock-menubar.log` for "Whisper-Guard" messages
3. No hallucinated text in output
4. **Pass:** Guard runs

### Scenario 54: Knowledge Graph
1. Check `~/HiDock/knowledge.db` exists after transcriptions
2. **Pass:** Database created

### Scenario 55: LLM Summarization (requires CLI)
1. Transcribe with `--summarize` (if `claude`/`ollama`/`gemini` CLI available)
2. Check for "## Summary" section in transcript
3. **Pass:** Summary with action items, decisions, key points

### Scenario 56: Obsidian Sync (if configured)
1. Verify notes sync to vault with `[[wikilinks]]`
2. **Pass:** Notes appear

### Scenario 57: MCP Server (if configured)
1. Query meeting knowledge via MCP tools
2. **Pass:** Responds correctly

---

## Part R: CI & Build

### Scenario 58: Push and Verify CI
1. `git push origin main`
2. Check GitHub Actions — macOS + Windows builds trigger
3. Both pass, Python tests pass (88+)
4. **Pass:** All CI green

---

## Quick Checklist

| # | Test | Status |
|---|------|--------|
| **App Launch & Mic Trigger** | | |
| 1 | App launches with recordings (no HiDock) | |
| 2 | Mic trigger start / stop | |
| 3 | Auto-start toggle persists | |
| 4 | Mic selection + preferred/fallback | |
| **Connection & Recordings** | | |
| 5 | HiDock connects, full list (200+) | |
| 6 | Column sorting (7 columns) | |
| 7 | Filter by device | |
| 8 | Hide downloaded / select controls | |
| **Download** | | |
| 9 | Download a recording | |
| 10 | Stop download mid-progress | |
| 11 | Auto-download toggle | |
| 12 | Mark as downloaded | |
| 13 | Choose recordings folder | |
| 14 | Choose transcripts folder | |
| **Transcription** | | |
| 15 | Transcribe with smooth progress | |
| 16 | Download → transcribe chain | |
| 17 | Transcribe All | |
| 18 | Re-transcribe (alert) | |
| 19 | Queue multiple transcriptions | |
| 20 | Queue pause / resume | |
| 21 | Queue cancel all | |
| 22 | Queue remove / reorder | |
| **Transcript Viewer** | | |
| 23 | Viewer opens in-app | |
| 24 | Non-diarized (timestamps only) | |
| 25 | Audio playback per segment | |
| 26 | Rename speakers | |
| 27 | Merge speakers (right-click) | |
| 28 | Undo merge (Cmd+Z) | |
| 29 | Re-diarize with speaker count | |
| **Audio Editing** | | |
| 30 | Trim (save as copy) | |
| 31 | Trim (replace original) | |
| 32 | Merge recordings | |
| 33 | Merge filename collision | |
| 34 | Context menu (all 7 actions) | |
| **Corrections** | | |
| 35 | Corrections dictionary applied | |
| **Transcript Quality** | | |
| 36 | Markdown format correct | |
| 37 | Status badge indicators (4 states) | |
| **Model Management** | | |
| 38 | Download Whisper model | |
| 39 | Model Manager (view/delete) | |
| **Voice Library** | | |
| 40 | View enrolled speakers | |
| 41 | Rename / delete speaker | |
| **Device Manager** | | |
| 42 | View paired devices | |
| 43 | Forget / re-pair device | |
| 44 | Volume device workflow | |
| **Preferences** | | |
| 45 | Appearance mode (dark/light/auto) | |
| 46 | Notification preferences | |
| **Resilience** | | |
| 47 | Disconnect / reconnect persistence | |
| **Feedback & Updates** | | |
| 48 | Submit feedback | |
| 49 | Feedback history (search/filter/sort) | |
| 50 | Check for updates | |
| **Other** | | |
| 51 | Cowork prompt dialog | |
| 52 | Onboarding wizard | |
| **Intelligence (Optional)** | | |
| 53 | Whisper-Guard | |
| 54 | Knowledge graph | |
| 55 | LLM summarization | |
| 56 | Obsidian sync | |
| 57 | MCP server | |
| **CI** | | |
| 58 | CI builds pass | |
