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
    @Published var syncFilterDeviceId: String?
    @Published var syncPairedDevices: [HiDockPairedDevice] = []
    @Published var syncDeviceConnected: [String: Bool] = [:]
    @Published var syncPaired = false

    // MARK: - Transcription State
    @Published var diarizeEnabled = false
    @Published var transcriptionBusy = false
    @Published var transcriptionCurrentFile: String?
    @Published var transcriptionProgress: Int = 0
    @Published var transcriptionFileIndex: Int = 0
    @Published var transcriptionFileCount: Int = 0
    @Published var transcriptionStatus: String = ""
    var onCancelTranscription: () -> Void = {}

    // MARK: - Computed
    var visibleEntries: [HiDockSyncRecordingEntry] {
        var entries = syncEntries
        if let filterDeviceId = syncFilterDeviceId {
            entries = entries.filter { $0.deviceId == filterDeviceId }
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

    var needsTaggingCount: Int {
        syncEntries.filter { $0.transcribed && !$0.speakersTagged }.count
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
    var onFilterByDevice: (String?) -> Void = { _ in }
    var onToggleChecked: (String) -> Void = { _ in }
    var onToggleHideDownloaded: () -> Void = {}
    var onToggleAutoDownload: () -> Void = {}
    var onTranscribeSelected: () -> Void = {}
    var onTranscribeAll: () -> Void = {}
    var onToggleDiarize: () -> Void = {}
    var onRevealRecording: (String) -> Void = { _ in }
    var onRevealTranscript: (String) -> Void = { _ in }
    var onOpenTranscriptViewer: (String) -> Void = { _ in }
    var onSendFeedback: () -> Void = {}
    var onShowFeedbackHistory: () -> Void = {}
    var onShowCoworkPrompt: () -> Void = {}
    var onOpenInObsidian: (String) -> Void = { _ in }
    var onMergeSelected: () -> Void = {}
    var onTrimRecording: (String) -> Void = { _ in }
    var onCheckForUpdates: () -> Void = {}
    var onShowVoiceLibrary: () -> Void = {}

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
