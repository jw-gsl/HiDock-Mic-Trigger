# Remove automatic Plaud device-filter behavior
Research date: 2026-06-24
Sources: hidock-mic-trigger/Sources/AppDelegate.swift, Windows-App/ui/main_window.py
Branch: fix/no-auto-plaud-filter

## Current State
User reported the recordings table kept "auto-filtering to the Plaud list"
without clicking any filter control.

## Findings
Root cause (macOS only): in the `download-new` completion handler in
`AppDelegate.swift` (~line 6332), introduced in commit `4965176` (heatmap PR #45,
2026-06-22):

```swift
if device.deviceType == .plaud && deviceDownloaded > 0 {
    self.syncFilterDeviceId = device.deviceId
    self.viewModel.statusFilters = []
    self.log("Plaud fresh downloads: showing ... rows and clearing status filter")
}
```

Every time a Plaud device pulled ≥1 new recording, the app force-set the device
filter to that Plaud device and wiped status filters. Because auto-download
(`scheduleAutoDownloadNewRecordings` → `downloadNewSyncRecordings` →
`downloadNewFromDevices`) re-runs on a debounce timer, the filter kept snapping
back to Plaud during background syncs — not user clicks.

Windows app: **already correct** — `_refresh_device_filter_combo()` preserves the
current selection and nothing force-selects a Plaud device on download. No change
needed there; removing the macOS block brings the platforms back into parity.

## Completed
- [x] Removed the auto-filter block in `AppDelegate.swift`, replaced with a
      comment documenting that filters change only on explicit user action.
- [x] Audited all filter mutation sites (`syncFilterDeviceId`, `statusFilters`,
      `summaryTypeFilter`): every remaining one is inside a Button/Menu action
      (filter dropdown, device card) — i.e. user-driven only.
- [x] Confirmed Windows app needs no change.

## Planned
- [ ] User to review PR; build/deploy macOS (Debug) per the ask-before-xcodebuild rule.
- [ ] PARITY.md: no new row needed (behavior now matches; both platforms = filter
      changes only on user action).

## Rejected / Not Applicable
- Keeping the jump but not wiping status filters — still re-snaps on every
  auto-download cycle, so rejected. User wants zero automatic filter changes.
