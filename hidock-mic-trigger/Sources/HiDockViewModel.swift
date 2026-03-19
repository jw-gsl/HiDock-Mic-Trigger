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

    // MARK: - Sync State
    @Published var syncStatus: String = "Not loaded"
    @Published var syncStatusLevel: StatusLevel = .secondary
    @Published var syncOutputFolder: String?
    @Published var syncTranscriptFolder: String?
    @Published var syncEntries: [HiDockSyncRecordingEntry] = []
    @Published var syncCheckedRecordings: Set<String> = []
    @Published var syncHideDownloaded = false
    @Published var syncAutoDownload = false
    @Published var syncBusy = false
    @Published var syncDownloading = false
    @Published var syncDownloadProgress: String?
    @Published var syncSortKey: String = "created"
    @Published var syncSortAscending: Bool = false
    @Published var syncFilterDeviceProductId: Int?
    @Published var syncPairedDevices: [HiDockPairedDevice] = []
    @Published var syncDeviceConnected: [Int: Bool] = [:]
    @Published var syncPaired = false

    // MARK: - Transcription State
    @Published var transcriptionBusy = false
    @Published var transcriptionCurrentFile: String?
    @Published var transcriptionProgress: Int = 0
    @Published var transcriptionFileIndex: Int = 0
    @Published var transcriptionFileCount: Int = 0

    // MARK: - Computed
    var visibleEntries: [HiDockSyncRecordingEntry] {
        var entries = syncEntries
        if let filterPid = syncFilterDeviceProductId {
            entries = entries.filter { $0.deviceProductId == filterPid }
        }
        if syncHideDownloaded {
            entries = entries.filter { !$0.recording.downloaded }
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

    var hasSelection: Bool {
        !syncCheckedRecordings.isEmpty
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
    var onRefreshSync: () -> Void = {}
    var onPairDock: () -> Void = {}
    var onUnpairDock: () -> Void = {}
    var onChooseRecordingsFolder: () -> Void = {}
    var onChooseTranscriptFolder: () -> Void = {}
    var onDownloadSelected: () -> Void = {}
    var onDownloadNew: () -> Void = {}
    var onStopDownload: () -> Void = {}
    var onMarkDownloaded: () -> Void = {}
    var onSelectAll: () -> Void = {}
    var onSelectNone: () -> Void = {}
    var onSelectNotDownloaded: () -> Void = {}
    var onFilterByDevice: (Int?) -> Void = { _ in }
    var onToggleChecked: (String) -> Void = { _ in }
    var onToggleHideDownloaded: () -> Void = {}
    var onToggleAutoDownload: () -> Void = {}
    var onTranscribeSelected: () -> Void = {}
    var onTranscribeAll: () -> Void = {}
    var onRevealRecording: (String) -> Void = { _ in }
    var onRevealTranscript: (String) -> Void = { _ in }
}
