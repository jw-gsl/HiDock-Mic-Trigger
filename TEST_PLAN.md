# HiDock Tools — Test Plan

## Current Test Coverage

| Component | Tests | Coverage |
|-----------|-------|----------|
| USB Extractor | 88 | Protocol parsing, filenames, recording status, validation, volume commands |
| Windows App | 45 | Config, state, USB sync parsing, device models, notifications |
| macOS App (Swift) | 20 | Helpers, Models (device types, paired devices, Codable compat, recording entries) |
| Transcription Pipeline | 28 | State management, CLI parsing, voice library I/O |
| Shared modules | 50 | Audio utils, diarize, voice library, models |

## Automated Test Execution

```bash
# All Python tests (133 total)
python -m pytest usb-extractor/tests/ Windows-App/tests/ -q

# Full suite including shared & transcription
python3 -m pytest transcription-pipeline/tests/ usb-extractor/tests/ Windows-App/tests/ shared/tests/ -v

# Swift tests (macOS only)
cd hidock-mic-trigger && xcodebuild test -scheme hidock-mic-trigger -quiet
```

---

## Manual Test Plan

This is the end-to-end manual test plan. Test on **both macOS and Windows** unless marked platform-specific. For each step, record: Pass / Fail / Blocked, plus any notes.

Use speech-to-text to narrate what you see as you go — we'll parse the results into issues afterwards.

---

### Prerequisites

Before starting:

- [ ] macOS app is installed and launchable
- [ ] Windows app is installed and launchable
- [ ] At least one HiDock device available (or skip HiDock-specific tests)
- [ ] At least one USB volume device with audio files available (e.g. ZOOM recorder, SD card with WAV/MP3 files) — or skip volume-specific tests
- [ ] Internet connection (for update checker tests)

---

### 1. App Launch & First Run

| # | Step | Expected Result | macOS | Windows | Notes |
|---|------|----------------|-------|---------|-------|
| 1.1 | Launch app for the first time (or after clearing settings) | Onboarding wizard appears | | | |
| 1.2 | Click "Skip" on every onboarding step | App opens to main window, no crash | | | |
| 1.3 | Re-launch app | Onboarding does NOT appear again | | | |
| 1.4 | Verify main window layout | Menu bar (macOS) or menu + toolbar (Windows), recording table, footer bar all visible | | | |

---

### 2. Device Manager — Opening & Layout

| # | Step | Expected Result | macOS | Windows | Notes |
|---|------|----------------|-------|---------|-------|
| 2.1 | **macOS**: Click "Devices" button in footer bar. **Windows**: Actions menu > "Devices..." | Device Manager dialog opens | | | |
| 2.2 | Check dialog title | "Device Manager" | | | |
| 2.3 | Check layout | Search field, Type filter (All/HiDock/Volume), Sort picker (Name/Type/Paired), device list, footer with device count + "Pair Volume" button | | | |
| 2.4 | With no devices paired, check empty state | Shows "No devices paired" message with guidance text | | | |
| 2.5 | Close and reopen Device Manager | Opens without error, state preserved | | | |

---

### 3. Device Manager — Pair HiDock

| # | Step | Expected Result | macOS | Windows | Notes |
|---|------|----------------|-------|---------|-------|
| 3.1 | Connect a HiDock device via USB | Device should be detectable | | | |
| 3.2 | Pair the HiDock (via main UI "Pair" button or Device Manager) | HiDock appears in device list with name, product ID, type badge "HiDock" | | | |
| 3.3 | Check "Connected" badge | Green "Connected" badge visible next to paired HiDock | | | |
| 3.4 | Check device count in footer | Shows "1 device paired" (or correct count) | | | |

---

### 4. Device Manager — Pair Volume Device

| # | Step | Expected Result | macOS | Windows | Notes |
|---|------|----------------|-------|---------|-------|
| 4.1 | Connect a USB volume device (e.g. ZOOM recorder, USB drive with audio files) | Device mounts as a USB volume | | | |
| 4.2 | In Device Manager, click "Pair Volume" | **macOS**: Popover appears with "Pair USB Volume" heading. **Windows**: Inline widget with Scan button, combo box, subfolder field | | | |
| 4.3 | **macOS**: Verify auto-scan starts. **Windows**: Click "Scan" button | Scanning indicator appears ("Scanning..." or spinner) | | | |
| 4.4 | Wait for scan to complete | Discovered volumes listed, each showing volume name and audio file count (e.g. "ZOOM_H1 — 5 audio files") | | | |
| 4.5 | Select a discovered volume (or type a name manually) | Volume name populates in the input field / combo box | | | |
| 4.6 | Optionally enter a subfolder path | Subfolder field accepts text | | | |
| 4.7 | Click "Pair" (macOS) or "Pair Volume" (Windows) | Volume device added to device list. Type badge shows "Volume". Details show volume name and subfolder if set | | | |
| 4.8 | Try to pair the same volume again | Should show as already paired or prevent duplicate | | | |

---

### 5. Device Manager — Search, Filter, Sort

| # | Step | Expected Result | macOS | Windows | Notes |
|---|------|----------------|-------|---------|-------|
| 5.1 | With multiple devices paired (HiDock + Volume), type in search field | List filters to matching devices by name, volume name, or device ID | | | |
| 5.2 | Clear search, select "HiDock" from type filter | Only HiDock devices shown | | | |
| 5.3 | Select "Volume" from type filter | Only Volume devices shown | | | |
| 5.4 | Select "All" from type filter | All devices shown | | | |
| 5.5 | Change sort to "Name" | Devices sorted alphabetically by name | | | |
| 5.6 | Change sort to "Type" | Devices grouped by type | | | |
| 5.7 | Change sort to "Paired" | Devices sorted by pairing date (most recent first) | | | |

---

### 6. Device Manager — Forget Device

| # | Step | Expected Result | macOS | Windows | Notes |
|---|------|----------------|-------|---------|-------|
| 6.1 | Click "Forget" button on a paired device | Device removed from list | | | |
| 6.2 | Check device count updates | Footer count decreases by 1 | | | |
| 6.3 | Close and reopen Device Manager | Forgotten device does not reappear (persisted) | | | |
| 6.4 | Refresh sync in main window | Forgotten device's recordings no longer appear | | | |

---

### 7. Sync Refresh — Multi-Device

| # | Step | Expected Result | macOS | Windows | Notes |
|---|------|----------------|-------|---------|-------|
| 7.1 | Pair both a HiDock and a Volume device | Both appear in Device Manager | | | |
| 7.2 | Click "Refresh" in main window | Status refreshes for ALL paired devices | | | |
| 7.3 | Check recording table | Recordings from both devices appear, each with correct device name in "Device" column | | | |
| 7.4 | Check connection status per device | Connected devices show green, disconnected show appropriate status | | | |
| 7.5 | Disconnect one device, click Refresh | Disconnected device shows not-connected status, other device still shows recordings | | | |

---

### 8. Device Filter in Toolbar

| # | Step | Expected Result | macOS | Windows | Notes |
|---|------|----------------|-------|---------|-------|
| 8.1 | With recordings from multiple devices, find the filter controls | **macOS**: "Filter:" label + device buttons. **Windows**: "Filter:" label + combo box dropdown | | | |
| 8.2 | Verify "All" is selected by default | All recordings from all devices shown | | | |
| 8.3 | Click/select a specific device | Table shows ONLY that device's recordings | | | |
| 8.4 | Check that recording count updates | Summary reflects filtered view | | | |
| 8.5 | Click/select "All" again | All recordings reappear | | | |
| 8.6 | Click Refresh while filter is active | Filter persists — same device selected, table still filtered | | | |

---

### 9. Download from Volume Device

| # | Step | Expected Result | macOS | Windows | Notes |
|---|------|----------------|-------|---------|-------|
| 9.1 | With a volume device paired and connected, click Refresh | Volume recordings appear with device name and status | | | |
| 9.2 | Select one or more volume recordings | Rows selected/checked | | | |
| 9.3 | Click "Download Selected" | Download starts, progress bar visible, files imported from volume | | | |
| 9.4 | Wait for download to complete | Progress completes, status changes to "Downloaded" | | | |
| 9.5 | Check recordings output folder | Downloaded files present at expected location | | | |
| 9.6 | Check notification on completion | **macOS**: Notification says "Download Complete" (NOT "HiDock Download Complete") | | | macOS notification |

---

### 10. Download New — Multi-Device

| # | Step | Expected Result | macOS | Windows | Notes |
|---|------|----------------|-------|---------|-------|
| 10.1 | With both HiDock and Volume devices having new recordings | Both show "New" status entries | | | |
| 10.2 | Click "Download New" | Downloads run for both devices in sequence | | | |
| 10.3 | Wait for completion | All new recordings downloaded, statuses updated | | | |
| 10.4 | Click "Download New" again | Nothing new to download — completes quickly, no error | | | |

---

### 11. Mark as Downloaded — Volume Device

| # | Step | Expected Result | macOS | Windows | Notes |
|---|------|----------------|-------|---------|-------|
| 11.1 | Select downloaded volume recordings | Rows selected | | | |
| 11.2 | Click "Mark Done" or context menu > "Mark as Downloaded" | Status updates to downloaded | | | |
| 11.3 | Click Refresh | Marked recordings still show as downloaded (state persisted) | | | |
| 11.4 | Enable "Hide Downloaded" checkbox | Marked recordings disappear from view | | | |
| 11.5 | Disable "Hide Downloaded" | Marked recordings reappear | | | |

---

### 12. Auto-Download

| # | Step | Expected Result | macOS | Windows | Notes |
|---|------|----------------|-------|---------|-------|
| 12.1 | Enable "Auto-download" checkbox | Checkbox checked, setting persisted | | | |
| 12.2 | Connect a device with new recordings, wait for refresh | New recordings automatically download | | | |
| 12.3 | Disable "Auto-download" | No auto-download on next refresh | | | |

---

### 13. Mic Trigger

| # | Step | Expected Result | macOS | Windows | Notes |
|---|------|----------------|-------|---------|-------|
| 13.1 | Select a microphone from the dropdown | Mic selected | | | |
| 13.2 | Click Start | Status "Running", green indicator, uptime counting | | | |
| 13.3 | Wait 30 seconds | Uptime shows elapsed time | | | |
| 13.4 | Click Stop | Status "Stopped", gray indicator | | | |
| 13.5 | Enable "Auto-start on launch" | Setting persisted | | | |
| 13.6 | Relaunch app | Trigger starts automatically | | | |

---

### 14. Transcription

| # | Step | Expected Result | macOS | Windows | Notes |
|---|------|----------------|-------|---------|-------|
| 14.1 | Ensure a model is downloaded (Model Manager) | Model shows "Installed" | | | |
| 14.2 | Select a downloaded recording | Row selected | | | |
| 14.3 | Click "Transcribe" (toolbar or context menu) | Progress bar with file count | | | |
| 14.4 | Wait for completion | Transcript saved, checkmark in table | | | |
| 14.5 | Click Cancel during transcription | Stops, progress bar disappears | | | |
| 14.6 | Right-click > "Open Transcript" | Transcript viewer opens with text | | | |

---

### 15. Speaker Diarization & Voice Library

| # | Step | Expected Result | macOS | Windows | Notes |
|---|------|----------------|-------|---------|-------|
| 15.1 | Enable "Speaker Labels" toggle | Setting persisted | | | |
| 15.2 | Transcribe recording with multiple speakers | Transcript shows speaker-labeled segments with colors | | | |
| 15.3 | Click a speaker name to rename | Name field editable | | | |
| 15.4 | Enter a name and confirm | All instances update, voice enrolled | | | |
| 15.5 | Open Voice Library | Enrolled speaker with sample count | | | |
| 15.6 | Transcribe another recording with same speaker | Speaker auto-identified | | | |

---

### 16. Model Manager

| # | Step | Expected Result | macOS | Windows | Notes |
|---|------|----------------|-------|---------|-------|
| 16.1 | Open Model Manager | Models listed with names, sizes, status | | | |
| 16.2 | Download an uninstalled model | Progress bar, completes | | | |
| 16.3 | Delete an installed model | Removed, status "Not installed" | | | |
| 16.4 | Refresh | Statuses correct | | | |

---

### 17. Appearance

| # | Step | Expected Result | macOS | Windows | Notes |
|---|------|----------------|-------|---------|-------|
| 17.1 | Select "Dark" | Dark theme applied | | | |
| 17.2 | Select "Light" | Light theme applied | | | |
| 17.3 | Select "Auto" | Follows system | | | |
| 17.4 | Relaunch app | Theme persisted | | | |

---

### 18. Notifications

| # | Step | Expected Result | macOS | Windows | Notes |
|---|------|----------------|-------|---------|-------|
| 18.1 | Complete a download | **macOS**: "Download Complete" (generic). **Windows**: Status bar updates | | | |
| 18.2 | Complete a transcription | Notification on both platforms | | | |
| 18.3 | Disable notifications in preferences | No notifications for subsequent events | | | |
| 18.4 | Re-enable notifications | Notifications resume | | | |

---

### 19. Feedback & Update Checker

| # | Step | Expected Result | macOS | Windows | Notes |
|---|------|----------------|-------|---------|-------|
| 19.1 | Click "Send Feedback" | Form with categories/severity | | | |
| 19.2 | Submit feedback | Issue created, confirmation | | | |
| 19.3 | Click "My Feedback" | History shown | | | |
| 19.4 | Click "Check for Updates" | Shows version result | | | |

---

### 20. Onboarding (Full Flow)

| # | Step | Expected Result | macOS | Windows | Notes |
|---|------|----------------|-------|---------|-------|
| 20.1 | Clear settings, launch app | Wizard at Step 1 (Welcome) | | | |
| 20.2 | Click Next | Step 2 (Connect HiDock) | | | |
| 20.3 | Connect HiDock during step 2 | Auto-detected, auto-advances | | | |
| 20.4 | Step 3: select a mic | Dropdown populated, persisted | | | |
| 20.5 | Step 4: download a model | Progress bar, completes | | | |
| 20.6 | Step 5: click "Get Started" | Onboarding closes, main window shown | | | |
| 20.7 | Click Back on any step | Returns to previous step | | | |
| 20.8 | Click Skip on any step | Advances, shown as "Skipped" in dots | | | |

---

### 21. Cross-Platform Parity Spot Check

| # | Step | Expected Result | macOS | Windows | Notes |
|---|------|----------------|-------|---------|-------|
| 21.1 | Compare footer bar buttons | Both: Appearance, Models, Voice Library, Updates, Feedback | | | |
| 21.2 | Compare Device Manager layout | Both: search, type filter, sort, device list, Pair Volume | | | |
| 21.3 | Compare recording table columns | Both: Device, Status, Transcribed, Name, DateTime, Duration, Size, Path | | | |
| 21.4 | Compare toolbar controls | Both: Select All/None/New, Device filter, Hide Downloaded, Auto-download | | | |
| 21.5 | Compare context menu items | Both: Download, Mark Downloaded, Transcribe, Show in Finder/Open Location, Open Transcript | | | |

---

## Test Results Template

Copy this for each test session:

```
## Test Session: [DATE]
Platform: macOS / Windows
App Version: [version]
Tester: [name]

### Results
[Paste or dictate results here — note the test number and Pass/Fail/Blocked + any observations]

### Issues Found
1. [Test #] — [Brief description of issue]
2. ...

### Blocked Tests
1. [Test #] — [Why blocked]
2. ...
```
