import SwiftUI
import Combine

final class HiDockViewModel: ObservableObject {
    // MARK: - Mic Trigger State
    @Published var triggerRunning = false
    @Published var triggerPID: Int32?
    @Published var triggerUptime: String = ""
    @Published var selectedMicName: String?
    @Published var autoStartOnLaunch = true
    @Published var availableMics: [String] = []
    /// The mic-trigger's ffmpeg is currently streaming from a HiDock —
    /// meaning the HiDock is actively recording. Set/cleared when the
    /// trigger CLI emits 'IN USE' / 'NOT IN USE' lines. The HiDock's
    /// USB data endpoint is unreachable while this is true, so data
    /// queries will fail until the stream stops.
    @Published var hidockRecordingActive: Bool = false
    /// The CoreAudio device name ffmpeg is currently holding, e.g.
    /// "HiDock H1" or "HiDock P1". Nil when nothing is being held.
    /// Used by DeviceCardView to put the Recording chip on only the
    /// specific HiDock being recorded, not every card.
    @Published var hidockRecordingDeviceName: String? = nil

    // MARK: - Sync State
    @Published var syncStatus: String = "Not loaded"
    @Published var syncStatusLevel: StatusLevel = .secondary
    @Published var syncOutputFolder: String?
    @Published var syncTranscriptFolder: String?
    @Published var syncEntries: [HiDockSyncRecordingEntry] = []
    @Published var syncCheckedRecordings: Set<String> = []
    @Published var syncAutoDownload = false
    @Published var syncAutoTranscribe = false
    @Published var mergeGroups: [MergeGroup] = []
    @Published var expandedMergeGroups: Set<String> = []
    @Published var syncBusy = false
    @Published var syncDownloading = false
    @Published var syncDownloadProgress: String?
    @Published var syncSortKey: String = "created"
    @Published var syncSortAscending: Bool = false
    @Published var syncFilterDeviceId: String?
    /// Pipeline-stage filter for the recordings table. Defaults to
    /// `.all`. Combined with `syncFilterDeviceId` (AND) and the
    /// user's sort key inside `visibleEntries`.
    @Published var syncStatusFilter: SyncStatusFilter = .all
    @Published var syncPairedDevices: [HiDockPairedDevice] = []
    @Published var syncDeviceConnected: [String: Bool] = [:]
    @Published var syncPaired = false

    // MARK: - Local-file Operation State
    /// True while an ffmpeg trim is in flight. Separate from
    /// `syncBusy` so a device still probing / connecting doesn't
    /// disable Trim — the operation is purely local-file.
    @Published var trimBusy = false

    // MARK: - Merge candidate detection (split-recording finder)
    /// Chains the extractor flagged as likely-one-conversation. High
    /// confidence ones are styled stronger; the rest sit behind a
    /// "show all" toggle in the merge candidates sheet.
    @Published var mergeCandidates: [MergeCandidate] = []
    @Published var mergeCandidatesShowAll = false
    /// User-visible "Merge candidates (N)" count — hidden when zero.
    var mergeCandidateCountForBadge: Int {
        let pool = effectiveMergeCandidates
        return mergeCandidatesShowAll
            ? pool.count
            : pool.filter(\.high_confidence).count
    }
    /// Set of mp3 paths that appear in at least one currently-surfaced
    /// candidate. Used by the recordings table to draw the subtle
    /// blue left-border accent on those rows.
    var mergeCandidatePaths: Set<String> {
        let pool = effectiveMergeCandidates
        let visible = mergeCandidatesShowAll
            ? pool
            : pool.filter(\.high_confidence)
        return Set(visible.flatMap { $0.pieces.map(\.mp3_path) })
    }
    /// mp3 paths the user has ticked as "yes, include this in a merge".
    /// Multi-select within candidate rows: a 5-piece chain can be
    /// merged as a 3-of-5 subset by ticking only the pieces the user
    /// confirms belong together. Only paths visible in
    /// `mergeCandidatePaths` should ever be added here.
    @Published var mergeCandidatesTicked: Set<String> = []
    /// `true` once the user has ticked enough candidate rows to do a
    /// merge — drives the "Merge N selected" action's visibility.
    var canMergeTickedCandidates: Bool { mergeCandidatesTicked.count >= 2 }

    /// Candidates after filtering out chains where any piece is
    /// already a child of an active merge group. The visual heuristic
    /// is "if the row already shows the merge-triangle icon next to
    /// the folder, it's been merged — don't keep suggesting it." This
    /// double-defends against any race where the dismiss didn't
    /// propagate (state update lag, scan hadn't refreshed yet) by
    /// using the existing merge-groups state as the authoritative
    /// "already merged" signal. Used everywhere a candidate would
    /// otherwise surface (row tint, tick toggle, toolbar count,
    /// scroll target, right-click menu).
    var effectiveMergeCandidates: [MergeCandidate] {
        let mergedStems = Set(mergeGroups.flatMap(\.childNames)
            .map { ($0 as NSString).deletingPathExtension })
        return mergeCandidates.filter { cand in
            let chainStems = cand.pieces.map {
                ($0.mp3_name as NSString).deletingPathExtension
            }
            return !chainStems.contains(where: { mergedStems.contains($0) })
        }
    }
    /// Path of the first candidate row, used by the toolbar's count
    /// label to scroll the table to the first suggestion when clicked.
    var firstMergeCandidatePath: String? {
        let pool = effectiveMergeCandidates
        let visible = mergeCandidatesShowAll
            ? pool
            : pool.filter(\.high_confidence)
        return visible.first?.pieces.first?.mp3_path
    }

    var onScanMergeCandidates: () -> Void = {}
    var onMergeCandidate: (MergeCandidate) -> Void = { _ in }
    var onDismissMergeCandidate: (MergeCandidate) -> Void = { _ in }
    var onMergeTickedCandidates: () -> Void = {}
    /// Increment to ask the recordings table to scroll to the first
    /// candidate row. The table observes this via `.onChange` and
    /// scrolls when it bumps. Counter rather than bool because we
    /// want consecutive clicks to work even when the row is already
    /// visible (a re-bump still triggers `.onChange`).
    @Published var scrollToFirstCandidateTrigger: Int = 0

    // MARK: - Transcription State
    @Published var diarizeEnabled = false
    @Published var transcriptionBusy = false
    @Published var transcriptionCurrentFile: String?
    @Published var transcriptionProgress: Int = 0
    @Published var transcriptionFileIndex: Int = 0
    @Published var transcriptionFileCount: Int = 0
    @Published var transcriptionStatus: String = ""
    @Published var transcriptionPaused = false
    @Published var transcriptionQueue: [TranscriptionQueueItem] = []
    var onCancelTranscription: () -> Void = {}
    var onShowTranscriptionQueue: () -> Void = {}
    var onRemoveFromQueue: (String) -> Void = { _ in }
    var onMoveInQueue: (Int, Int) -> Void = { _, _ in }
    var onPauseTranscription: () -> Void = {}
    var onResumeTranscription: () -> Void = {}
    var onRediarize: (String, Int?) -> Void = { _, _ in }  // jsonPath, nSpeakers

    // MARK: - Merge-group transcript state

    /// Merge groups don't live in `syncEntries` (which only holds
    /// HiDock + imported recordings), so the per-row `transcribed` /
    /// `speakersTagged` flags never set for them. Tracking the state
    /// per merged-mp3-name here lets mergeTranscriptionIndicator and
    /// `needsTaggingCount` surface merge-rediarize results correctly.
    /// Populated by refreshTranscriptionState after each Python `status`
    /// query — same source of truth as the per-row state.
    @Published var mergedFileTranscribed: Set<String> = []
    @Published var mergedFileTagged: Set<String> = []
    @Published var mergedFileTranscriptPaths: [String: String] = [:]

    // MARK: - Computed

    /// True if any currently-checked recording has been locally
    /// trimmed. Drives the Download Selected button's label flip to
    /// "Re-download Selected" so the user sees what the click will
    /// actually do (overwrite the trimmed local file with the
    /// device original).
    var selectionIncludesTrimmed: Bool {
        syncEntries.contains { entry in
            syncCheckedRecordings.contains(entry.recording.name)
                && (entry.recording.trimmed ?? false)
        }
    }

    /// Paths whose last transcription attempt ended in failure or was
    /// cancelled. Used by the tag column to show an X icon so the user
    /// can tell at a glance which rows need a retry, and by bulk-select
    /// flows that want to surface "N recordings need a retry".
    var failedTranscriptionPaths: Set<String> {
        Set(transcriptionQueue
            .filter { $0.status == .failed || $0.status == .cancelled }
            .map(\.path))
    }

    /// Look up the captured error / cancellation message for a path.
    /// Returns nil for queued/transcribing/completed items. The view
    /// layer hands this to an alert when the red X is clicked.
    func transcriptionErrorMessage(for path: String) -> String? {
        transcriptionQueue.first(where: { $0.path == path })?.errorMessage
    }

    var visibleEntries: [HiDockSyncRecordingEntry] {
        var entries = syncEntries
        if let filterDeviceId = syncFilterDeviceId {
            // Imported recordings are always visible regardless of the device
            // filter — they aren't "on" any HiDock, so filtering by a specific
            // device shouldn't hide them.
            entries = entries.filter {
                $0.deviceId == filterDeviceId || $0.deviceId == "imported:local"
            }
        }
        // (Hide Downloaded toggle removed in 2026-04-26 cleanup —
        // it was a strict subset of the Filter dropdown's "On device"
        // / "Untranscribed" options, so keeping both was redundant.)
        //
        // Pipeline-stage filter, evaluated on the same statusText
        // cascade the table renders, so what the user picks always
        // matches what the rows display.
        switch syncStatusFilter {
        case .all:
            break
        case .onDevice:
            entries = entries.filter { $0.statusText == "On device" }
        case .downloaded:
            entries = entries.filter { $0.statusText == "Downloaded" }
        case .untranscribed:
            // Downloaded locally but no transcript yet, regardless of
            // device. Excludes Skipped (user opted out) and Imported
            // that's already been transcribed.
            entries = entries.filter {
                $0.recording.localExists && !$0.transcribed && !$0.transcriptionSkipped
            }
        case .transcribed:
            entries = entries.filter { $0.transcribed }
        case .skipped:
            entries = entries.filter { $0.statusText == "Skipped" }
        case .removed:
            entries = entries.filter { $0.statusText == "Removed" }
        case .failed:
            entries = entries.filter { $0.statusText == "Failed" }
        case .imported:
            entries = entries.filter { $0.deviceId == "imported:local" }
        }
        entries.sort { a, b in
            let ar = a.recording, br = b.recording
            let result: Bool
            switch syncSortKey {
            case "status":
                result = a.statusText.localizedCaseInsensitiveCompare(b.statusText) == .orderedAscending
            case "name":
                result = ar.outputName.localizedCaseInsensitiveCompare(br.outputName) == .orderedAscending
            case "created":
                let aKey = "\(ar.createDate) \(ar.createTime)"
                let bKey = "\(br.createDate) \(br.createTime)"
                result = aKey < bKey
            case "duration":
                result = ar.duration < br.duration
            case "size":
                result = ar.length < br.length
            case "path":
                result = ar.outputPath.localizedCaseInsensitiveCompare(br.outputPath) == .orderedAscending
            case "device":
                result = a.deviceName.localizedCaseInsensitiveCompare(b.deviceName) == .orderedAscending
            default:
                result = ar.createDate < br.createDate
            }
            return syncSortAscending ? result : !result
        }
        return entries
    }

    /// Build the display list with merge groups expanded/collapsed
    var displayRows: [DisplayRow] {
        let entries = visibleEntries
        // Collect all child names across all merge groups
        let allChildNames = Set(mergeGroups.flatMap(\.childNames))

        var rows: [DisplayRow] = []
        var insertedGroups = Set<String>()

        for entry in entries {
            // Check if this entry is a child of a merge group
            if let group = mergeGroups.first(where: { $0.childNames.contains(entry.recording.name) }) {
                // Insert the merge parent row at the position of the first child we encounter
                if !insertedGroups.contains(group.id) {
                    insertedGroups.insert(group.id)
                    rows.append(.mergeParent(group))
                }
                // Show child only if the group is expanded
                if expandedMergeGroups.contains(group.id) {
                    rows.append(.mergeChild(entry))
                }
            } else {
                rows.append(.recording(entry))
            }
        }

        return rows
    }

    var hasSelection: Bool {
        !syncCheckedRecordings.isEmpty
    }

    var anySelectedMarkedOnly: Bool {
        // Unskip applies to either:
        //  1. download-skipped (downloaded flag set but file absent), or
        //  2. transcription-skipped (user opted out of transcribing a
        //     locally-present recording).
        guard hasSelection else { return false }
        return syncEntries.contains {
            guard syncCheckedRecordings.contains($0.recording.name) else { return false }
            let downloadSkipped = $0.recording.downloaded && !$0.recording.localExists
            return downloadSkipped || $0.transcriptionSkipped
        }
    }

    var needsTaggingCount: Int {
        let perRow = syncEntries.filter { $0.transcribed && !$0.speakersTagged }.count
        // Add merged-file rows that are transcribed-but-not-tagged.
        // These don't live in syncEntries, so without this they'd
        // never show up in the count even when the rediarize step
        // produced a fresh transcript that needs speaker labelling.
        let perMerge = mergedFileTranscribed.subtracting(mergedFileTagged).count
        return perRow + perMerge
    }

    var syncSummary: String {
        let visible = visibleEntries
        let downloadedCount = syncEntries.filter(\.recording.downloaded).count
        let selectedCount = syncCheckedRecordings.count
        var parts = ["\(visible.count) shown", "\(syncEntries.count) total", "\(downloadedCount) downloaded"]
        if selectedCount > 0 {
            parts.append("\(selectedCount) selected")
        }
        return parts.joined(separator: " · ")
    }

    // MARK: - Action Closures (set by AppDelegate)
    var onStartTrigger: () -> Void = {}
    var onStopTrigger: () -> Void = {}
    var onToggleAutoStart: () -> Void = {}
    var onSelectMic: (String) -> Void = { _ in }
    /// Per-device storage summary, keyed by deviceId. Summed recording
    /// sizes from the device catalogue — a minimum bound when firmware
    /// truncation flag is set.
    @Published var syncDeviceStorage: [String: HiDockStorageStats] = [:]

    /// Per-device last-error timestamp and message. When a status query
    /// fails (extractor timeout, USB EIO, access denied) we record the
    /// reason here so the storage summary can flag "H1: unreachable" and
    /// the user knows why new recordings aren't showing up even though
    /// the existing list looks fine.
    @Published var syncDeviceLastError: [String: (String, Date)] = [:]
    /// Per-device last-successful-query timestamp. Pair with lastError to
    /// decide whether to show "unreachable" vs normal storage line.
    @Published var syncDeviceLastOK: [String: Date] = [:]

    /// Known on-device storage capacities in bytes, keyed by product
    /// short-name. Sourced from HiDock's published product specs and
    /// user-verified on a real device. Used to compute free space since
    /// the USB protocol we've implemented doesn't expose a capacity query.
    /// 1 GB = 1,073,741,824 bytes (binary GB, matching what the vendor
    /// lists as "32 GB" / "64 GB" on the product page).
    private static let knownCapacityBytes: [String: Int64] = [
        "H1":  32 * 1_073_741_824,
        "H1E": 32 * 1_073_741_824,
        "P1":  64 * 1_073_741_824,
    ]

    /// Human-readable summary shown in the status header, e.g.
    /// "H1: 4.4 / 32 GB (28 free) · P1: 1.1 / 64 GB (63 free)".
    /// Empty string if nothing usable.
    var storageSummary: String {
        let parts = syncPairedDevices.compactMap { device -> String? in
            // If the device's last query failed AND hasn't recovered since,
            // surface that prominently. Preserves the last-known stats as
            // context so the user knows what state the catalogue is frozen
            // at — not a silent staleness.
            if let (errMsg, errAt) = syncDeviceLastError[device.deviceId],
               (syncDeviceLastOK[device.deviceId].map { $0 < errAt } ?? true) {
                let f = DateFormatter()
                f.dateFormat = "HH:mm"
                let when = f.string(from: errAt)
                let short = errMsg.split(separator: "—").first.map(String.init)?
                    .trimmingCharacters(in: .whitespaces) ?? errMsg
                if let stats = syncDeviceStorage[device.deviceId] {
                    let gb = Double(stats.totalBytesReturned) / 1_073_741_824
                    return "\(device.shortName): ⚠ \(short) @ \(when) — last seen \(String(format: "%.1f GB", gb)) (\(stats.totalFiles) files)"
                }
                return "\(device.shortName): ⚠ \(short) @ \(when)"
            }
            guard let stats = syncDeviceStorage[device.deviceId] else { return nil }
            let usedBytes = Int64(stats.totalBytesReturned)
            let usedGB = Double(usedBytes) / 1_073_741_824
            let usedMB = Double(usedBytes) / 1_048_576
            let usedStr = usedGB >= 1 ? String(format: "%.1f", usedGB) : String(format: "%.0f MB", usedMB)
            let truncFlag = stats.truncated ? "+" : ""

            // If we know the device's capacity, show used / total (free).
            // Otherwise fall back to just used-size so we never show
            // misleading free-space numbers.
            if let cap = Self.knownCapacityBytes[device.shortName] {
                let capGB = Double(cap) / 1_073_741_824
                let freeGB = max(0, capGB - usedGB)
                // When the list is truncated, actual usage is higher — so
                // free is an upper bound. Flag with '≤'.
                let freeStr = stats.truncated
                    ? String(format: "≤%.0f GB free", freeGB)
                    : String(format: "%.0f GB free", freeGB)
                return String(
                    format: "%@: %@%@ / %.0f GB (%@, %d files)",
                    device.shortName, usedStr, truncFlag, capGB, freeStr, stats.totalFiles
                )
            } else {
                return "\(device.shortName): \(usedStr)\(truncFlag) GB (\(stats.totalFiles) files)"
            }
        }
        return parts.joined(separator: " · ")
    }

    var onRefreshSync: () -> Void = {}
    var onImportAudioFile: () -> Void = {}
    var onRemoveImport: (String) -> Void = { _ in }
    /// Transcribe a single recording with a user-supplied expected speaker
    /// count. Overrides the pipeline's automatic density-prior estimate for
    /// recordings where the user knows how many voices to expect (e.g.
    /// "this is a 1:1", "this is a 6-person hackathon panel").
    var onTranscribeWithSpeakerCount: (String, Int) -> Void = { _, _ in }
    /// Delete the locally-downloaded MP3 for a recording (keeps the device
    /// copy intact). Next device refresh will show it as "On device" again.
    var onDeleteLocalCopy: (String) -> Void = { _ in }
    /// Unified Remove for the currently-checked selection. Imports are
    /// removed entirely (file + JSON entry); downloaded HiDock recordings
    /// have their local MP3 deleted but the device copy is preserved.
    var onRemoveSelected: () -> Void = {}
    /// Force a fresh status query against one specific device. Clears
    /// any stale 'unreachable' flag and re-runs the extractor. Useful
    /// when a device recovers from a USB stall and the user wants to
    /// pick it up without waiting for the next auto-refresh cycle.
    var onReconnectDevice: (String) -> Void = { _ in }
    var onPairDock: () -> Void = {}
    var onUnpairDock: () -> Void = {}
    var onChooseRecordingsFolder: () -> Void = {}
    var onChooseTranscriptFolder: () -> Void = {}
    var onDownloadSelected: () -> Void = {}
    var onStopDownload: () -> Void = {}
    var onMarkDownloaded: () -> Void = {}
    var onSelectAll: () -> Void = {}
    var onSelectNone: () -> Void = {}
    var onSelectNotDownloaded: () -> Void = {}
    var onFilterByDevice: (String?) -> Void = { _ in }
    var onToggleChecked: (String, Bool) -> Void = { _, _ in }  // name, shiftHeld
    var onUnmarkDownloaded: () -> Void = {}
    var onToggleAutoDownload: () -> Void = {}
    var onToggleAutoTranscribe: () -> Void = {}
    var onToggleMergeExpand: (String) -> Void = { _ in }
    var onTranscribeSelected: () -> Void = {}
    var onToggleDiarize: () -> Void = {}
    var onRevealRecording: (String) -> Void = { _ in }
    var onRevealTranscript: (String) -> Void = { _ in }
    var onExportSRT: (String) -> Void = { _ in }
    var onOpenTranscriptViewer: (String) -> Void = { _ in }
    var onSendFeedback: () -> Void = {}
    var onShowFeedbackHistory: () -> Void = {}
    var onShowCoworkPrompt: () -> Void = {}
    var onOpenInObsidian: (String) -> Void = { _ in }
    var onMergeSelected: () -> Void = {}
    var onTrimRecording: (String) -> Void = { _ in }
    var onCheckForUpdates: () -> Void = {}
    var onShowVoiceLibrary: () -> Void = {}
    var onShowVoiceTraining: () -> Void = {}

    // MARK: - Onboarding State
    @Published var showOnboarding = false
    @Published var modelReady = false
    @Published var modelDownloadProgress: Double = 0
    @Published var modelDownloadStatus: String = ""
    @Published var modelDownloading = false
    var onDownloadModel: () -> Void = {}
    var onCancelModelDownload: () -> Void = {}
    var onCompleteOnboarding: () -> Void = {}

    // MARK: - Model Manager
    @Published var modelStatuses: [String: ModelStatus] = [:]
    var onDownloadModelByKey: (String) -> Void = { _ in }
    var onDeleteModelByKey: (String) -> Void = { _ in }
    var onRefreshModelStatuses: () -> Void = {}
    /// Set the given model as the active backend for its stage.
    /// The registry entry's stage metadata determines what gets
    /// persisted; only one model per stage can be active at a time.
    var onSetActiveModelByKey: (String) -> Void = { _ in }
    var onShowModelManager: () -> Void = {}

    // MARK: - Device Manager
    var onShowDeviceManager: () -> Void = {}
    var onForgetDevice: (HiDockPairedDevice) -> Void = { _ in }
    var onPairVolume: (String, String?) -> Void = { _, _ in } // volumeName, subpath
    var onScanVolumes: (@escaping ([VolumeScanResult]) -> Void) -> Void = { $0([]) }

    // MARK: - Notification Preferences
    @Published var notifyTranscriptionComplete: Bool = true
    @Published var notifyDownloadComplete: Bool = true
    @Published var notifyMicChanges: Bool = true
    var onToggleNotifyTranscription: () -> Void = {}
    var onToggleNotifyDownload: () -> Void = {}
    var onToggleNotifyMicChanges: () -> Void = {}

    // MARK: - Update Status
    @Published var updateStatusText: String = ""

    // MARK: - Appearance
    @Published var appearanceMode: String = "auto"  // "dark", "light", "auto"
    var onSetAppearance: (String) -> Void = { _ in }
}

extension HiDockViewModel {
    var appearanceIcon: String {
        switch appearanceMode {
        case "dark": return "moon.fill"
        case "light": return "sun.max.fill"
        default: return "circle.lefthalf.filled"
        }
    }

    var appearanceLabel: String {
        switch appearanceMode {
        case "dark": return "Dark"
        case "light": return "Light"
        default: return "Auto"
        }
    }
}
