import AppKit
import SwiftUI
import CoreAudio
import UniformTypeIdentifiers
import UserNotifications

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, NSMenuDelegate, UNUserNotificationCenterDelegate {
    private var statusItem: NSStatusItem!
    private let menu = NSMenu()
    private var startItem: NSMenuItem!
    private var stopItem: NSMenuItem!
    private var autoStartItem: NSMenuItem!
    private var syncWindowItem: NSMenuItem!
    private var micMenuItem: NSMenuItem!
    private var micSubmenu: NSMenu!
    private var process: Process?

    private var syncWindow: NSWindow?
    private var transcriptViewerWindow: NSWindow?
    private var voiceLibraryWindow: NSWindow?
    private var voiceTrainingWindow: NSWindow?
    private var modelManagerWindow: NSWindow?
    private var coworkPromptWindow: NSWindow?
    private var deviceManagerWindow: NSWindow?
    private var terminalWindow: NSWindow?
    private weak var speakerLabelsMenuItem: NSMenuItem?
    private var importedRecordings: [ImportedRecordingEntry] = []
    /// Filenames the user has opted out of transcribing. Persisted to
    /// ~/HiDock/skipped_transcriptions.json; loaded at launch.
    private var skippedTranscriptions: Set<String> = []
    let viewModel = HiDockViewModel()

    private var syncOutputFolder: String?
    private var syncTranscriptFolder: String?
    private var syncEntries: [HiDockSyncRecordingEntry] = []
    private var syncCheckedRecordings: Set<String> = []
    private var syncHideDownloaded = false
    private var syncAutoDownload = false
    private var syncAutoTranscribe = false
    private let syncAutoTranscribeKey = "hidockSyncAutoTranscribe"
    private var mergeGroups: [MergeGroup] = []
    private let mergeGroupsPath = "\(NSHomeDirectory())/HiDock/merge_groups.json"
    private var diarizeEnabled = false
    private var syncSortKey: String = "created"
    private var syncSortAscending: Bool = false
    private var syncFilterDeviceId: String? = nil
    private var syncDeviceConnected: [String: Bool] = [:]
    private var syncDeviceStorage: [String: HiDockStorageStats] = [:]
    private var syncDeviceLastError: [String: (String, Date)] = [:]
    private var syncDeviceLastOK: [String: Date] = [:]
    /// Devices whose last probe timed out with a full 30s hung kill.
    /// Until they're manually reconnected (via the ↻ button) or the
    /// backoff expires, auto-refresh/auto-connect paths skip them so
    /// the user isn't waiting 30s+ per reopen on a known-stalled H1.
    private var syncDeviceHungUntil: [String: Date] = [:]
    private let hungBackoffInterval: TimeInterval = 180
    private var syncBusy = false
    private var syncRefreshStartDate: Date?
    private var syncRefreshTimer: Timer?
    private var syncAutoDownloadTimer: Timer?
    private var syncExtractorProcess: Process?
    private let syncExtractorQueue = DispatchQueue(label: "hidock.extractor", qos: .userInitiated)
    private var syncDownloadStartDate: Date?
    private var syncDownloadTimer: Timer?
    private var syncDownloadStopping = false
    private var syncDownloading = false

    // Transcription
    private let transcriptionDispatchQueue = DispatchQueue(label: "hidock.transcription", qos: .background)
    private var transcriptionBusy = false
    private var transcriptionCancelled = false
    private var transcriptionPaused = false
    private var transcriptionCurrentFile: String? = nil
    private var transcriptionProgress: Int = 0
    private var transcriptionFileIndex: Int = 0
    private var transcriptionFileCount: Int = 0
    private var pendingTranscriptionQueue: [TranscriptionQueueItem] = []
    private var transcriptionQueueWindow: NSWindow?
    private var transcriptionProgressTimer: Timer?
    private var transcriptionStartTime: Date?
    private var transcriptionEstimatedDuration: TimeInterval = 180  // 3 min default
    private var transcriptionLastRealProgress: Int = 0

    private var processStartDate: Date?
    private var uptimeTimer: Timer?

    // Auto-restart tracking
    private var stoppingIntentionally = false
    private var crashCount = 0
    private let maxCrashRetries = 3
    private let crashRetryDelay: TimeInterval = 3

    private let logPath = "\(NSHomeDirectory())/Library/Logs/hidock-menubar.log"
    private let repoRootKey = "hidockRepoRoot"
    private let syncPairedKey = "hidockSyncPaired"
    private let syncPairedDevicesKey = "hidockSyncPairedDevices"
    private let syncOutputFolderKey = "hidockSyncOutputFolder"
    private let syncTranscriptFolderKey = "hidockSyncTranscriptFolder"
    private let syncHideDownloadedKey = "hidockSyncHideDownloaded"
    private let syncAutoDownloadKey = "hidockSyncAutoDownload"
    private let hasCompletedOnboardingKey = "hasCompletedOnboarding"
    private let notifyTranscriptionKey = "notifyTranscriptionComplete"
    private let notifyDownloadKey = "notifyDownloadComplete"
    private let notifyMicChangesKey = "notifyMicChanges"

    /// Repo root resolved from UserDefaults, falling back to the default home directory path.
    private var repoRoot: String {
        if let saved = UserDefaults.standard.string(forKey: repoRootKey), !saved.isEmpty {
            return saved
        }
        return "\(NSHomeDirectory())/_git/hidock-tools"
    }

    /// When running from a bundled .app, resources are inside the app bundle.
    private var bundledResourcesRoot: String? {
        guard let resPath = Bundle.main.resourcePath else { return nil }
        let bundledExtractor = "\(resPath)/usb-extractor/extractor.py"
        if FileManager.default.fileExists(atPath: bundledExtractor) {
            return resPath
        }
        return nil
    }

    private var extractorRoot: String {
        if let root = bundledResourcesRoot { return "\(root)/usb-extractor" }
        return "\(repoRoot)/usb-extractor"
    }

    private var extractorScriptPath: String {
        "\(extractorRoot)/extractor.py"
    }

    private var extractorPythonPath: String {
        "\(extractorRoot)/.venv/bin/python3"
    }

    private var transcriptionRoot: String {
        if let root = bundledResourcesRoot { return "\(root)/transcription-pipeline" }
        return "\(repoRoot)/transcription-pipeline"
    }

    private var transcriptionPythonPath: String { "\(transcriptionRoot)/.venv/bin/python3" }

    private var transcriptionScriptPath: String {
        // Use whisper.cpp version in bundle, openai-whisper in dev
        if bundledResourcesRoot != nil {
            return "\(transcriptionRoot)/transcribe_cpp.py"
        }
        return "\(transcriptionRoot)/transcribe.py"
    }

    private lazy var binaryPath: String = {
        if let override = ProcessInfo.processInfo.environment["HIDOCK_MIC_TRIGGER_PATH"], !override.isEmpty {
            return override
        }
        if let bundled = Bundle.main.path(forResource: "hidock-mic-trigger", ofType: nil) {
            return bundled
        }
        return "\(repoRoot)/mic-trigger/hidock-mic-trigger"
    }()

    private lazy var sourcePath: String = {
        return "\(repoRoot)/mic-trigger/MicTrigger.swift"
    }()

    private let autoStartKey = "autoStartOnLaunch"
    private var autoStartOnLaunch: Bool {
        get {
            if UserDefaults.standard.object(forKey: autoStartKey) == nil {
                return true
            }
            return UserDefaults.standard.bool(forKey: autoStartKey)
        }
        set {
            UserDefaults.standard.set(newValue, forKey: autoStartKey)
        }
    }

    private let selectedMicKey = "selectedMicName"
    private var selectedMicName: String? {
        get { UserDefaults.standard.string(forKey: selectedMicKey) }
        set { UserDefaults.standard.set(newValue, forKey: selectedMicKey) }
    }

    private let preferredMicKey = "preferredMicName"
    private var preferredMicName: String? {
        get { UserDefaults.standard.string(forKey: preferredMicKey) }
        set { UserDefaults.standard.set(newValue, forKey: preferredMicKey) }
    }

    private let fallbackMicKey = "fallbackMicName"
    private var fallbackMicName: String? {
        get { UserDefaults.standard.string(forKey: fallbackMicKey) }
        set { UserDefaults.standard.set(newValue, forKey: fallbackMicKey) }
    }

    private var previousDeviceNames: Set<String> = []
    private var deviceChangeDebounceTimer: Timer?
    private var syncPaired: Bool {
        !syncPairedDevices.isEmpty
    }

    private var syncPairedDevices: [HiDockPairedDevice] {
        get {
            guard let data = UserDefaults.standard.data(forKey: syncPairedDevicesKey) else { return [] }
            let all = (try? JSONDecoder().decode([HiDockPairedDevice].self, from: data)) ?? []
            // Drop USB-enumeration stubs that may have been auto-paired in
            // older builds. Matches the guard in autoConnectSyncIfPaired so
            // we don't re-surface phantoms on every relaunch.
            return all.filter { d in
                let lc = d.displayName.lowercased()
                if d.deviceType == .hidock {
                    if lc.contains("stub") { return false }
                    if d.productId == 0 || d.productId == 0xFF00 { return false }
                }
                return true
            }
        }
        set {
            let data = try? JSONEncoder().encode(newValue)
            UserDefaults.standard.set(data, forKey: syncPairedDevicesKey)
        }
    }

    // MARK: - App lifecycle

    func applicationDidFinishLaunching(_ notification: Notification) {
        log("applicationDidFinishLaunching")
        NSApp.setActivationPolicy(.accessory)
        setupNotifications()
        applyPreferredMicOnStartup()
        setupMainMenu()
        setupStatusItem()
        wireViewModel()
        registerDeviceChangeListener()
        previousDeviceNames = Set(getInputDeviceNames())
        // Skipped: loadCachedRecordings() used to fire a short-timeout
        // extractor probe against every paired HiDock for "instant
        // display from cache", but the extractor still does a USB open
        // + dev.reset() even on the cached path. With two HiDocks that
        // meant four USB resets stacked back-to-back at launch — and
        // the race with the refreshSyncStatus that showSyncWindow runs
        // moments later was flipping H1 to "held by Python (pid of our
        // own just-exited probe)". The refreshSyncStatus call populates
        // the UI within a couple of seconds anyway.
        loadMergeGroups()
        importedRecordings = ImportedRecordingsStore.load()
        log("Loaded \(importedRecordings.count) imported recording(s) from \(ImportedRecordingsStore.path)")
        // Initial recording-state probe — the trigger child may already have
        // been running (if the app was relaunched mid-session), in which case
        // we need to pick up 'recording' state without waiting for the next
        // mic in/out transition.
        viewModel.hidockRecordingActive = Self.probeFFmpegHoldingHiDock()
        skippedTranscriptions = SkippedTranscriptionsStore.load()
        log("Loaded \(skippedTranscriptions.count) skipped-transcription filename(s)")
        // Backfill duration for any entries imported before duration probing
        // was wired up (duration saved as 0).
        var needsSave = false
        for i in importedRecordings.indices {
            if importedRecordings[i].duration <= 0,
               FileManager.default.fileExists(atPath: importedRecordings[i].outputPath) {
                let d = ImportedRecordingsStore.probeDuration(at: importedRecordings[i].outputPath)
                if d > 0 {
                    let e = importedRecordings[i]
                    importedRecordings[i] = ImportedRecordingEntry(
                        name: e.name, outputPath: e.outputPath, originalPath: e.originalPath,
                        length: e.length, duration: d,
                        createdAt: e.createdAt, importedAt: e.importedAt
                    )
                    needsSave = true
                    log("Backfilled duration for \(e.name): \(Int(d))s")
                }
            }
        }
        if needsSave { ImportedRecordingsStore.save(importedRecordings) }
        rebuildSyncEntries()
        let imp = syncEntries.filter { $0.deviceId == IMPORTED_DEVICE_ID }
        log("After rebuildSyncEntries: syncEntries=\(syncEntries.count) imported=\(imp.count)")
        if let first = imp.first {
            log("First imported entry: name=\(first.recording.name), deviceId=\(first.deviceId), downloaded=\(first.recording.downloaded), localExists=\(first.recording.localExists), outputPath=\(first.recording.outputPath)")
        }
        let vis = viewModel.visibleEntries.filter { $0.deviceId == IMPORTED_DEVICE_ID }
        log("viewModel.visibleEntries imported count = \(vis.count) (filter: hideDownloaded=\(viewModel.syncHideDownloaded), deviceFilter=\(viewModel.syncFilterDeviceId ?? "nil"))")
        showSyncWindow()

        // Show onboarding wizard on first run
        if !UserDefaults.standard.bool(forKey: hasCompletedOnboardingKey) {
            viewModel.showOnboarding = true
        }

        // Apply saved appearance mode
        applyAppearanceMode()

        // Order matters: probe devices FIRST (while nothing is holding
        // the HiDock USB interface), THEN start the mic trigger. The
        // old order (startTrigger then autoConnect) created a narrow
        // window where ffmpeg was grabbing the interface right as the
        // first status probe dispatched — the probe either timed out or
        // returned "held by", and in the worst case the dev.reset()
        // inside the extractor's prepare_device could wedge the HiDock
        // firmware. Probing first is free because the trigger hasn't
        // attached yet. startTrigger fires from the autoConnect
        // completion.
        autoConnectSyncIfPaired(startTriggerOnCompletion: true)

        // Wire update status to the footer bar
        UpdateChecker.onStatusUpdate = { [weak self] (text: String) in
            self?.viewModel.updateStatusText = text
        }

        // Check for updates after a short delay — show in-app dialog
        UpdateChecker.registerCategory()
        DispatchQueue.main.asyncAfter(deadline: .now() + 5) {
            UpdateChecker.checkForUpdate { title, body, release in
                UpdateChecker.showUpdateAlert(title: title, body: body, release: release)
            }
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return false
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showSyncWindow()
        return true
    }

    func applicationWillTerminate(_ notification: Notification) {
        removeDeviceChangeListener()
        stoppingIntentionally = true
        stopTrigger()
        UpdateChecker.installPendingUpdateIfNeeded()
    }

    // MARK: - NSWindowDelegate

    func windowWillClose(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
    }

    // MARK: - NSMenuDelegate

    func menuNeedsUpdate(_ menu: NSMenu) {
        if menu == micSubmenu {
            rebuildMicSubmenu()
        }
    }

    // MARK: - UNUserNotificationCenterDelegate

    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        let userInfo = response.notification.request.content.userInfo
        let categoryId = response.notification.request.content.categoryIdentifier

        if response.actionIdentifier == Self.micSwitchActionID {
            DispatchQueue.main.async { [weak self] in
                self?.showSyncWindow()
            }
        } else if response.actionIdentifier == Self.openTranscriptActionID {
            if let path = userInfo["transcriptPath"] as? String {
                DispatchQueue.main.async { [weak self] in
                    self?.openTranscriptViewer(transcriptMdPath: path)
                }
            }
        } else if response.actionIdentifier == Self.revealTranscriptActionID {
            if let path = userInfo["transcriptPath"] as? String {
                NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
            }
        } else if categoryId == Self.transcriptionCategoryID && response.actionIdentifier == UNNotificationDefaultActionIdentifier {
            // Clicking the notification body opens the transcript
            if let path = userInfo["transcriptPath"] as? String {
                DispatchQueue.main.async { [weak self] in
                    self?.openTranscriptViewer(transcriptMdPath: path)
                }
            }
        } else if response.actionIdentifier == UpdateChecker.updateActionID ||
                    categoryId == UpdateChecker.updateCategoryID {
            if let urlString = UserDefaults.standard.string(forKey: UpdateChecker.updateURLKey),
               let url = URL(string: urlString) {
                NSWorkspace.shared.open(url)
            }
        }
        completionHandler()
    }

    // MARK: - Wire ViewModel

    private func wireViewModel() {
        viewModel.autoStartOnLaunch = autoStartOnLaunch
        viewModel.selectedMicName = selectedMicName
        viewModel.availableMics = getInputDeviceNames()
        viewModel.syncPairedDevices = syncPairedDevices
        viewModel.syncPaired = syncPaired
        viewModel.syncHideDownloaded = UserDefaults.standard.bool(forKey: syncHideDownloadedKey)
        syncHideDownloaded = viewModel.syncHideDownloaded
        viewModel.syncAutoDownload = UserDefaults.standard.bool(forKey: syncAutoDownloadKey)
        syncAutoDownload = viewModel.syncAutoDownload
        viewModel.syncAutoTranscribe = UserDefaults.standard.bool(forKey: syncAutoTranscribeKey)
        syncAutoTranscribe = viewModel.syncAutoTranscribe
        // Default diarization to ON — speaker labels are almost always wanted
        if UserDefaults.standard.object(forKey: "diarizeEnabled") == nil {
            UserDefaults.standard.set(true, forKey: "diarizeEnabled")
        }
        diarizeEnabled = UserDefaults.standard.bool(forKey: "diarizeEnabled")
        viewModel.diarizeEnabled = diarizeEnabled

        if let savedFolder = UserDefaults.standard.string(forKey: syncOutputFolderKey), !savedFolder.isEmpty {
            syncOutputFolder = savedFolder
            viewModel.syncOutputFolder = savedFolder
        }
        if let savedTranscriptFolder = UserDefaults.standard.string(forKey: syncTranscriptFolderKey), !savedTranscriptFolder.isEmpty {
            syncTranscriptFolder = savedTranscriptFolder
            viewModel.syncTranscriptFolder = savedTranscriptFolder
        } else {
            syncTranscriptFolder = "\(NSHomeDirectory())/HiDock/Raw Transcripts"
            viewModel.syncTranscriptFolder = syncTranscriptFolder
        }

        viewModel.onStartTrigger = { [weak self] in self?.startTrigger() }
        viewModel.onStopTrigger = { [weak self] in self?.stopTrigger() }
        viewModel.onToggleAutoStart = { [weak self] in self?.toggleAutoStart() }
        viewModel.onSelectMic = { [weak self] mic in self?.selectMic(mic) }
        viewModel.onRefreshSync = { [weak self] in self?.refreshSyncStatus() }
        viewModel.onImportAudioFile = { [weak self] in self?.importAudioFile() }
        viewModel.onRemoveImport = { [weak self] name in self?.removeImportedRecording(name: name) }
        viewModel.onTranscribeWithSpeakerCount = { [weak self] name, n in self?.transcribeWithSpeakerCount(name: name, nSpeakers: n) }
        viewModel.onDeleteLocalCopy = { [weak self] name in self?.deleteLocalCopy(name: name) }
        viewModel.onRemoveSelected = { [weak self] in self?.removeSelected() }
        viewModel.onReconnectDevice = { [weak self] deviceId in self?.reconnectDevice(deviceId: deviceId) }
        viewModel.onPairDock = { [weak self] in self?.pairSyncDock() }
        viewModel.onUnpairDock = { [weak self] in self?.unpairSyncDock() }
        viewModel.onChooseRecordingsFolder = { [weak self] in self?.chooseSyncOutputFolder() }
        viewModel.onChooseTranscriptFolder = { [weak self] in self?.chooseTranscriptOutputFolder() }
        viewModel.onDownloadSelected = { [weak self] in self?.downloadSelectedSyncRecording() }
        viewModel.onDownloadNew = { [weak self] in self?.downloadNewSyncRecordings() }
        viewModel.onStopDownload = { [weak self] in self?.stopSyncDownload() }
        viewModel.onMarkDownloaded = { [weak self] in self?.markSyncRecordingsAsDownloaded() }
        viewModel.onSelectAll = { [weak self] in self?.selectAllSyncRecordings() }
        viewModel.onSelectNone = { [weak self] in self?.selectNoneSyncRecordings() }
        viewModel.onSelectNotDownloaded = { [weak self] in self?.selectNotDownloadedSyncRecordings() }
        viewModel.onFilterByDevice = { [weak self] deviceId in self?.filterSyncByDevice(deviceId) }
        viewModel.onToggleChecked = { [weak self] name, shift in self?.toggleSyncRecordingCheckbox(name, shiftHeld: shift) }
        viewModel.onUnmarkDownloaded = { [weak self] in self?.unmarkSyncRecordingsAsDownloaded() }
        viewModel.onToggleHideDownloaded = { [weak self] in self?.toggleHideDownloaded() }
        viewModel.onToggleAutoDownload = { [weak self] in self?.toggleAutoDownload() }
        viewModel.onToggleAutoTranscribe = { [weak self] in self?.toggleAutoTranscribe() }
        viewModel.onToggleMergeExpand = { [weak self] id in self?.toggleMergeExpand(id) }
        viewModel.onTranscribeSelected = { [weak self] in self?.transcribeSelectedRecordings() }
        viewModel.onTranscribeAll = { [weak self] in self?.transcribeAllRecordings() }
        viewModel.onToggleDiarize = { [weak self] in self?.toggleDiarize() }
        viewModel.onRevealRecording = { path in
            NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
        }
        viewModel.onRevealTranscript = { [weak self] path in
            self?.openTranscriptViewer(transcriptMdPath: path)
        }
        viewModel.onExportSRT = { [weak self] path in
            self?.exportSRT(transcriptMdPath: path)
        }
        viewModel.onOpenTranscriptViewer = { [weak self] path in
            self?.openTranscriptViewer(transcriptMdPath: path)
        }
        viewModel.onShowCoworkPrompt = { [weak self] in self?.showCoworkPrompt() }
        viewModel.onMergeSelected = { [weak self] in self?.mergeSelectedRecordings() }
        viewModel.onTrimRecording = { [weak self] path in self?.showTrimDialog(for: path) }
        viewModel.onShowTranscriptionQueue = { [weak self] in self?.showTranscriptionQueueWindow() }
        viewModel.onRemoveFromQueue = { [weak self] path in self?.removeFromTranscriptionQueue(path) }
        viewModel.onMoveInQueue = { [weak self] from, to in self?.moveInTranscriptionQueue(from: from, to: to) }
        viewModel.onPauseTranscription = { [weak self] in self?.pauseTranscription() }
        viewModel.onResumeTranscription = { [weak self] in self?.resumeTranscription() }
        viewModel.onOpenInObsidian = { path in
            let url = URL(fileURLWithPath: path)
            let noteName = url.deletingPathExtension().lastPathComponent
            let task = Process()
            task.executableURL = URL(fileURLWithPath: "/usr/bin/env")
            task.arguments = ["obsidian", "open", noteName]
            try? task.run()
        }
        viewModel.onShowVoiceLibrary = { [weak self] in self?.openVoiceLibrary() }
        viewModel.onShowVoiceTraining = { [weak self] in self?.showVoiceTraining() }
        viewModel.onCancelTranscription = { [weak self] in self?.cancelTranscription() }
        viewModel.onShowModelManager = { [weak self] in self?.openModelManager() }
        viewModel.onShowDeviceManager = { [weak self] in self?.openDeviceManager() }
        viewModel.onForgetDevice = { [weak self] device in self?.forgetDevice(device) }
        viewModel.onPairVolume = { [weak self] volumeName, subpath in self?.pairVolume(volumeName: volumeName, subpath: subpath) }
        viewModel.onScanVolumes = { [weak self] completion in self?.scanVolumes(completion: completion) }
        viewModel.onRefreshModelStatuses = { [weak self] in self?.refreshModelStatuses() }
        viewModel.onDownloadModelByKey = { [weak self] key in self?.downloadModelByKey(key) }
        viewModel.onDeleteModelByKey = { [weak self] key in self?.deleteModelByKey(key) }
        viewModel.onSendFeedback = { [weak self] in self?.sendFeedback() }
        viewModel.onShowFeedbackHistory = { [weak self] in self?.showFeedbackHistory() }
        viewModel.onCheckForUpdates = { UpdateChecker.manualCheck() }

        // Notification preferences (default to true if not set)
        viewModel.notifyTranscriptionComplete = UserDefaults.standard.object(forKey: notifyTranscriptionKey) == nil || UserDefaults.standard.bool(forKey: notifyTranscriptionKey)
        viewModel.notifyDownloadComplete = UserDefaults.standard.object(forKey: notifyDownloadKey) == nil || UserDefaults.standard.bool(forKey: notifyDownloadKey)
        viewModel.notifyMicChanges = UserDefaults.standard.object(forKey: notifyMicChangesKey) == nil || UserDefaults.standard.bool(forKey: notifyMicChangesKey)
        viewModel.onToggleNotifyTranscription = { [weak self] in
            guard let self = self else { return }
            let newVal = !self.viewModel.notifyTranscriptionComplete
            UserDefaults.standard.set(newVal, forKey: self.notifyTranscriptionKey)
            self.viewModel.notifyTranscriptionComplete = newVal
        }
        viewModel.onToggleNotifyDownload = { [weak self] in
            guard let self = self else { return }
            let newVal = !self.viewModel.notifyDownloadComplete
            UserDefaults.standard.set(newVal, forKey: self.notifyDownloadKey)
            self.viewModel.notifyDownloadComplete = newVal
        }
        viewModel.onToggleNotifyMicChanges = { [weak self] in
            guard let self = self else { return }
            let newVal = !self.viewModel.notifyMicChanges
            UserDefaults.standard.set(newVal, forKey: self.notifyMicChangesKey)
            self.viewModel.notifyMicChanges = newVal
        }

        // Appearance
        let currentMode = UserDefaults.standard.string(forKey: "appearanceMode") ?? "auto"
        viewModel.appearanceMode = currentMode
        viewModel.onSetAppearance = { [weak self] mode in
            guard let self = self else { return }
            UserDefaults.standard.set(mode, forKey: "appearanceMode")
            self.viewModel.appearanceMode = mode
            self.applyAppearanceMode()
        }

        // Onboarding
        viewModel.modelReady = FileManager.default.fileExists(atPath: whisperModelPath)

        viewModel.onCompleteOnboarding = { [weak self] in
            guard let self = self else { return }
            UserDefaults.standard.set(true, forKey: self.hasCompletedOnboardingKey)
            self.viewModel.showOnboarding = false
        }

        viewModel.onDownloadModel = { [weak self] in
            self?.downloadWhisperModel()
        }
        viewModel.onCancelModelDownload = { [weak self] in
            self?.cancelModelDownload()
        }
    }

    /// Push all mutable state to the ViewModel so SwiftUI reflects it.
    private func syncViewModelState() {
        let running = process != nil
        viewModel.triggerRunning = running
        viewModel.triggerPID = process?.processIdentifier
        viewModel.triggerUptime = formatUptime() ?? ""
        viewModel.autoStartOnLaunch = autoStartOnLaunch
        viewModel.selectedMicName = selectedMicName
        viewModel.availableMics = getInputDeviceNames()
        viewModel.syncBusy = syncBusy
        viewModel.syncDownloading = syncDownloading
        viewModel.syncEntries = syncEntries
        viewModel.syncCheckedRecordings = syncCheckedRecordings
        viewModel.syncHideDownloaded = syncHideDownloaded
        viewModel.syncAutoDownload = syncAutoDownload
        viewModel.syncAutoTranscribe = syncAutoTranscribe
        viewModel.diarizeEnabled = diarizeEnabled
        viewModel.syncFilterDeviceId = syncFilterDeviceId
        viewModel.syncPairedDevices = syncPairedDevices
        viewModel.syncPaired = syncPaired
        viewModel.syncDeviceConnected = syncDeviceConnected
        viewModel.syncDeviceStorage = syncDeviceStorage
        viewModel.syncDeviceLastError = syncDeviceLastError
        viewModel.syncDeviceLastOK = syncDeviceLastOK
        viewModel.syncOutputFolder = syncOutputFolder
        viewModel.syncTranscriptFolder = syncTranscriptFolder
        viewModel.transcriptionBusy = transcriptionBusy
        viewModel.transcriptionCurrentFile = transcriptionCurrentFile
        viewModel.transcriptionProgress = transcriptionProgress
        viewModel.transcriptionPaused = transcriptionPaused
        viewModel.transcriptionQueue = pendingTranscriptionQueue
        viewModel.transcriptionFileIndex = transcriptionFileIndex
        viewModel.transcriptionFileCount = transcriptionFileCount
    }

    // MARK: - Notifications

    private func setupNotifications() {
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        center.requestAuthorization(options: [.alert, .sound]) { granted, error in
            if let error = error {
                self.log("Notification auth error: \(error)")
            }
            self.log("Notification auth granted: \(granted)")
        }
        registerNotificationCategories()
    }

    private func postNotification(title: String, body: String) {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default
        let request = UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request)
    }

    private func postSyncDownloadNotification(title: String, body: String) {
        guard UserDefaults.standard.object(forKey: notifyDownloadKey) == nil || UserDefaults.standard.bool(forKey: notifyDownloadKey) else { return }
        postNotification(title: title, body: body)
    }

    private func postTranscriptionNotification(title: String, body: String, transcriptPath: String? = nil) {
        guard UserDefaults.standard.object(forKey: notifyTranscriptionKey) == nil || UserDefaults.standard.bool(forKey: notifyTranscriptionKey) else { return }
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default
        content.categoryIdentifier = Self.transcriptionCategoryID
        if let path = transcriptPath {
            content.userInfo["transcriptPath"] = path
        }
        let request = UNNotificationRequest(identifier: "transcription-\(UUID().uuidString)", content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request)
    }

    // MARK: - CLI output monitoring

    private func handleCLIOutput(_ text: String) {
        for line in text.components(separatedBy: .newlines) {
            if line.contains("IN USE") && line.contains("holding HiDock") {
                log("Trigger: USB mic in use, HiDock recording started")
                postNotification(title: "HiDock Recording Started", body: "USB mic is in use — HiDock input held open.")
                DispatchQueue.main.async {
                    self.viewModel.hidockRecordingActive = true
                }
            } else if line.contains("NOT IN USE") && line.contains("releasing HiDock") {
                log("Trigger: USB mic idle, HiDock recording stopped")
                postNotification(title: "HiDock Recording Stopped", body: "USB mic went idle — HiDock input released.")
                DispatchQueue.main.async {
                    self.viewModel.hidockRecordingActive = false
                }
                DispatchQueue.main.asyncAfter(deadline: .now() + 8.0) { [weak self] in
                    guard let self = self, self.syncPaired, !self.syncBusy else { return }
                    self.log("Auto-refreshing sync after mic release")
                    self.refreshSyncStatus()
                }
                scheduleAutoDownloadNewRecordings()
            }
        }
    }

    // MARK: - CoreAudio device enumeration

    private func getInputDeviceNames() -> [String] {
        var propsize: UInt32 = 0
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDevices,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var status = AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &propsize)
        guard status == noErr else { return [] }

        let count = Int(propsize) / MemoryLayout<AudioDeviceID>.size
        var deviceIDs = Array(repeating: AudioDeviceID(0), count: count)
        status = AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &propsize, &deviceIDs)
        guard status == noErr else { return [] }

        var names: [String] = []
        for deviceID in deviceIDs {
            var streamAddress = AudioObjectPropertyAddress(
                mSelector: kAudioDevicePropertyStreamConfiguration,
                mScope: kAudioDevicePropertyScopeInput,
                mElement: kAudioObjectPropertyElementMain
            )
            var streamSize: UInt32 = 0
            status = AudioObjectGetPropertyDataSize(deviceID, &streamAddress, 0, nil, &streamSize)
            guard status == noErr else { continue }

            let bufferListPtr = UnsafeMutablePointer<AudioBufferList>.allocate(capacity: Int(streamSize))
            defer { bufferListPtr.deallocate() }
            status = AudioObjectGetPropertyData(deviceID, &streamAddress, 0, nil, &streamSize, bufferListPtr)
            guard status == noErr else { continue }

            let bufferList = UnsafeMutableAudioBufferListPointer(bufferListPtr)
            let channels = bufferList.reduce(0) { $0 + Int($1.mNumberChannels) }
            guard channels > 0 else { continue }

            var nameAddress = AudioObjectPropertyAddress(
                mSelector: kAudioObjectPropertyName,
                mScope: kAudioObjectPropertyScopeGlobal,
                mElement: kAudioObjectPropertyElementMain
            )
            var cfName: CFString = "" as CFString
            var nameSize = UInt32(MemoryLayout<CFString>.size)
            status = AudioObjectGetPropertyData(deviceID, &nameAddress, 0, nil, &nameSize, &cfName)
            guard status == noErr else { continue }

            names.append(cfName as String)
        }
        return names
    }

    // MARK: - Preferred mic startup

    private func applyPreferredMicOnStartup() {
        guard let preferred = preferredMicName, !preferred.isEmpty else { return }
        let devices = getInputDeviceNames()
        if devices.contains(preferred) {
            log("Preferred mic '\(preferred)' found on startup, selecting it")
            selectedMicName = preferred
        } else {
            log("Preferred mic '\(preferred)' not connected on startup, using fallback")
        }
    }

    // MARK: - CoreAudio device change listener

    private func registerDeviceChangeListener() {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDevices,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        let status = AudioObjectAddPropertyListenerBlock(
            AudioObjectID(kAudioObjectSystemObject),
            &address,
            DispatchQueue.main,
            deviceChangeListenerBlock
        )
        if status != noErr {
            log("Failed to register device change listener: \(status)")
        } else {
            log("Registered CoreAudio device change listener")
        }
    }

    private func removeDeviceChangeListener() {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDevices,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        AudioObjectRemovePropertyListenerBlock(
            AudioObjectID(kAudioObjectSystemObject),
            &address,
            DispatchQueue.main,
            deviceChangeListenerBlock
        )
    }

    private lazy var deviceChangeListenerBlock: AudioObjectPropertyListenerBlock = { [weak self] _, _ in
        self?.scheduleDeviceChangeHandler()
    }

    private func scheduleDeviceChangeHandler() {
        deviceChangeDebounceTimer?.invalidate()
        deviceChangeDebounceTimer = Timer.scheduledTimer(withTimeInterval: 1.5, repeats: false) { [weak self] _ in
            self?.handleDeviceListChanged()
        }
    }

    private func handleDeviceListChanged() {
        let currentDevices = Set(getInputDeviceNames())
        let oldDevices = previousDeviceNames
        previousDeviceNames = currentDevices

        let appeared = currentDevices.subtracting(oldDevices)
        let disappeared = oldDevices.subtracting(currentDevices)

        if !appeared.isEmpty { log("Devices appeared: \(appeared)") }
        if !disappeared.isEmpty { log("Devices disappeared: \(disappeared)") }

        if !appeared.isEmpty || !disappeared.isEmpty {
            viewModel.availableMics = getInputDeviceNames()
            if syncPaired && !syncBusy {
                log("USB device change detected, refreshing sync status")
                autoConnectSyncIfPaired()
            }
        }

        let preferred = preferredMicName
        let selected = selectedMicName

        if let preferred = preferred, !preferred.isEmpty,
           appeared.contains(preferred), selected != preferred {
            log("Preferred mic '\(preferred)' just connected, auto-switching")
            let oldMic = selectedMicName
            selectedMicName = preferred
            viewModel.selectedMicName = preferred
            viewModel.availableMics = getInputDeviceNames()
            updateMenuState()
            if process != nil && preferred != oldMic {
                restartTrigger()
            }
            postMicChangeNotification(
                title: "Switched to \(shortenMicName(preferred))",
                body: "Your preferred mic is now connected.",
                micName: preferred
            )
            return
        }

        if let selected = selected, !selected.isEmpty,
           disappeared.contains(selected) {
            log("Current mic '\(selected)' disconnected")
            let fallback = resolveFallbackMic(from: currentDevices)
            if let fallback = fallback {
                selectedMicName = fallback
                viewModel.selectedMicName = fallback
                viewModel.availableMics = getInputDeviceNames()
                updateMenuState()
                if process != nil { restartTrigger() }
                postMicChangeNotification(
                    title: "Mic Disconnected",
                    body: "\(shortenMicName(selected)) was unplugged. Fell back to \(shortenMicName(fallback)).",
                    micName: fallback
                )
            } else {
                selectedMicName = nil
                viewModel.selectedMicName = nil
                updateMenuState()
                if process != nil { restartTrigger() }
                postNotification(title: "Mic Disconnected", body: "\(shortenMicName(selected)) was unplugged. No mics available.")
            }
            return
        }
    }

    private func resolveFallbackMic(from devices: Set<String>) -> String? {
        if let fb = fallbackMicName, !fb.isEmpty, devices.contains(fb) {
            return fb
        }
        if let macbook = devices.first(where: { $0.contains("MacBook") }) {
            return macbook
        }
        return devices.first
    }

    private static let micSwitchCategoryID = "MIC_SWITCH"
    private static let micSwitchActionID = "OPEN_MIC_MENU"
    private static let transcriptionCategoryID = "TRANSCRIPTION_COMPLETE"
    private static let openTranscriptActionID = "OPEN_TRANSCRIPT"
    private static let revealTranscriptActionID = "REVEAL_TRANSCRIPT"

    private func registerNotificationCategories() {
        let openMicAction = UNNotificationAction(identifier: Self.micSwitchActionID, title: "Open Mic Settings", options: .foreground)
        let micCategory = UNNotificationCategory(identifier: Self.micSwitchCategoryID, actions: [openMicAction], intentIdentifiers: [])

        let openTranscriptAction = UNNotificationAction(identifier: Self.openTranscriptActionID, title: "Open Transcript", options: .foreground)
        let revealTranscriptAction = UNNotificationAction(identifier: Self.revealTranscriptActionID, title: "Show in Finder", options: .foreground)
        let transcriptionCategory = UNNotificationCategory(identifier: Self.transcriptionCategoryID, actions: [openTranscriptAction, revealTranscriptAction], intentIdentifiers: [])

        UNUserNotificationCenter.current().setNotificationCategories([micCategory, transcriptionCategory])
    }

    private func postMicChangeNotification(title: String, body: String, micName: String) {
        guard UserDefaults.standard.object(forKey: notifyMicChangesKey) == nil || UserDefaults.standard.bool(forKey: notifyMicChangesKey) else { return }
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default
        content.categoryIdentifier = Self.micSwitchCategoryID
        let request = UNNotificationRequest(identifier: "mic-change-\(UUID().uuidString)", content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request)
    }

    // MARK: - Menu bar

    private func setupStatusItem() {
        log("setupStatusItem")
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.image = statusImage(running: false)
        statusItem.button?.imagePosition = .imageLeft
        #if DEV_BUILD
        var initialTitle = "HiDock DEV"
        #else
        var initialTitle = "HiDock"
        #endif
        // Don't show devices on initial launch — they'll appear once connection is confirmed
        if let mic = selectedMicName, !mic.isEmpty {
            let isFallback = preferredMicName != nil && !preferredMicName!.isEmpty && mic != preferredMicName
            let suffix = isFallback ? " (fallback)" : ""
            initialTitle += " · \(shortenMicName(mic))\(suffix)"
        } else {
            initialTitle += " · No Mic"
        }
        statusItem.button?.title = initialTitle

        startItem = NSMenuItem(title: "Start", action: #selector(startTriggerMenu), keyEquivalent: "s")
        stopItem = NSMenuItem(title: "Stop", action: #selector(stopTriggerMenu), keyEquivalent: "t")
        autoStartItem = NSMenuItem(title: "Auto-start on launch", action: #selector(toggleAutoStartMenu), keyEquivalent: "")
        syncWindowItem = NSMenuItem(title: "Show Window...", action: #selector(showSyncWindow), keyEquivalent: "d")

        micSubmenu = NSMenu()
        micSubmenu.delegate = self
        micMenuItem = NSMenuItem(title: "Trigger Mic", action: nil, keyEquivalent: "")
        micMenuItem.submenu = micSubmenu

        let logsItem = NSMenuItem(title: "Show Logs", action: #selector(showLogs), keyEquivalent: "l")
        let statusInfoItem = NSMenuItem(title: "Show Status", action: #selector(showStatus), keyEquivalent: "i")
        let quitItem = NSMenuItem(title: "Quit", action: #selector(quitApp), keyEquivalent: "q")

        for item in [startItem, stopItem, autoStartItem, syncWindowItem, logsItem, statusInfoItem, quitItem] {
            item?.target = self
        }

        menu.addItem(startItem)
        menu.addItem(stopItem)
        menu.addItem(NSMenuItem.separator())
        menu.addItem(micMenuItem)
        menu.addItem(autoStartItem)
        menu.addItem(NSMenuItem.separator())
        menu.addItem(syncWindowItem)
        menu.addItem(NSMenuItem.separator())
        menu.addItem(logsItem)
        menu.addItem(statusInfoItem)
        menu.addItem(NSMenuItem.separator())
        let voiceLibraryItem = NSMenuItem(title: "Voice Library...", action: #selector(openVoiceLibraryMenu), keyEquivalent: "")
        voiceLibraryItem.target = self
        menu.addItem(voiceLibraryItem)
        let voiceTrainingItem = NSMenuItem(title: "Voice Training...", action: #selector(openVoiceTrainingMenu), keyEquivalent: "")
        voiceTrainingItem.target = self
        menu.addItem(voiceTrainingItem)
        let modelManagerItem = NSMenuItem(title: "Models...", action: #selector(openModelManagerMenu), keyEquivalent: "")
        modelManagerItem.target = self
        menu.addItem(modelManagerItem)
        let terminalItem = NSMenuItem(title: "Terminal...", action: #selector(openTerminalMenu), keyEquivalent: "t")
        terminalItem.keyEquivalentModifierMask = [.command, .shift]
        terminalItem.target = self
        menu.addItem(terminalItem)
        let importItem = NSMenuItem(title: "Import Audio File...", action: #selector(importAudioFileMenu), keyEquivalent: "i")
        importItem.keyEquivalentModifierMask = [.command, .shift]
        importItem.target = self
        menu.addItem(importItem)
        let firmwareItem = NSMenuItem(title: "Check for Firmware Updates...", action: #selector(openFirmwarePage), keyEquivalent: "")
        firmwareItem.target = self
        menu.addItem(firmwareItem)
        let feedbackItem = NSMenuItem(title: "Send Feedback...", action: #selector(sendFeedback), keyEquivalent: "f")
        feedbackItem.target = self
        menu.addItem(feedbackItem)
        let historyItem = NSMenuItem(title: "My Feedback", action: #selector(showFeedbackHistory), keyEquivalent: "")
        historyItem.target = self
        menu.addItem(historyItem)
        let updateItem = NSMenuItem(title: "Check for Updates...", action: #selector(checkForUpdatesManual), keyEquivalent: "")
        updateItem.target = self
        menu.addItem(updateItem)
        menu.addItem(NSMenuItem.separator())
        menu.addItem(quitItem)

        menu.autoenablesItems = false
        statusItem.menu = menu
        updateMenuState()
    }

    private func rebuildMicSubmenu() {
        micSubmenu.removeAllItems()
        let devices = getInputDeviceNames()
        let current = selectedMicName
        let preferred = preferredMicName
        let fallback = fallbackMicName

        for name in devices {
            var badges: [String] = []
            if name == preferred { badges.append("Default") }
            if name == fallback { badges.append("Fallback") }
            let suffix = badges.isEmpty ? "" : "  [\(badges.joined(separator: ", "))]"
            let item = NSMenuItem(title: "\(name)\(suffix)", action: #selector(micMenuItemSelected(_:)), keyEquivalent: "")
            item.representedObject = name
            item.target = self
            item.state = (name == current) ? .on : .off
            micSubmenu.addItem(item)
        }

        if devices.isEmpty {
            let noDevices = NSMenuItem(title: "No input devices found", action: nil, keyEquivalent: "")
            noDevices.isEnabled = false
            micSubmenu.addItem(noDevices)
        }

        guard !devices.isEmpty else { return }

        micSubmenu.addItem(NSMenuItem.separator())

        if let current = current, !current.isEmpty, current != preferred {
            let setDefault = NSMenuItem(title: "Set \"\(shortenMicName(current))\" as Default", action: #selector(setPreferredMic), keyEquivalent: "")
            setDefault.target = self
            micSubmenu.addItem(setDefault)
        }
        if preferred != nil && !(preferred!.isEmpty) {
            let clearDefault = NSMenuItem(title: "Clear Default Mic", action: #selector(clearPreferredMic), keyEquivalent: "")
            clearDefault.target = self
            micSubmenu.addItem(clearDefault)
        }

        if let current = current, !current.isEmpty, current != fallback {
            let setFallback = NSMenuItem(title: "Set \"\(shortenMicName(current))\" as Fallback", action: #selector(setFallbackMic), keyEquivalent: "")
            setFallback.target = self
            micSubmenu.addItem(setFallback)
        }
        if fallback != nil && !(fallback!.isEmpty) {
            let clearFallback = NSMenuItem(title: "Clear Fallback Mic", action: #selector(clearFallbackMic), keyEquivalent: "")
            clearFallback.target = self
            micSubmenu.addItem(clearFallback)
        }

        micSubmenu.addItem(NSMenuItem.separator())
        let defaultInfo = NSMenuItem(
            title: "Default: \(preferred.flatMap { $0.isEmpty ? nil : shortenMicName($0) } ?? "Not set")",
            action: nil, keyEquivalent: ""
        )
        defaultInfo.isEnabled = false
        micSubmenu.addItem(defaultInfo)

        let fallbackInfo = NSMenuItem(
            title: "Fallback: \(fallback.flatMap { $0.isEmpty ? nil : shortenMicName($0) } ?? "MacBook (auto)")",
            action: nil, keyEquivalent: ""
        )
        fallbackInfo.isEnabled = false
        micSubmenu.addItem(fallbackInfo)
    }

    @objc private func micMenuItemSelected(_ sender: NSMenuItem) {
        let micName = (sender.representedObject as? String) ?? sender.title
        selectMic(micName)
    }

    private func selectMic(_ micName: String) {
        let oldMic = selectedMicName
        selectedMicName = micName
        viewModel.selectedMicName = micName
        log("Selected trigger mic: \(micName)")
        updateMenuState()
        if process != nil && micName != oldMic {
            restartTrigger()
        }
    }

    @objc private func setPreferredMic() {
        guard let current = selectedMicName, !current.isEmpty else { return }
        preferredMicName = current
        log("Set preferred mic to '\(current)'")
        updateMenuState()
    }

    @objc private func clearPreferredMic() {
        log("Cleared preferred mic (was '\(preferredMicName ?? "nil")')")
        preferredMicName = nil
        updateMenuState()
    }

    @objc private func setFallbackMic() {
        guard let current = selectedMicName, !current.isEmpty else { return }
        fallbackMicName = current
        log("Set fallback mic to '\(current)'")
    }

    @objc private func clearFallbackMic() {
        log("Cleared fallback mic (was '\(fallbackMicName ?? "nil")')")
        fallbackMicName = nil
    }

    private func setupMainMenu() {
        let mainMenu = NSMenu()
        let appMenuItem = NSMenuItem()
        mainMenu.addItem(appMenuItem)

        let appMenu = NSMenu()
        appMenu.addItem(NSMenuItem(title: "About HiDock Mic Trigger", action: #selector(showAbout), keyEquivalent: ""))
        let checkUpdatesItem = NSMenuItem(title: "Check for Updates...", action: #selector(checkForUpdatesManual), keyEquivalent: "")
        checkUpdatesItem.target = self
        appMenu.addItem(checkUpdatesItem)
        appMenu.addItem(NSMenuItem.separator())

        // Appearance submenu
        let appearanceItem = NSMenuItem(title: "Appearance", action: nil, keyEquivalent: "")
        let appearanceSubmenu = NSMenu(title: "Appearance")
        let currentMode = UserDefaults.standard.string(forKey: "appearanceMode") ?? "auto"

        let darkItem = NSMenuItem(title: "Dark", action: #selector(setAppearanceDark), keyEquivalent: "")
        darkItem.target = self
        darkItem.state = currentMode == "dark" ? .on : .off
        appearanceSubmenu.addItem(darkItem)

        let lightItem = NSMenuItem(title: "Light", action: #selector(setAppearanceLight), keyEquivalent: "")
        lightItem.target = self
        lightItem.state = currentMode == "light" ? .on : .off
        appearanceSubmenu.addItem(lightItem)

        let autoItem = NSMenuItem(title: "Auto (System)", action: #selector(setAppearanceAuto), keyEquivalent: "")
        autoItem.target = self
        autoItem.state = (currentMode != "dark" && currentMode != "light") ? .on : .off
        appearanceSubmenu.addItem(autoItem)

        appearanceItem.submenu = appearanceSubmenu
        appMenu.addItem(appearanceItem)

        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(NSMenuItem(title: "Quit HiDock Mic Trigger", action: #selector(quitApp), keyEquivalent: "q"))
        appMenuItem.submenu = appMenu

        // File menu — hosts the config actions that used to live in the
        // sync-window toolbar (pair/unpair, folder pickers, import,
        // speaker-labels toggle). Keeps those accessible via standard
        // macOS menu conventions without cluttering the table UI.
        let fileMenuItem = NSMenuItem()
        mainMenu.addItem(fileMenuItem)
        let fileMenu = NSMenu(title: "File")

        let importFileItem = NSMenuItem(title: "Import Audio File...", action: #selector(importAudioFileMenu), keyEquivalent: "i")
        importFileItem.keyEquivalentModifierMask = [.command, .shift]
        importFileItem.target = self
        fileMenu.addItem(importFileItem)

        fileMenu.addItem(NSMenuItem.separator())

        let pairItem = NSMenuItem(title: "Pair HiDock...", action: #selector(fileMenuPair), keyEquivalent: "")
        pairItem.target = self
        fileMenu.addItem(pairItem)

        let unpairItem = NSMenuItem(title: "Unpair HiDock", action: #selector(fileMenuUnpair), keyEquivalent: "")
        unpairItem.target = self
        fileMenu.addItem(unpairItem)

        let deviceMgrItem = NSMenuItem(title: "Device Manager...", action: #selector(fileMenuOpenDeviceManager), keyEquivalent: "d")
        deviceMgrItem.keyEquivalentModifierMask = [.command, .shift]
        deviceMgrItem.target = self
        fileMenu.addItem(deviceMgrItem)

        fileMenu.addItem(NSMenuItem.separator())

        let chooseRecItem = NSMenuItem(title: "Choose Recordings Folder...", action: #selector(fileMenuChooseRecordingsFolder), keyEquivalent: "")
        chooseRecItem.target = self
        fileMenu.addItem(chooseRecItem)

        let chooseTxItem = NSMenuItem(title: "Choose Transcripts Folder...", action: #selector(fileMenuChooseTranscriptsFolder), keyEquivalent: "")
        chooseTxItem.target = self
        fileMenu.addItem(chooseTxItem)

        fileMenu.addItem(NSMenuItem.separator())

        let speakerLabelsItem = NSMenuItem(title: "Speaker Labels (Diarize)", action: #selector(fileMenuToggleDiarize), keyEquivalent: "")
        speakerLabelsItem.target = self
        speakerLabelsItem.state = diarizeEnabled ? .on : .off
        fileMenu.addItem(speakerLabelsItem)
        speakerLabelsMenuItem = speakerLabelsItem

        fileMenuItem.submenu = fileMenu

        // Edit menu — required for standard keyboard shortcuts (⌘C, ⌘V, ⌘X, ⌘A, ⌘Z) to work in text views
        let editMenuItem = NSMenuItem()
        mainMenu.addItem(editMenuItem)
        let editMenu = NSMenu(title: "Edit")
        // Undo/Redo are informal protocols on NSResponder; string-based selectors are the standard approach here.
        editMenu.addItem(NSMenuItem(title: "Undo", action: Selector(("undo:")), keyEquivalent: "z"))
        editMenu.addItem(NSMenuItem(title: "Redo", action: Selector(("redo:")), keyEquivalent: "Z"))
        editMenu.addItem(NSMenuItem.separator())
        editMenu.addItem(NSMenuItem(title: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x"))
        editMenu.addItem(NSMenuItem(title: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c"))
        editMenu.addItem(NSMenuItem(title: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v"))
        editMenu.addItem(NSMenuItem(title: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a"))
        editMenuItem.submenu = editMenu

        NSApp.mainMenu = mainMenu
    }

    // MARK: - Appearance

    private func applyAppearanceMode() {
        let mode = UserDefaults.standard.string(forKey: "appearanceMode") ?? "auto"
        switch mode {
        case "dark":
            NSApp.appearance = NSAppearance(named: .darkAqua)
        case "light":
            NSApp.appearance = NSAppearance(named: .aqua)
        default:
            NSApp.appearance = nil  // follow system
        }
    }

    private func setAppearanceAndRebuildMenu(_ mode: String) {
        UserDefaults.standard.set(mode, forKey: "appearanceMode")
        applyAppearanceMode()
        // Rebuild menu so the checkmarks update
        setupMainMenu()
    }

    @objc private func setAppearanceDark() {
        setAppearanceAndRebuildMenu("dark")
    }

    @objc private func setAppearanceLight() {
        setAppearanceAndRebuildMenu("light")
    }

    @objc private func setAppearanceAuto() {
        setAppearanceAndRebuildMenu("auto")
    }

    private func statusImage(running: Bool) -> NSImage? {
        let name = running ? "waveform" : "waveform.slash"
        let image = NSImage(systemSymbolName: name, accessibilityDescription: running ? "Running" : "Stopped")
        image?.isTemplate = true
        return image
    }

    private func formatUptime() -> String? {
        guard let start = processStartDate else { return nil }
        let elapsed = Int(Date().timeIntervalSince(start))
        if elapsed < 60 {
            return "\(elapsed)s"
        } else if elapsed < 3600 {
            return "\(elapsed / 60)m \(elapsed % 60)s"
        } else {
            let h = elapsed / 3600
            let m = (elapsed % 3600) / 60
            return "\(h)h \(m)m"
        }
    }

    private func updateMenuState() {
        let running = (process != nil)
        startItem.isEnabled = !running
        stopItem.isEnabled = running
        if running, let uptime = formatUptime() {
            startItem.title = "Running · \(uptime)"
        } else {
            startItem.title = "Start"
        }
        autoStartItem.state = autoStartOnLaunch ? .on : .off
        statusItem.button?.image = statusImage(running: running)
        #if DEV_BUILD
        var title = "HiDock DEV"
        #else
        var title = "HiDock"
        #endif
        // Only show connected devices in the menu bar
        let connectedDevices = syncPairedDevices.filter { syncDeviceConnected[$0.deviceId] == true }
        if !connectedDevices.isEmpty {
            let deviceParts = connectedDevices.map { "\($0.shortName) ✓" }
            title += " · \(deviceParts.joined(separator: " · "))"
        }
        if let mic = selectedMicName, !mic.isEmpty {
            let isFallback = preferredMicName != nil && !preferredMicName!.isEmpty && mic != preferredMicName
            let suffix = isFallback ? " (fallback)" : ""
            title += " · \(shortenMicName(mic))\(suffix)"
        } else {
            title += " · No Mic"
        }
        statusItem.button?.title = title
        syncViewModelState()
    }

    private func startUptimeTimer() {
        guard uptimeTimer == nil else { return }
        uptimeTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            self.viewModel.triggerUptime = self.formatUptime() ?? ""
            self.updateMenuUptime()
        }
    }

    private func stopUptimeTimer() {
        uptimeTimer?.invalidate()
        uptimeTimer = nil
    }

    private func updateMenuUptime() {
        guard process != nil, let uptime = formatUptime() else { return }
        startItem.title = "Running · \(uptime)"
        startItem.isEnabled = false
    }

    // MARK: - Process management

    @objc private func startTriggerMenu() { startTrigger() }
    @objc private func stopTriggerMenu() { stopTrigger() }
    @objc private func toggleAutoStartMenu() { toggleAutoStart() }

    private func startTrigger() {
        #if DEV_BUILD
        log("Mic trigger disabled in dev build")
        return
        #endif
        guard process == nil else { return }

        if !FileManager.default.isExecutableFile(atPath: binaryPath) {
            log("Binary not found at \(binaryPath), attempting build...")
            buildTriggerBinaryAsync { [weak self] success in
                guard let self = self else { return }
                if success {
                    self.log("Build succeeded, starting trigger")
                    self.launchProcess()
                } else {
                    self.showError("Binary not found and build failed.\nExpected: \(self.binaryPath)")
                }
            }
            return
        }

        launchProcess()
    }

    private func launchProcess() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: binaryPath)

        var args: [String] = []
        if let mic = selectedMicName, !mic.isEmpty {
            args += ["--mic", mic]
        }
        p.arguments = args

        let outPipe = Pipe()
        p.standardOutput = outPipe
        p.standardError = Pipe()

        outPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            DispatchQueue.main.async {
                self?.handleCLIOutput(text)
            }
        }

        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async {
                guard let self = self else { return }
                let status = proc.terminationStatus
                self.log("Process terminated with status \(status)")

                outPipe.fileHandleForReading.readabilityHandler = nil

                self.process = nil
                self.processStartDate = nil
                self.stopUptimeTimer()
                self.updateMenuState()

                if !self.stoppingIntentionally && status != 0 {
                    self.handleCrash(exitStatus: status)
                } else if self.stoppingIntentionally {
                    self.crashCount = 0
                }
                self.stoppingIntentionally = false
            }
        }

        do {
            try p.run()
            process = p
            processStartDate = Date()
            let micDesc = selectedMicName ?? "(default)"
            log("Started hidock-mic-trigger (pid \(p.processIdentifier), mic: \(micDesc))")
            startUptimeTimer()
            updateMenuState()
        } catch {
            log("Failed to start: \(error)")
            showError("Failed to start hidock-mic-trigger:\n\(error.localizedDescription)")
        }
    }

    private func handleCrash(exitStatus: Int32) {
        crashCount += 1
        log("Unexpected exit (status \(exitStatus)), crash #\(crashCount)/\(maxCrashRetries)")

        if crashCount >= maxCrashRetries {
            log("Max crash retries reached, giving up")
            postNotification(title: "HiDock Mic Trigger Stopped",
                             body: "Crashed \(maxCrashRetries) times. Restart manually.")
            crashCount = 0
            return
        }

        log("Auto-restarting in \(Int(crashRetryDelay))s...")
        postNotification(title: "HiDock Mic Trigger Restarting",
                         body: "Process crashed (exit \(exitStatus)). Restarting...")
        DispatchQueue.main.asyncAfter(deadline: .now() + crashRetryDelay) { [weak self] in
            guard let self = self, self.process == nil else { return }
            self.startTrigger()
        }
    }

    private func stopTrigger() {
        guard let p = process else { return }
        stoppingIntentionally = true
        log("Stopping hidock-mic-trigger (pid \(p.processIdentifier))")
        p.interrupt()
        DispatchQueue.global().async { [weak self] in
            p.waitUntilExit()
            DispatchQueue.main.async {
                self?.log("Process stopped")
                self?.process = nil
                self?.processStartDate = nil
                self?.stoppingIntentionally = false
                self?.crashCount = 0
                self?.stopUptimeTimer()
                // Trigger child gone → no ffmpeg → HiDock not being held.
                self?.viewModel.hidockRecordingActive = false
                self?.updateMenuState()
            }
        }
    }

    private func restartTrigger() {
        guard let p = process else {
            startTrigger()
            return
        }
        stoppingIntentionally = true
        log("Restarting trigger with new mic selection...")
        p.interrupt()
        DispatchQueue.global().async { [weak self] in
            p.waitUntilExit()
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.process = nil
                self.processStartDate = nil
                self.stoppingIntentionally = false
                self.updateMenuState()
                self.startTrigger()
            }
        }
    }

    private func toggleAutoStart() {
        autoStartOnLaunch.toggle()
        viewModel.autoStartOnLaunch = autoStartOnLaunch
        updateMenuState()
        if autoStartOnLaunch && process == nil {
            startTrigger()
        }
    }

    // MARK: - HiDock Sync Startup

    private func autoConnectSyncIfPaired(startTriggerOnCompletion: Bool = false) {
        // If we were asked to start the trigger on completion but we
        // short-circuit (busy, extractor missing), still honour the
        // promise — otherwise the app launches with no trigger running.
        func finishEarly() {
            if startTriggerOnCompletion && autoStartOnLaunch {
                log("autoConnectSyncIfPaired: starting trigger after early exit")
                startTrigger()
            }
        }
        guard !syncBusy else {
            log("autoConnectSyncIfPaired: skipping, already busy")
            finishEarly()
            return
        }
        guard ensureExtractorReady() else {
            log("autoConnectSyncIfPaired: extractor not ready, aborting")
            finishEarly()
            return
        }
        // Unblock any reopen-driven refreshes that were deferred during
        // the early-launch window — from here on, probes are safe
        // because we own the initial state.
        didInitialAutoConnect = true
        log("autoConnectSyncIfPaired: running list-devices")

        runExtractor(arguments: ["list-devices"]) { [weak self] result in
            guard let self = self else { return }
            if case .success(let data) = result,
               let response = try? JSONDecoder().decode(HiDockDeviceListResponse.self, from: data) {
                let alreadyPaired = Set(self.syncPairedDevices.map(\.deviceId))
                for device in response.devices where !alreadyPaired.contains("hidock:\(device.productId)") {
                    // Reject USB-enumeration stubs that briefly appear when a
                    // device is still negotiating its descriptors. 'USB STUB'
                    // is the placeholder displayName used by some USB
                    // subsystems during enumeration; productId 65280 (0xFF00)
                    // is a well-known 'unconfigured' marker. Auto-pairing
                    // these leaves a phantom device in the paired list that
                    // never connects.
                    let lcName = device.displayName.lowercased()
                    if lcName.contains("usb stub") || lcName.contains("stub") || device.productId == 0 || device.productId == 0xFF00 {
                        self.log("Skipping auto-pair for enumeration stub: \(device.displayName) (pid: \(device.productId))")
                        continue
                    }
                    let pairedDevice = HiDockPairedDevice(productId: device.productId, displayName: device.displayName)
                    var devices = self.syncPairedDevices
                    devices.append(pairedDevice)
                    self.syncPairedDevices = devices
                    self.log("Auto-paired \(device.displayName) (product ID: \(device.productId))")
                }
            }

            let devices = self.syncPairedDevices
            guard !devices.isEmpty else {
                DispatchQueue.main.async { finishEarly() }
                return
            }
            self.log("Auto-connecting \(devices.count) paired device(s) on startup")

            // Do NOT pause the mic trigger for auto-connect probes. The
            // trigger is the primary purpose of the app; stopping it
            // under the user's feet to do a background status refresh
            // is more disruptive than helpful. Instead, if the trigger
            // is already running, skip HiDock probes this round — the
            // cards will keep their last-known state (preserved by
            // renderSyncStatus). Volumes don't share an interface with
            // ffmpeg so they're safe to probe either way. Manual
            // Reconnect (↻ on a card) still pauses the trigger —
            // that's explicit user intent.
            // Gate on hidockRecordingActive (ffmpeg currently streaming
            // audio off the HiDock), NOT on process != nil (trigger
            // child alive). The trigger child stays alive for the life
            // of the app; ffmpeg only runs when the mic is actually in
            // use. Checking process != nil would skip HiDock probes
            // forever and break auto-download / auto-transcribe, which
            // rely on refreshSyncStatus firing 8s after the mic goes
            // idle to pick up new recordings.
            let recording = self.viewModel.hidockRecordingActive
            let probeDevices = devices.filter { device in
                if device.deviceType == .volume { return true }
                if !recording { return true }
                self.log("Auto-connect: skipping \(device.cleanName) — ffmpeg is currently recording, keeping last-known state")
                return false
            }
            guard !probeDevices.isEmpty else {
                self.log("Auto-connect: no devices to probe (recording active, all HiDocks skipped)")
                DispatchQueue.main.async {
                    self.syncViewModelState()
                    finishEarly()
                }
                return
            }
            self.runAutoConnectProbes(
                devices: probeDevices,
                restartTriggerAfter: false,
                startTriggerOnCompletion: startTriggerOnCompletion
            )
        }
    }

    private func runAutoConnectProbes(
        devices: [HiDockPairedDevice],
        restartTriggerAfter: Bool,
        startTriggerOnCompletion: Bool = false
    ) {
        syncBusy = true
        syncViewModelState()

        let group = DispatchGroup()
        var anyConnected = false
        var deviceErrors: [String: String] = [:]  // device name -> error
        let now = Date()

        for device in devices {
            if let until = syncDeviceHungUntil[device.deviceId], until > now {
                log("Auto-connect: skipping \(device.cleanName) — hung-backoff active for \(Int(until.timeIntervalSince(now)))s more")
                if syncDeviceConnected[device.deviceId] == true { anyConnected = true }
                continue
            }
            group.enter()

            let args: [String]
            let pid: Int?
            switch device.deviceType {
            case .hidock:
                args = ["status", "--timeout-ms", "2000"]
                pid = device.productId
            case .volume:
                args = self.volumeExtractorArguments("volume-status", device: device)
                pid = nil
            }

            self.runExtractor(arguments: args, productId: pid) { [weak self] result in
                guard let self = self else { group.leave(); return }
                switch result {
                case .success(let data):
                    if let status = try? JSONDecoder().decode(HiDockSyncStatusResponse.self, from: data) {
                        // Same transient-"held by" guard as refreshSyncStatus
                        let transientHeldBy = !status.connected
                            && device.deviceType == .hidock
                            && (status.error?.contains("held by") ?? false)
                            && self.syncDeviceConnected[device.deviceId] == true
                        if transientHeldBy {
                            self.log("Auto-connect: ignoring transient 'held by' for \(device.cleanName): \(status.error ?? "")")
                            anyConnected = true
                        } else {
                            self.renderSyncStatus(status, device: device)
                            if status.connected {
                                anyConnected = true
                                self.log("Auto-connect: \(device.cleanName) connected (\(status.recordings.count) recordings)")
                            } else {
                                let err = status.error ?? "unknown"
                                self.log("Auto-connect: \(device.cleanName) not connected: \(err)")
                                deviceErrors[device.cleanName] = err
                            }
                        }
                    } else {
                        let preview = String(data: data.prefix(200), encoding: .utf8) ?? "(binary)"
                        self.log("Auto-connect: \(device.cleanName) decode failed: \(preview)")
                        deviceErrors[device.cleanName] = "Failed to decode status response"
                    }
                case .failure(let error):
                    let desc = error.localizedDescription
                    let shortDesc = desc.components(separatedBy: "\n").last(where: { !$0.isEmpty }) ?? desc
                    self.log("Auto-connect: \(device.cleanName) failed: \(shortDesc)")
                    deviceErrors[device.cleanName] = shortDesc
                }
                group.leave()
            }
        }

        group.notify(queue: .main) { [weak self] in
            guard let self = self else { return }
            self.syncBusy = false
            if !anyConnected, !deviceErrors.isEmpty {
                // Prefer the most informative error (one with "held by" info)
                let bestError = deviceErrors.values.first(where: { $0.contains("held by") })
                    ?? deviceErrors.values.first ?? "unknown"
                let message = syncErrorDescription(bestError)
                self.viewModel.syncStatus = message
                self.viewModel.syncStatusLevel = .warning
            }
            self.updateMenuSyncStatus(connected: anyConnected)
            // Populate transcribed/tagged state from the on-disk
            // transcripts directory. Without this call the freshly-
            // built syncEntries have transcribed=false / tagged=false,
            // so the Status column can't reach "Transcribed" and the
            // Tagged column is empty — even when the .md/.json files
            // are right there on disk. performRefreshProbes calls it;
            // the launch path (runAutoConnectProbes) was missing it.
            self.refreshTranscriptionState()
            self.syncViewModelState()
            if restartTriggerAfter {
                self.log("Auto-connect: restarting mic trigger")
                self.startTrigger()
            } else if startTriggerOnCompletion && self.autoStartOnLaunch && self.process == nil {
                // Launch-time path: we probed first, now kick the trigger.
                self.log("Auto-connect: launch probe complete — starting mic trigger")
                self.startTrigger()
            }
        }
    }

    private func updateMenuSyncStatus(connected: Bool) {
        if !syncPaired {
            syncWindowItem?.title = "Show Window..."
            return
        }
        let connectedDevices = syncPairedDevices.filter { syncDeviceConnected[$0.deviceId] == true }
        if connectedDevices.isEmpty {
            syncWindowItem?.title = "Show Window..."
        } else {
            let parts = connectedDevices.map { "\($0.shortName) ✓" }
            syncWindowItem?.title = "Sync: \(parts.joined(separator: " · "))"
        }
        updateMenuState()
    }

    // MARK: - UI

    @objc private func showLogs() {
        let cliLogPath = "\(NSHomeDirectory())/Library/Logs/mic-trigger.log"
        let cliErrPath = "\(NSHomeDirectory())/Library/Logs/mic-trigger.err"

        var opened = false
        for path in [logPath, cliLogPath, cliErrPath] {
            if FileManager.default.fileExists(atPath: path) {
                NSWorkspace.shared.open(URL(fileURLWithPath: path))
                opened = true
            }
        }
        if !opened {
            showError("No log files found yet.\nExpected:\n\(cliLogPath)\n\(cliErrPath)")
        }
    }

    @objc private func showStatus() {
        showSyncWindow()
    }

    @objc private func checkForUpdatesManual() {
        UpdateChecker.manualCheck()
    }

    @objc private func showAbout() {
        let alert = NSAlert()
        alert.alertStyle = .informational
        alert.messageText = "HiDock Mic Trigger"
        let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0.0"
        alert.informativeText = "Desktop app for HiDock USB docking stations.\nMic trigger, USB sync, and transcription.\nVersion \(version)"
        alert.runModal()
    }

    /// Token injected at build time via CI. Read from bundle's embedded file.
    private var feedbackToken: String? {
        if let path = Bundle.main.path(forResource: "feedback_token", ofType: "txt"),
           let token = try? String(contentsOfFile: path, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines),
           !token.isEmpty {
            return token
        }
        // Fallback: check for file next to the app (dev builds)
        let devPath = "\(repoRoot)/feedback_token.txt"
        if let token = try? String(contentsOfFile: devPath, encoding: .utf8).trimmingCharacters(in: .whitespacesAndNewlines),
           !token.isEmpty {
            return token
        }
        return nil
    }

    // User-friendly category labels → technical mapping
    private struct FeedbackCategory {
        let label: String          // shown to user
        let gitHubLabel: String    // GitHub issue label
        let component: String      // file paths for Copilot
    }

    private let feedbackCategories: [FeedbackCategory] = [
        FeedbackCategory(label: "Something isn't working", gitHubLabel: "bug", component: "General"),
        FeedbackCategory(label: "Recording & downloads", gitHubLabel: "usb-sync", component: "`usb-extractor/extractor.py`, `AppDelegate.swift` (sync section)"),
        FeedbackCategory(label: "Microphone detection", gitHubLabel: "mic-trigger", component: "`mic-trigger/MicTrigger.swift`, `AppDelegate.swift` (trigger section)"),
        FeedbackCategory(label: "Transcription & speech-to-text", gitHubLabel: "transcription", component: "`transcription-pipeline/transcribe_cpp.py`, `core/transcription.py`"),
        FeedbackCategory(label: "App appearance or layout", gitHubLabel: "ui", component: "`Sources/Views/*.swift`, `ui/main_window.py`"),
        FeedbackCategory(label: "I have a suggestion", gitHubLabel: "enhancement", component: "General"),
    ]

    private let feedbackSeverities: [(label: String, gitHubLabel: String)] = [
        ("It stops me from working", "priority-high"),
        ("It's annoying but I can work around it", "priority-medium"),
        ("It's a minor thing", "priority-low"),
    ]

    @objc private func sendFeedback() {
        let alert = NSAlert()
        alert.alertStyle = .informational
        alert.messageText = "Send Feedback"
        alert.informativeText = ""
        alert.addButton(withTitle: "Send")
        alert.addButton(withTitle: "Cancel")

        // Build the form
        // Layout uses NSView coordinate system (origin bottom-left).
        // All three text boxes are the same height so the form looks balanced.
        let textBoxHeight: CGFloat = 72
        let labelHeight: CGFloat = 18
        let controlHeight: CGFloat = 26
        let gap: CGFloat = 6
        let w: CGFloat = 420

        // Build layout bottom-up, calculating y positions
        var y: CGFloat = 0

        // Steps to reproduce (optional) — multi-line, same size as the others
        let stepsScroll = NSScrollView(frame: NSRect(x: 0, y: y, width: w, height: textBoxHeight))
        stepsScroll.hasVerticalScroller = true
        stepsScroll.borderType = .bezelBorder
        let stepsText = NSTextView(frame: NSRect(x: 0, y: 0, width: w, height: textBoxHeight))
        stepsText.isEditable = true; stepsText.isRichText = false
        stepsText.font = .systemFont(ofSize: 13)
        stepsText.autoresizingMask = [.width, .height]
        stepsText.isVerticallyResizable = true
        stepsText.textContainer?.widthTracksTextView = true
        stepsScroll.documentView = stepsText

        y += textBoxHeight + 4
        let stepsLabel = NSTextField(labelWithString: "Steps to reproduce (optional)")
        stepsLabel.font = .systemFont(ofSize: 11)
        stepsLabel.textColor = .secondaryLabelColor
        stepsLabel.frame = NSRect(x: 0, y: y, width: w, height: labelHeight)

        y += labelHeight + gap

        // What did you expect to happen?
        let expectedScroll = NSScrollView(frame: NSRect(x: 0, y: y, width: w, height: textBoxHeight))
        expectedScroll.hasVerticalScroller = true
        expectedScroll.borderType = .bezelBorder
        let expectedText = NSTextView(frame: NSRect(x: 0, y: 0, width: w, height: textBoxHeight))
        expectedText.isEditable = true; expectedText.isRichText = false
        expectedText.font = .systemFont(ofSize: 13)
        expectedText.autoresizingMask = [.width, .height]
        expectedText.isVerticallyResizable = true
        expectedText.textContainer?.widthTracksTextView = true
        expectedScroll.documentView = expectedText

        y += textBoxHeight + 4
        let expectedLabel = NSTextField(labelWithString: "What did you expect to happen?")
        expectedLabel.font = .systemFont(ofSize: 12, weight: .medium)
        expectedLabel.frame = NSRect(x: 0, y: y, width: w, height: labelHeight)

        y += labelHeight + gap

        // What happened?
        let descScroll = NSScrollView(frame: NSRect(x: 0, y: y, width: w, height: textBoxHeight))
        descScroll.hasVerticalScroller = true
        descScroll.borderType = .bezelBorder
        let descText = NSTextView(frame: NSRect(x: 0, y: 0, width: w, height: textBoxHeight))
        descText.isEditable = true; descText.isRichText = false
        descText.font = .systemFont(ofSize: 13)
        descText.autoresizingMask = [.width, .height]
        descText.isVerticallyResizable = true
        descText.textContainer?.widthTracksTextView = true
        descScroll.documentView = descText

        y += textBoxHeight + 4
        let descLabel = NSTextField(labelWithString: "What happened?")
        descLabel.font = .systemFont(ofSize: 12, weight: .medium)
        descLabel.frame = NSRect(x: 0, y: y, width: w, height: labelHeight)

        y += labelHeight + gap

        // Severity
        let severityPopup = NSPopUpButton(frame: NSRect(x: 0, y: y, width: w, height: controlHeight), pullsDown: false)
        for sev in feedbackSeverities {
            severityPopup.addItem(withTitle: sev.label)
        }

        y += controlHeight + 4
        let severityLabel = NSTextField(labelWithString: "How much does it affect you?")
        severityLabel.font = .systemFont(ofSize: 12, weight: .medium)
        severityLabel.frame = NSRect(x: 0, y: y, width: w, height: labelHeight)

        y += labelHeight + gap

        // Category
        let categoryPopup = NSPopUpButton(frame: NSRect(x: 0, y: y, width: w, height: controlHeight), pullsDown: false)
        for cat in feedbackCategories {
            categoryPopup.addItem(withTitle: cat.label)
        }

        y += controlHeight + 4
        let categoryLabel = NSTextField(labelWithString: "What's this about?")
        categoryLabel.font = .systemFont(ofSize: 12, weight: .medium)
        categoryLabel.frame = NSRect(x: 0, y: y, width: w, height: labelHeight)

        let containerHeight = y + labelHeight
        let container = NSView(frame: NSRect(x: 0, y: 0, width: w, height: containerHeight))
        for sub in [stepsScroll, stepsLabel, expectedScroll, expectedLabel,
                    descScroll, descLabel, severityPopup, severityLabel,
                    categoryPopup, categoryLabel] as [NSView] {
            container.addSubview(sub)
        }

        alert.accessoryView = container
        alert.window.initialFirstResponder = descText

        let response = alert.runModal()
        guard response == .alertFirstButtonReturn else { return }

        let description = descText.string.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !description.isEmpty else { return }

        let category = feedbackCategories[categoryPopup.indexOfSelectedItem]
        let severity = feedbackSeverities[severityPopup.indexOfSelectedItem]
        let expected = expectedText.string.trimmingCharacters(in: .whitespacesAndNewlines)
        let steps = stepsText.string.trimmingCharacters(in: .whitespacesAndNewlines)

        // Gather system info
        let appVersion = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0.0"
        let macOSVersion = ProcessInfo.processInfo.operatingSystemVersionString
        let connectedDevices = syncPairedDevices.filter { syncDeviceConnected[$0.deviceId] == true }
        let deviceStatus = connectedDevices.isEmpty ? "Not connected" : connectedDevices.map(\.shortName).joined(separator: ", ")
        let triggerStatus = process != nil ? "Running (\(selectedMicName ?? "unknown mic"))" : "Stopped"
        let recordingCount = syncEntries.count
        let downloadedCount = syncEntries.filter(\.recording.downloaded).count

        // Build structured issue body for Copilot
        let title = category.gitHubLabel == "enhancement"
            ? "Feature: \(String(description.prefix(60)))"
            : "\(category.label): \(String(description.prefix(50)))"

        var body = "## Description\n\(description)\n"

        if !expected.isEmpty {
            body += "\n## Expected Behavior\n\(expected)\n"
        }
        if !steps.isEmpty {
            body += "\n## Steps to Reproduce\n\(steps)\n"
        }

        body += "\n## Component\n\(category.component)\n"
        body += "\n## Platform\nmacOS\n"

        body += """

        <details>
        <summary>System Information</summary>

        - **App Version:** \(appVersion)
        - **macOS:** \(macOSVersion)
        - **Devices:** \(deviceStatus)
        - **Mic Trigger:** \(triggerStatus)
        - **Recordings:** \(recordingCount) synced, \(downloadedCount) downloaded
        </details>
        """

        let labels = [category.gitHubLabel, severity.gitHubLabel, "feedback"]
        submitGitHubIssue(title: title, body: body, labels: labels)
    }

    private func submitGitHubIssue(title: String, body: String, labels: [String] = ["feedback"]) {
        guard let token = feedbackToken else {
            log("No feedback token, falling back to browser")
            guard let encodedBody = body.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed),
                  let encodedTitle = title.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed),
                  let url = URL(string: "https://github.com/jw-gsl/HiDock-Mic-Trigger/issues/new?title=\(encodedTitle)&body=\(encodedBody)&labels=\(labels.joined(separator: ","))") else { return }
            NSWorkspace.shared.open(url)
            return
        }

        let url = URL(string: "https://api.github.com/repos/jw-gsl/HiDock-Mic-Trigger/issues")!
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("token \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("HiDock/1.0", forHTTPHeaderField: "User-Agent")

        let payload: [String: Any] = [
            "title": title,
            "body": body,
            "labels": labels
        ]
        request.httpBody = try? JSONSerialization.data(withJSONObject: payload)

        log("Submitting feedback issue...")
        URLSession.shared.dataTask(with: request) { [weak self] data, response, error in
            DispatchQueue.main.async {
                if let error = error {
                    self?.log("Feedback submission failed: \(error.localizedDescription)")
                    self?.postNotification(title: "Feedback Failed", body: "Could not submit feedback. Please try again.")
                    return
                }
                let httpResponse = response as? HTTPURLResponse
                if httpResponse?.statusCode == 201, let data = data {
                    self?.log("Feedback submitted successfully")
                    self?.postNotification(title: "Feedback Sent", body: "Thank you! Your feedback has been submitted.")
                    // Save to local history
                    if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                        self?.saveFeedbackToHistory(
                            title: title,
                            body: body,
                            url: json["html_url"] as? String,
                            number: json["number"] as? Int,
                            state: json["state"] as? String ?? "open"
                        )
                    }
                } else {
                    let statusCode = httpResponse?.statusCode ?? 0
                    self?.log("Feedback submission returned status \(statusCode)")
                    self?.postNotification(title: "Feedback Failed", body: "Server returned status \(statusCode). Please try again.")
                }
            }
        }.resume()
    }

    // MARK: - Merge & Trim Audio

    private let ffmpegPath = "/opt/homebrew/bin/ffmpeg"

    private func mergeSelectedRecordings() {
        let selected = selectedSyncEntries()
        let entries = selected
            .filter { $0.recording.downloaded && $0.recording.localExists }
            .sorted { "\($0.recording.createDate) \($0.recording.createTime)" < "\($1.recording.createDate) \($1.recording.createTime)" }
        guard entries.count >= 2 else {
            let total = selected.count
            let local = selected.filter { $0.recording.localExists }.count
            log("Merge: \(total) selected, \(local) have local files, need 2+ downloaded files")
            if total >= 2 && local < 2 {
                viewModel.syncStatus = "Merge requires 2+ downloaded recordings (only \(local) available locally)"
                viewModel.syncStatusLevel = .warning
                syncViewModelState()
            }
            return
        }
        guard FileManager.default.fileExists(atPath: ffmpegPath) else {
            showError("ffmpeg not found at \(ffmpegPath).\nInstall with: brew install ffmpeg")
            return
        }
        guard let outputFolder = syncOutputFolder else { return }

        let firstName = URL(fileURLWithPath: entries.first!.recording.outputPath).deletingPathExtension().lastPathComponent
        let lastName = URL(fileURLWithPath: entries.last!.recording.outputPath).deletingPathExtension().lastPathComponent
        var outputName = "Merged-\(firstName)-to-\(lastName).mp3"
        if outputName.count > 100 { outputName = "Merged-\(firstName).mp3" }
        let outputPath = "\(outputFolder)/\(outputName)"
        let childNames = Set(entries.map(\.recording.name))

        // Check if these same children were already merged — replace instead of creating duplicates
        if let existingIdx = mergeGroups.firstIndex(where: { Set($0.childNames) == childNames }) {
            let old = mergeGroups[existingIdx]
            if old.outputPath != outputPath {
                try? FileManager.default.removeItem(atPath: old.outputPath)
                // Clean up old transcripts
                let oldStem = (old.outputPath as NSString).lastPathComponent.replacingOccurrences(of: ".mp3", with: "")
                let transcriptDir = syncTranscriptFolder ?? "\(NSHomeDirectory())/HiDock/Raw Transcripts"
                for suffix in ["", "_diarized", "_whisper"] {
                    let ext = suffix.isEmpty ? ".md" : ".json"
                    try? FileManager.default.removeItem(atPath: "\(transcriptDir)/\(oldStem)\(suffix)\(ext)")
                }
                log("Replaced existing merge: \(old.outputName)")
            }
            mergeGroups.remove(at: existingIdx)
        }

        // Remove any existing file at the output path (re-merge)
        if FileManager.default.fileExists(atPath: outputPath) {
            try? FileManager.default.removeItem(atPath: outputPath)
        }

        log("Merging \(entries.count) recordings into \(outputPath)")
        viewModel.syncStatus = "Merging \(entries.count) recordings…"
        viewModel.syncStatusLevel = .secondary
        syncViewModelState()

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            // Write concat list to temp file
            let listPath = NSTemporaryDirectory() + "hidock-merge-list.txt"
            let listContent = entries.map { "file '\($0.recording.outputPath)'" }.joined(separator: "\n")
            try? listContent.write(toFile: listPath, atomically: true, encoding: .utf8)

            let process = Process()
            process.executableURL = URL(fileURLWithPath: self.ffmpegPath)
            process.arguments = ["-y", "-f", "concat", "-safe", "0", "-i", listPath, "-c", "copy", outputPath]
            process.standardOutput = FileHandle.nullDevice
            process.standardError = FileHandle.nullDevice

            do {
                try process.run()
                process.waitUntilExit()
            } catch {
                DispatchQueue.main.async {
                    self.viewModel.syncStatus = "Merge failed"
                    self.viewModel.syncStatusLevel = .error
                    self.showError("Merge failed: \(error.localizedDescription)")
                    self.syncViewModelState()
                }
                return
            }

            try? FileManager.default.removeItem(atPath: listPath)

            DispatchQueue.main.async {
                if process.terminationStatus == 0 {
                    let outputName = (outputPath as NSString).lastPathComponent
                    self.log("Merged \(entries.count) recordings → \(outputPath)")
                    self.viewModel.syncStatus = "Merged → \(outputName)"
                    self.viewModel.syncStatusLevel = .success

                    // Save merge group for tree display
                    let totalDuration = entries.reduce(0.0) { $0 + $1.recording.duration }
                    let group = MergeGroup(
                        outputPath: outputPath,
                        childNames: entries.map(\.recording.name),
                        totalDuration: totalDuration
                    )
                    self.mergeGroups.append(group)
                    self.saveMergeGroups()

                    self.syncCheckedRecordings.removeAll()
                    self.refreshSyncStatus()
                    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: outputPath)])
                } else {
                    self.viewModel.syncStatus = "Merge failed"
                    self.viewModel.syncStatusLevel = .error
                    self.showError("ffmpeg exited with status \(process.terminationStatus)")
                }
                self.syncViewModelState()
            }
        }
    }

    private func saveMergeGroups() {
        do {
            let data = try JSONEncoder().encode(mergeGroups)
            try data.write(to: URL(fileURLWithPath: mergeGroupsPath))
        } catch {
            log("Failed to save merge groups: \(error)")
        }
        viewModel.mergeGroups = mergeGroups
        syncViewModelState()
    }

    private func loadMergeGroups() {
        guard FileManager.default.fileExists(atPath: mergeGroupsPath),
              let data = FileManager.default.contents(atPath: mergeGroupsPath),
              let groups = try? JSONDecoder().decode([MergeGroup].self, from: data) else {
            return
        }
        mergeGroups = groups
        viewModel.mergeGroups = groups
    }

    private func toggleMergeExpand(_ groupId: String) {
        if viewModel.expandedMergeGroups.contains(groupId) {
            viewModel.expandedMergeGroups.remove(groupId)
        } else {
            viewModel.expandedMergeGroups.insert(groupId)
        }
    }

    private var trimWindow: NSWindow?

    private func showTrimDialog(for path: String) {
        guard FileManager.default.fileExists(atPath: ffmpegPath) else {
            showError("ffmpeg not found at \(ffmpegPath).\nInstall with: brew install ffmpeg")
            return
        }
        let entry = syncEntries.first { $0.recording.outputPath == path }
        let duration = entry?.recording.duration ?? 0

        let filename = URL(fileURLWithPath: path).lastPathComponent
        let trimView = TrimAudioView(
            filename: filename,
            duration: duration,
            onTrim: { [weak self] start, end, saveAsCopy in
                self?.trimWindow?.close()
                self?.trimWindow = nil
                self?.trimRecording(path: path, start: start, end: end, saveAsCopy: saveAsCopy)
            },
            onCancel: { [weak self] in
                self?.trimWindow?.close()
                self?.trimWindow = nil
            }
        )

        let hostingView = NSHostingView(rootView: trimView)
        hostingView.frame = NSRect(x: 0, y: 0, width: 300, height: 260)
        let window = NSWindow(contentRect: hostingView.frame, styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Trim Audio"
        window.contentView = hostingView
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        trimWindow = window
    }

    private func trimRecording(path: String, start: Double, end: Double, saveAsCopy: Bool) {
        let url = URL(fileURLWithPath: path)
        let outputPath: String
        if saveAsCopy {
            let stem = url.deletingPathExtension().lastPathComponent
            let dir = url.deletingLastPathComponent().path
            outputPath = "\(dir)/\(stem)-trimmed.mp3"
        } else {
            outputPath = path + ".tmp"
        }

        viewModel.syncStatus = "Trimming…"
        viewModel.syncStatusLevel = .secondary
        viewModel.syncBusy = true
        syncViewModelState()

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            let process = Process()
            process.executableURL = URL(fileURLWithPath: self.ffmpegPath)
            process.arguments = [
                "-y", "-i", path,
                "-ss", String(format: "%.2f", start),
                "-to", String(format: "%.2f", end),
                "-c", "copy", outputPath
            ]
            process.standardOutput = FileHandle.nullDevice
            process.standardError = FileHandle.nullDevice

            do {
                try process.run()
                process.waitUntilExit()
            } catch {
                DispatchQueue.main.async {
                    self.viewModel.syncBusy = false
                    self.viewModel.syncStatus = "Trim failed"
                    self.viewModel.syncStatusLevel = .error
                    self.showError("Trim failed: \(error.localizedDescription)")
                    self.syncViewModelState()
                }
                return
            }

            // If replacing original, swap files
            if !saveAsCopy && process.terminationStatus == 0 {
                try? FileManager.default.removeItem(atPath: path)
                try? FileManager.default.moveItem(atPath: outputPath, toPath: path)
            }

            DispatchQueue.main.async {
                self.viewModel.syncBusy = false
                if process.terminationStatus == 0 {
                    let name = URL(fileURLWithPath: path).lastPathComponent
                    self.log("Trimmed \(name) (\(start)s–\(end)s)")
                    self.viewModel.syncStatus = "Trimmed \(name)"
                    self.viewModel.syncStatusLevel = .success
                    self.refreshSyncStatus()
                } else {
                    self.viewModel.syncStatus = "Trim failed"
                    self.viewModel.syncStatusLevel = .error
                    self.showError("ffmpeg exited with status \(process.terminationStatus)")
                }
                self.syncViewModelState()
            }
        }
    }

    // MARK: - Cached Recordings (instant load)

    private func loadCachedRecordings() {
        // Load cached recordings from extractor state for instant display
        // The live USB refresh will update these in the background
        guard ensureExtractorReady() else { return }
        let devices = syncPairedDevices
        guard !devices.isEmpty else { return }

        // Run status with a very short timeout — it'll use cached catalog if available
        for device in devices {
            runExtractor(arguments: ["status", "--timeout-ms", "500"], productId: device.productId) { [weak self] result in
                guard let self = self else { return }
                if case .success(let data) = result,
                   let payload = try? JSONDecoder().decode(HiDockSyncStatusResponse.self, from: data) {
                    self.renderSyncStatus(payload, device: device)
                    self.refreshTranscriptionState()
                    self.syncViewModelState()
                }
            }
        }
    }

    // MARK: - Re-diarize

    private func rediarizeTranscript(jsonPath: String, nSpeakers: Int?) {
        guard ensureTranscriptionReady() else { return }
        log("Re-diarizing \(jsonPath) with \(nSpeakers.map { "\($0)" } ?? "auto") speakers")
        viewModel.syncStatus = "Re-diarizing…"
        viewModel.syncStatusLevel = .secondary
        syncViewModelState()

        var args = ["rediarize", jsonPath]
        if let n = nSpeakers { args += ["--n-speakers", "\(n)"] }

        runTranscription(arguments: args) { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success:
                self.log("Re-diarization complete")
                self.viewModel.syncStatus = "Re-diarization complete — reopen transcript to see changes"
                self.viewModel.syncStatusLevel = .success
                // Close and reopen the viewer to pick up the new data
                self.transcriptViewerWindow?.close()
                self.transcriptViewerWindow = nil
                // Derive md path from json path
                let mdPath = jsonPath.replacingOccurrences(of: "_diarized.json", with: ".md")
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                    self.openTranscriptViewer(transcriptMdPath: mdPath)
                }
            case .failure(let error):
                self.log("Re-diarization failed: \(error.localizedDescription)")
                self.viewModel.syncStatus = "Re-diarization failed"
                self.viewModel.syncStatusLevel = .error
            }
            self.syncViewModelState()
        }
    }

    // MARK: - Feedback History

    private var feedbackHistoryPath: String {
        "\(NSHomeDirectory())/HiDock/feedback_history.json"
    }

    private func loadFeedbackHistory() -> [[String: Any]] {
        guard let data = FileManager.default.contents(atPath: feedbackHistoryPath),
              let items = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] else {
            return []
        }
        return items
    }

    private func saveFeedbackToHistory(title: String, body: String, url: String?, number: Int?, state: String) {
        var history = loadFeedbackHistory()
        let entry: [String: Any] = [
            "title": title,
            "body": body,
            "url": url ?? "",
            "number": number ?? 0,
            "state": state,
            "date": ISO8601DateFormatter().string(from: Date()),
        ]
        history.insert(entry, at: 0)
        if history.count > 50 { history = Array(history.prefix(50)) }

        let dir = (feedbackHistoryPath as NSString).deletingLastPathComponent
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        if let data = try? JSONSerialization.data(withJSONObject: history, options: .prettyPrinted) {
            try? data.write(to: URL(fileURLWithPath: feedbackHistoryPath))
        }
    }

    private var feedbackHistoryWindow: NSWindow?

    @objc private func showFeedbackHistory() {
        let history = loadFeedbackHistory()
        if history.isEmpty {
            let alert = NSAlert()
            alert.messageText = "My Feedback"
            alert.informativeText = "No feedback submitted yet."
            alert.runModal()
            return
        }

        let items = history.enumerated().map { (index, item) in
            FeedbackItem(
                id: index,
                title: item["title"] as? String ?? "Untitled",
                body: item["body"] as? String ?? "",
                url: item["url"] as? String ?? "",
                number: item["number"] as? Int ?? 0,
                state: item["state"] as? String ?? "open",
                date: item["date"] as? String ?? ""
            )
        }

        if feedbackHistoryWindow == nil {
            let win = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 640, height: 420),
                styleMask: [.titled, .closable, .resizable, .miniaturizable],
                backing: .buffered, defer: false
            )
            win.center()
            win.title = "My Feedback"
            win.isReleasedWhenClosed = false
            win.minSize = NSSize(width: 500, height: 300)
            feedbackHistoryWindow = win
        }

        let hostingView = NSHostingView(rootView: FeedbackHistoryView(items: items))
        feedbackHistoryWindow?.contentView = hostingView
        feedbackHistoryWindow?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc private func showSyncWindow() {
        if syncWindow == nil {
            // Size window to fit all columns, capped to 90% of screen
            let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
            let idealWidth: CGFloat = 1380  // sum of ideal column widths + padding
            let idealHeight: CGFloat = 700
            let winWidth = min(idealWidth, screen.width * 0.9)
            let winHeight = min(idealHeight, screen.height * 0.9)
            let rect = NSRect(x: 0, y: 0, width: winWidth, height: winHeight)
            let style: NSWindow.StyleMask = [.titled, .closable, .miniaturizable, .resizable]
            let win = NSWindow(contentRect: rect, styleMask: style, backing: .buffered, defer: false)
            win.center()
            #if DEV_BUILD
            win.title = "HiDock DEV"
            #else
            win.title = "HiDock"
            #endif
            win.isReleasedWhenClosed = false
            win.delegate = self
            win.minSize = NSSize(width: 980, height: 510)

            let hostingView = NSHostingView(rootView: MainWindowView(viewModel: viewModel))
            win.contentView = hostingView

            syncWindow = win
        }
        syncViewModelState()
        NSApp.setActivationPolicy(.regular)
        syncWindow?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        // Refresh on reopen, but only if the initial launch-time
        // autoConnectSyncIfPaired has already completed. During the
        // launch sequence, applicationDidFinishLaunching calls
        // showSyncWindow BEFORE startTrigger — firing refreshSyncStatus
        // here would dispatch probes that race startTrigger's ffmpeg,
        // and the pause-trigger guard can't help because process is
        // still nil at the time refreshSyncStatus decides. Letting
        // autoConnectSyncIfPaired own the first probe (it runs AFTER
        // startTrigger, so its pause-trigger guard does work) avoids
        // the race.
        if didInitialAutoConnect {
            refreshSyncStatus()
        }
    }

    /// Set to true once the launch-time `autoConnectSyncIfPaired` has
    /// actually dispatched probes (i.e. we're past the early-launch
    /// window where the trigger hasn't yet been given a chance to run).
    private var didInitialAutoConnect = false

    // MARK: - Transcript Viewer

    private func openTranscriptViewer(transcriptMdPath: String) {
        // Derive _diarized.json path from the .md path
        let mdURL = URL(fileURLWithPath: transcriptMdPath)
        let baseName = mdURL.deletingPathExtension().lastPathComponent
        let dirURL = mdURL.deletingLastPathComponent()
        let diarizedPath = dirURL.appendingPathComponent(baseName + "_diarized.json").path

        // Try to load the segments JSON for in-app viewing
        var transcript: DiarizedTranscript?
        if FileManager.default.fileExists(atPath: diarizedPath),
           let data = FileManager.default.contents(atPath: diarizedPath) {
            do {
                transcript = try JSONDecoder().decode(DiarizedTranscript.self, from: data)
            } catch {
                log("Failed to decode transcript JSON at \(diarizedPath): \(error.localizedDescription)")
            }
        }

        guard let transcript = transcript else {
            // No segments JSON — fall back to revealing the .md in Finder
            log("No segments JSON found at \(diarizedPath), opening .md in Finder")
            NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: transcriptMdPath)])
            return
        }

        // Derive audio path from the transcript's audioFile field
        let audioPath: String
        if transcript.audioFile.hasPrefix("/") {
            audioPath = transcript.audioFile
        } else {
            audioPath = dirURL.appendingPathComponent(transcript.audioFile).path
        }

        let viewer = TranscriptViewerView(
            transcript: transcript,
            filePath: diarizedPath,
            audioPath: audioPath,
            onEnrollSpeaker: { [weak self] name, audio, start, end in
                self?.enrollSpeakerInVoiceLibrary(name: name, audioPath: audio, start: start, end: end)
            },
            onRediarize: { [weak self] jsonPath, nSpeakers in
                self?.rediarizeTranscript(jsonPath: jsonPath, nSpeakers: nSpeakers)
            }
        )

        let win = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 800, height: 600),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        win.center()
        win.title = "Transcript — \(transcript.audioFile)"
        win.isReleasedWhenClosed = false
        win.minSize = NSSize(width: 600, height: 400)
        win.contentView = NSHostingView(rootView: viewer)

        transcriptViewerWindow = win
        win.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    // MARK: - Voice Library

    // MARK: - Cowork Prompt

    // MARK: - Voice Training

    private func showVoiceTraining() {
        if let existing = voiceTrainingWindow, existing.isVisible {
            existing.makeKeyAndOrderFront(nil)
            return
        }

        let view = VoiceTrainingView(
            onEnroll: { [weak self] name, audioPath, start, end in
                self?.enrollSpeakerInVoiceLibrary(name: name, audioPath: audioPath, start: start, end: end)
            },
            onRefresh: { [weak self] completion in
                self?.loadVoiceTrainingData(completion: completion)
            }
        )

        let win = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 700, height: 550),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        win.center()
        win.title = "Voice Training"
        win.isReleasedWhenClosed = false
        win.minSize = NSSize(width: 600, height: 400)
        win.contentView = NSHostingView(rootView: view)
        win.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        voiceTrainingWindow = win
    }

    private func loadVoiceTrainingData(completion: @escaping ([VoiceClusterData]) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            let process = Process()
            process.currentDirectoryURL = URL(fileURLWithPath: self.repoRoot)
            process.executableURL = URL(fileURLWithPath: self.transcriptionPythonPath)
            process.arguments = ["\(self.repoRoot)/shared/voice_training.py"]

            var env = ProcessInfo.processInfo.environment
            env["HOME"] = NSHomeDirectory()
            env["PYTHONPATH"] = self.repoRoot
            if env["PATH"] == nil || !env["PATH"]!.contains("/opt/homebrew") {
                env["PATH"] = "\(NSHomeDirectory())/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
            } else if let existing = env["PATH"], !existing.contains("/.local/bin") {
                env["PATH"] = "\(NSHomeDirectory())/.local/bin:" + existing
            }
            process.environment = env

            let pipe = Pipe()
            process.standardOutput = pipe
            process.standardError = Pipe()

            do {
                try process.run()
                process.waitUntilExit()
            } catch {
                DispatchQueue.main.async { completion([]) }
                return
            }

            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            guard process.terminationStatus == 0,
                  let clusters = try? JSONDecoder().decode([VoiceClusterData].self, from: data) else {
                self.log("Voice training: failed to decode clusters (\(data.count) bytes)")
                DispatchQueue.main.async { completion([]) }
                return
            }

            DispatchQueue.main.async { completion(clusters) }
        }
    }

    private func showCoworkPrompt() {
        if let existing = coworkPromptWindow, existing.isVisible {
            existing.makeKeyAndOrderFront(nil)
            return
        }

        let view = CoworkPromptView()
        let hostingView = NSHostingView(rootView: view)
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 640, height: 520),
            styleMask: [.titled, .closable, .resizable],
            backing: .buffered,
            defer: false
        )
        window.contentView = hostingView
        window.title = "Cowork Setup"
        window.center()
        window.isReleasedWhenClosed = false
        window.makeKeyAndOrderFront(nil)
        coworkPromptWindow = window
    }

    @objc private func openVoiceLibraryMenu() {
        openVoiceLibrary()
    }

    @objc private func openVoiceTrainingMenu() {
        showVoiceTraining()
    }

    private func openVoiceLibrary() {
        // Shell out to voice_library_lite.py list to get speakers
        let sharedDir: String
        if let root = bundledResourcesRoot {
            sharedDir = "\(root)/shared"
        } else {
            sharedDir = "\(repoRoot)/shared"
        }

        let scriptPath = "\(sharedDir)/voice_library_lite.py"
        let pythonPath: String
        if FileManager.default.isExecutableFile(atPath: "\(sharedDir)/../transcription-pipeline/.venv/bin/python3") {
            pythonPath = "\(sharedDir)/../transcription-pipeline/.venv/bin/python3"
        } else if FileManager.default.isExecutableFile(atPath: "\(sharedDir)/../usb-extractor/.venv/bin/python3") {
            pythonPath = "\(sharedDir)/../usb-extractor/.venv/bin/python3"
        } else {
            pythonPath = "/usr/bin/python3"
        }

        var speakers: [VoiceLibrarySpeaker] = []

        if FileManager.default.fileExists(atPath: scriptPath) {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: pythonPath)
            process.arguments = [scriptPath, "list"]
            let pipe = Pipe()
            process.standardOutput = pipe
            process.standardError = Pipe()
            do {
                try process.run()
                process.waitUntilExit()
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                if let parsed = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] {
                    speakers = parsed.compactMap { dict in
                        guard let name = dict["name"] as? String else { return nil }
                        return VoiceLibrarySpeaker(
                            id: name,
                            name: name,
                            sampleCount: dict["sample_count"] as? Int ?? 0,
                            lastUpdated: dict["last_updated"] as? String ?? ""
                        )
                    }
                }
            } catch {
                log("Failed to list voice library: \(error)")
            }
        }

        let libraryView = VoiceLibraryView(
            speakers: speakers,
            onDelete: { [weak self] name in
                self?.deleteVoiceLibrarySpeaker(name: name)
            },
            onRename: { [weak self] oldName, newName in
                self?.renameVoiceLibrarySpeaker(oldName: oldName, newName: newName)
            }
        )

        let win = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 500, height: 400),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        win.center()
        win.title = "Voice Library"
        win.isReleasedWhenClosed = false
        win.minSize = NSSize(width: 400, height: 300)
        win.contentView = NSHostingView(rootView: libraryView)

        voiceLibraryWindow = win
        win.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func voiceLibraryPythonPath() -> String {
        let sharedDir: String
        if let root = bundledResourcesRoot {
            sharedDir = "\(root)/shared"
        } else {
            sharedDir = "\(repoRoot)/shared"
        }
        if FileManager.default.isExecutableFile(atPath: "\(sharedDir)/../transcription-pipeline/.venv/bin/python3") {
            return "\(sharedDir)/../transcription-pipeline/.venv/bin/python3"
        } else if FileManager.default.isExecutableFile(atPath: "\(sharedDir)/../usb-extractor/.venv/bin/python3") {
            return "\(sharedDir)/../usb-extractor/.venv/bin/python3"
        }
        return "/usr/bin/python3"
    }

    /// Save an .srt subtitle file for a transcript.
    ///
    /// Prefers the paired `.srt` that `transcribe.py` now auto-emits alongside
    /// the `.md`. For legacy transcripts that predate auto-emit, regenerates
    /// from the `_diarized.json` / `_whisper.json` sidecar via the shared
    /// `srt_writer` CLI. Shows an alert if no timed segments exist.
    private func exportSRT(transcriptMdPath: String) {
        let mdURL = URL(fileURLWithPath: transcriptMdPath)
        let pairedSRT = mdURL.deletingPathExtension().appendingPathExtension("srt")
        let stem = mdURL.deletingPathExtension().lastPathComponent
        let dir = mdURL.deletingLastPathComponent()
        let diarizedJSON = dir.appendingPathComponent("\(stem)_diarized.json")
        let whisperJSON = dir.appendingPathComponent("\(stem)_whisper.json")

        let savePanel = NSSavePanel()
        savePanel.allowedContentTypes = [.init(filenameExtension: "srt") ?? .data]
        savePanel.nameFieldStringValue = "\(stem).srt"
        savePanel.title = "Export SRT"
        savePanel.prompt = "Export"

        guard savePanel.runModal() == .OK, let destURL = savePanel.url else { return }

        let fm = FileManager.default
        do {
            if fm.fileExists(atPath: pairedSRT.path) {
                // Fast path: just copy the .srt that transcribe.py already wrote.
                if fm.fileExists(atPath: destURL.path) {
                    try fm.removeItem(at: destURL)
                }
                try fm.copyItem(at: pairedSRT, to: destURL)
            } else {
                // Legacy: regenerate from whichever sidecar we have.
                let sourceJSON: URL
                if fm.fileExists(atPath: diarizedJSON.path) {
                    sourceJSON = diarizedJSON
                } else if fm.fileExists(atPath: whisperJSON.path) {
                    sourceJSON = whisperJSON
                } else {
                    showSRTErrorAlert(message: "No timed segments are available for this transcript. Re-transcribe it to generate an SRT.")
                    return
                }

                let sharedDir = bundledResourcesRoot.map { "\($0)/shared" } ?? "\(repoRoot)/shared"
                let process = Process()
                process.executableURL = URL(fileURLWithPath: voiceLibraryPythonPath())
                process.arguments = ["-m", "shared.srt_writer", sourceJSON.path, destURL.path]
                var env = ProcessInfo.processInfo.environment
                env["PYTHONPATH"] = "\(sharedDir)/.." + (env["PYTHONPATH"].map { ":\($0)" } ?? "")
                process.environment = env
                let errPipe = Pipe()
                process.standardError = errPipe
                try process.run()
                process.waitUntilExit()
                if process.terminationStatus != 0 {
                    let err = String(data: errPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
                    showSRTErrorAlert(message: err.isEmpty ? "SRT generation failed." : err)
                    return
                }
            }
            NSWorkspace.shared.activateFileViewerSelecting([destURL])
        } catch {
            showSRTErrorAlert(message: "Could not write SRT: \(error.localizedDescription)")
        }
    }

    private func showSRTErrorAlert(message: String) {
        let alert = NSAlert()
        alert.messageText = "Export as SRT"
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.runModal()
    }

    private func voiceLibraryScriptPath() -> String {
        let sharedDir: String
        if let root = bundledResourcesRoot {
            sharedDir = "\(root)/shared"
        } else {
            sharedDir = "\(repoRoot)/shared"
        }
        return "\(sharedDir)/voice_library_lite.py"
    }

    private func enrollSpeakerInVoiceLibrary(name: String, audioPath: String, start: Double, end: Double) {
        let scriptPath = voiceLibraryScriptPath()
        guard FileManager.default.fileExists(atPath: scriptPath) else { return }

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let process = Process()
            process.executableURL = URL(fileURLWithPath: self?.voiceLibraryPythonPath() ?? "/usr/bin/python3")
            process.arguments = [
                scriptPath, "enroll",
                "--name", name,
                "--audio", audioPath,
                "--start", String(start),
                "--end", String(end)
            ]
            process.standardOutput = Pipe()
            process.standardError = Pipe()
            do {
                try process.run()
                process.waitUntilExit()
                DispatchQueue.main.async {
                    self?.log("Enrolled speaker '\(name)' in voice library")
                }
            } catch {
                DispatchQueue.main.async {
                    self?.log("Failed to enroll speaker '\(name)': \(error)")
                }
            }
        }
    }

    private func deleteVoiceLibrarySpeaker(name: String) {
        let scriptPath = voiceLibraryScriptPath()
        guard FileManager.default.fileExists(atPath: scriptPath) else { return }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: voiceLibraryPythonPath())
        process.arguments = [scriptPath, "delete", "--name", name]
        process.standardOutput = Pipe()
        process.standardError = Pipe()
        do {
            try process.run()
            process.waitUntilExit()
            log("Deleted speaker '\(name)' from voice library")
        } catch {
            log("Failed to delete speaker '\(name)': \(error)")
        }
    }

    private func renameVoiceLibrarySpeaker(oldName: String, newName: String) {
        let scriptPath = voiceLibraryScriptPath()
        guard FileManager.default.fileExists(atPath: scriptPath) else { return }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: voiceLibraryPythonPath())
        process.arguments = [scriptPath, "rename", "--old", oldName, "--new", newName]
        process.standardOutput = Pipe()
        process.standardError = Pipe()
        do {
            try process.run()
            process.waitUntilExit()
            log("Renamed speaker '\(oldName)' to '\(newName)' in voice library")
        } catch {
            log("Failed to rename speaker '\(oldName)': \(error)")
        }
    }

    // MARK: - Terminal

    @objc private func openTerminalMenu() {
        openTerminal(initialCommand: nil)
    }

    // MARK: - File menu bridge

    /// Each of these is a thin wrapper that calls the same action the
    /// toolbar buttons used to call. Keeping them as separate @objc
    /// methods so the menu items have stable selectors and the
    /// underlying flows stay unchanged.
    @objc private func fileMenuPair() { viewModel.onPairDock() }
    @objc private func fileMenuUnpair() { viewModel.onUnpairDock() }
    @objc private func fileMenuOpenDeviceManager() { openDeviceManager() }
    @objc private func fileMenuChooseRecordingsFolder() { viewModel.onChooseRecordingsFolder() }
    @objc private func fileMenuChooseTranscriptsFolder() { viewModel.onChooseTranscriptFolder() }
    @objc private func fileMenuToggleDiarize() {
        viewModel.onToggleDiarize()
        // Reflect the flipped state on the menu item so the checkmark
        // tracks reality without rebuilding the whole menu.
        speakerLabelsMenuItem?.state = diarizeEnabled ? .on : .off
    }

    /// Open an embedded PTY terminal window. Optionally start with a command
    /// pre-filled (e.g. `claude auth login`) — the shell drops back to an
    /// interactive prompt once the command completes so the user can keep
    /// typing.
    func openTerminal(initialCommand: String? = nil) {
        if let existing = terminalWindow, existing.isVisible {
            existing.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }

        let view = EmbeddedTerminalView(initialCommand: initialCommand)

        let win = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 820, height: 500),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        win.center()
        win.title = "Terminal"
        win.isReleasedWhenClosed = false
        win.minSize = NSSize(width: 600, height: 320)
        win.contentView = NSHostingView(rootView: view)

        terminalWindow = win
        win.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    // MARK: - Firmware

    /// HiDock firmware updates are distributed through the vendor's HiNotes
    /// app and their firmwares page. We don't have a device-side protocol
    /// command for querying version or triggering an OTA — the USB protocol
    /// we've implemented only covers file listing and transfer. This menu
    /// item provides the clearest path: open the vendor's firmwares page
    /// so the user can check what's current and grab HiNotes if they want
    /// to apply it.
    @objc private func openFirmwarePage() {
        guard let url = URL(string: "https://www.hidock.com/pages/firmwares") else { return }
        NSWorkspace.shared.open(url)
    }

    // MARK: - Import Audio File

    @objc private func importAudioFileMenu() {
        importAudioFile()
    }

    /// Present a file picker and import the chosen audio/video file into
    /// `~/HiDock/Recordings/`. On success the new entry is persisted in
    /// `imported_recordings.json` and added to the recordings table under
    /// a virtual "Imported" device, ready to download-less transcribe.
    private func importAudioFile() {
        let panel = NSOpenPanel()
        panel.title = "Import audio file"
        panel.prompt = "Import"
        panel.message = "Choose an audio or video file to import. ffmpeg extracts the audio track automatically for video formats."
        panel.allowedContentTypes = IMPORT_ALLOWED_EXTENSIONS.compactMap {
            UTType(filenameExtension: $0)
        }
        panel.allowsMultipleSelection = true
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.resolvesAliases = true

        guard panel.runModal() == .OK, !panel.urls.isEmpty else { return }

        guard let recordingsFolder = syncOutputFolder ?? defaultRecordingsFolder() else {
            log("importAudioFile: no recordings folder configured")
            return
        }
        let recordingsURL = URL(fileURLWithPath: recordingsFolder)
        try? FileManager.default.createDirectory(
            at: recordingsURL, withIntermediateDirectories: true
        )

        var added = 0
        for source in panel.urls {
            if let entry = importSingleFile(source, into: recordingsURL) {
                importedRecordings.append(entry)
                added += 1
            }
        }
        if added > 0 {
            ImportedRecordingsStore.save(importedRecordings)
            rebuildSyncEntries()
            viewModel.syncStatus = "Imported \(added) file\(added == 1 ? "" : "s")"
            viewModel.syncStatusLevel = .success
            syncViewModelState()
            refreshTranscriptionState()
        }
    }

    /// Copy a single source file into the recordings folder, gather its
    /// basic metadata, and return a persistable ImportedRecordingEntry.
    private func importSingleFile(
        _ source: URL, into recordingsURL: URL,
    ) -> ImportedRecordingEntry? {
        // Read the source's modification time first so we can use it as the
        // creation date for naming. This keeps the HiDock-style filename
        // prefix (YYYYMonDD-HHMMSS) meaningful instead of just "now".
        let sourceAttrs = (try? FileManager.default.attributesOfItem(atPath: source.path)) ?? [:]
        let sourceMtime = (sourceAttrs[.modificationDate] as? Date) ?? Date()

        let destName = ImportedRecordingsStore.uniqueDestinationName(
            for: source, createdAt: sourceMtime, in: recordingsURL
        )
        let destURL = recordingsURL.appendingPathComponent(destName)

        do {
            try FileManager.default.copyItem(at: source, to: destURL)
        } catch {
            log("importSingleFile: failed to copy \(source.path) → \(destURL.path): \(error.localizedDescription)")
            return nil
        }

        let attrs = (try? FileManager.default.attributesOfItem(atPath: destURL.path)) ?? [:]
        let size = (attrs[.size] as? Int) ?? 0
        let mtime = sourceMtime

        // Probe duration via AVFoundation — works on every format ffmpeg
        // handles (mp3, wav, m4a, flac, ogg, mp4, mov). ~100ms for a
        // 400 MB WAV on Apple Silicon.
        let duration = ImportedRecordingsStore.probeDuration(at: destURL.path)

        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime]

        log("Imported \(source.lastPathComponent) → \(destName) (\(size / 1_048_576) MB, \(Int(duration))s)")
        return ImportedRecordingEntry(
            name: destName,
            outputPath: destURL.path,
            originalPath: source.path,
            length: size,
            duration: duration,
            createdAt: iso.string(from: mtime),
            importedAt: iso.string(from: Date())
        )
    }

    private func defaultRecordingsFolder() -> String? {
        "\(NSHomeDirectory())/HiDock/Recordings"
    }

    /// Rebuild syncEntries by merging device-reported recordings with
    /// persisted imported recordings. Called after each import and on
    /// startup so the table always shows both sources.
    private func mergeImportedIntoSyncEntries() {
        // Remove any existing imported entries, then append fresh ones from
        // the persisted list. This keeps device-reported entries untouched.
        syncEntries.removeAll { $0.deviceId == IMPORTED_DEVICE_ID }
        let stableImportedPid = Int(truncatingIfNeeded: IMPORTED_DEVICE_ID.hashValue)
        for entry in importedRecordings {
            let rec = ImportedRecordingsStore.asSyncRecording(entry)
            let sync = HiDockSyncRecordingEntry(
                recording: rec,
                deviceProductId: stableImportedPid,
                deviceId: IMPORTED_DEVICE_ID,
                deviceName: IMPORTED_DEVICE_NAME
            )
            syncEntries.append(sync)
        }
    }

    /// Called by importAudioFile + on startup to refresh the table.
    private func rebuildSyncEntries() {
        mergeImportedIntoSyncEntries()
        viewModel.syncEntries = syncEntries
    }

    /// Queue a single recording for transcription with a hint that the
    /// speaker count is known. Bypasses the automatic estimator inside
    /// diarize_lite, which caps at 2 speakers for quiet group recordings
    /// where VAD is sparse. The hint flows all the way through to
    /// clustering as a fixed k.
    func transcribeWithSpeakerCount(name: String, nSpeakers: Int) {
        guard let entry = syncEntries.first(where: { $0.recording.name == name }) else {
            log("transcribeWithSpeakerCount: no entry named \(name)")
            return
        }
        guard entry.recording.downloaded && entry.recording.localExists else {
            log("transcribeWithSpeakerCount: \(name) not downloaded yet")
            return
        }
        guard ensureTranscriptionReady() else { return }

        var args = ["transcribe", entry.recording.outputPath]
        if diarizeEnabled { args.append("--diarize") }
        args.append("--summarize")
        args.append("--n-speakers")
        args.append("\(nSpeakers)")

        let timeout = Self.computeTranscriptionTimeout(
            for: entry.recording.outputPath,
            knownDuration: entry.recording.duration
        )

        log("Transcribing \(name) with --n-speakers \(nSpeakers), timeout=\(Int(timeout))s")
        viewModel.syncStatus = "Transcribing \(name) with \(nSpeakers) speakers..."
        viewModel.syncStatusLevel = .secondary
        syncViewModelState()

        runTranscription(arguments: args, timeout: timeout) { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success:
                self.log("Transcription complete (n_speakers=\(nSpeakers)): \(name)")
                self.viewModel.syncStatus = "Transcribed \(name)"
                self.viewModel.syncStatusLevel = .success
            case .failure(let err):
                self.log("Transcription failed for \(name): \(err.localizedDescription)")
                self.viewModel.syncStatus = "Transcription failed: \(err.localizedDescription)"
                self.viewModel.syncStatusLevel = .error
            }
            self.refreshTranscriptionState()
            self.syncViewModelState()
        }
    }

    /// Delete the downloaded MP3 for a HiDock recording while leaving the
    /// on-device copy intact. After this, Refresh will show the entry as
    /// 'On device' again. Useful for reclaiming disk space without touching
    /// the device — which is all we can safely do today, since the HiDock
    /// USB protocol commands we've implemented don't include a delete
    /// operation (that would need protocol reverse-engineering).
    func deleteLocalCopy(name: String) {
        guard let entry = syncEntries.first(where: { $0.recording.name == name }) else {
            log("deleteLocalCopy: no entry named \(name)")
            return
        }
        // Never destructive on imports — they don't have a device copy to
        // fall back to. Use Remove Import for those.
        guard entry.deviceId != IMPORTED_DEVICE_ID else {
            log("deleteLocalCopy: refusing to delete an imported entry (use Remove Import)")
            viewModel.syncStatus = "Use Remove Import for imported recordings"
            viewModel.syncStatusLevel = .warning
            syncViewModelState()
            return
        }
        let path = entry.recording.outputPath
        guard FileManager.default.fileExists(atPath: path) else {
            log("deleteLocalCopy: file already absent at \(path)")
            viewModel.syncStatus = "Local copy already absent"
            viewModel.syncStatusLevel = .info
            syncViewModelState()
            return
        }

        // Confirmation — this is destructive on disk even though the
        // device copy survives.
        let alert = NSAlert()
        alert.messageText = "Delete local copy of \(entry.recording.outputName)?"
        alert.informativeText = "The MP3 will be removed from \(entry.recording.outputPath). The recording stays on the HiDock and can be re-downloaded any time."
        alert.alertStyle = .warning
        alert.addButton(withTitle: "Delete Local Copy")
        alert.addButton(withTitle: "Cancel")
        guard alert.runModal() == .alertFirstButtonReturn else { return }

        do {
            try FileManager.default.removeItem(atPath: path)
            // Also unmark in the extractor state so the device catalogue
            // reports it as "not downloaded" again, matching reality.
            let device = syncPairedDevices.first { $0.deviceId == entry.deviceId }
            var args: [String]
            let pid: Int?
            if let device = device, device.deviceType == .volume {
                args = ["unmark-downloaded", "--volume-name", device.volumeName ?? "", entry.recording.name]
                pid = nil
            } else {
                args = ["unmark-downloaded", entry.recording.name]
                pid = device?.productId
            }
            runExtractor(arguments: args, productId: pid) { [weak self] _ in
                DispatchQueue.main.async {
                    self?.refreshSyncStatus()
                }
            }
            log("deleteLocalCopy: unlinked \(path)")
            viewModel.syncStatus = "Deleted local copy of \(entry.recording.outputName)"
            viewModel.syncStatusLevel = .success
            syncViewModelState()
        } catch {
            log("deleteLocalCopy: failed — \(error.localizedDescription)")
            showError("Failed to delete local copy:\n\(error.localizedDescription)")
        }
    }

    /// Re-query a single device's status, bypassing the usual batch flow
    /// and clearing any stale 'unreachable' flag before we try.
    ///
    /// Does NOT stop the mic trigger. Historically we paused ffmpeg
    /// before probing, waited 800ms, probed, then restarted the trigger.
    /// That stop-probe-restart dance was the biggest cause of the H1
    /// firmware wedging — every ffmpeg termination mid-stream risks
    /// putting the HiDock into a state where subsequent probes hang
    /// for 30s, which we'd then misinterpret as the device being
    /// physically stuck. The vendor-specific USB interface we probe
    /// (bInterfaceClass=255) is a separate interface from the USB
    /// audio class ffmpeg uses, so they can coexist; the pyusb claim
    /// only affects the vendor interface. Keep the trigger running.
    func reconnectDevice(deviceId: String) {
        guard let device = syncPairedDevices.first(where: { $0.deviceId == deviceId }) else {
            log("reconnectDevice: no paired device with id \(deviceId)")
            return
        }
        log("reconnectDevice: \(device.shortName) (\(deviceId))")

        syncDeviceLastError.removeValue(forKey: deviceId)
        // Manual reconnect always retries, even if the device is in hung
        // backoff — the user asked for it explicitly by clicking ↻.
        syncDeviceHungUntil.removeValue(forKey: deviceId)
        viewModel.syncStatus = "Reconnecting \(device.shortName)..."
        viewModel.syncStatusLevel = .info
        syncViewModelState()

        runReconnectProbe(device: device, restartTriggerAfter: false)
    }

    private func runReconnectProbe(
        device: HiDockPairedDevice, restartTriggerAfter: Bool,
    ) {
        let args: [String]
        let pid: Int?
        if device.deviceType == .volume {
            args = ["volume-status", "--volume-name", device.volumeName ?? "", "--timeout-ms", "5000"]
            pid = nil
        } else {
            args = ["status", "--timeout-ms", "5000"]
            pid = device.productId
        }

        runExtractor(arguments: args, productId: pid) { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success(let data):
                do {
                    let payload = try JSONDecoder().decode(HiDockSyncStatusResponse.self, from: data)
                    self.renderSyncStatus(payload, device: device)
                    self.viewModel.syncStatus = "\(device.shortName) reconnected"
                    self.viewModel.syncStatusLevel = .success
                    self.refreshTranscriptionState()
                } catch {
                    self.viewModel.syncStatus = "\(device.shortName): decode error"
                    self.viewModel.syncStatusLevel = .error
                    self.syncDeviceLastError[device.deviceId] = ("decode error: \(error.localizedDescription)", Date())
                }
            case .failure(let error):
                let desc = error.localizedDescription
                let shortDesc = desc.components(separatedBy: "\n").last(where: { !$0.isEmpty }) ?? desc
                self.log("reconnectDevice[\(device.shortName)] failed: \(shortDesc)")
                self.syncDeviceLastError[device.deviceId] = (shortDesc, Date())
                self.syncDeviceConnected[device.deviceId] = false
                self.viewModel.syncStatus = "\(device.shortName): still unreachable — \(shortDesc)"
                self.viewModel.syncStatusLevel = .error
            }
            // Restart the mic trigger we stopped so the user doesn't
            // silently lose recording capability after a reconnect probe.
            if restartTriggerAfter {
                self.log("reconnectDevice: restarting mic trigger")
                self.startTrigger()
            }
            self.syncViewModelState()
        }
    }

    /// Unified Remove for the currently-checked selection. Handles mixed
    /// selections sensibly: imports are removed entirely (file + JSON),
    /// downloaded HiDock recordings have their local MP3 deleted but the
    /// device copy is preserved. Shows a single confirmation listing the
    /// total damage.
    func removeSelected() {
        let entries = selectedSyncEntries()
        guard !entries.isEmpty else {
            viewModel.syncStatus = "Remove: no recordings selected"
            viewModel.syncStatusLevel = .warning
            syncViewModelState()
            return
        }

        let imports = entries.filter { $0.deviceId == IMPORTED_DEVICE_ID }
        let localCopies = entries.filter {
            $0.deviceId != IMPORTED_DEVICE_ID
            && $0.recording.downloaded
            && $0.recording.localExists
        }
        let nothingToDo = entries.count - imports.count - localCopies.count

        guard !imports.isEmpty || !localCopies.isEmpty else {
            viewModel.syncStatus = "Remove: \(nothingToDo) selected recording(s) have no local copy to remove"
            viewModel.syncStatusLevel = .warning
            syncViewModelState()
            return
        }

        let alert = NSAlert()
        alert.messageText = "Remove \(imports.count + localCopies.count) recording(s)?"
        var info: [String] = []
        if !imports.isEmpty {
            info.append("\(imports.count) imported file(s) will be deleted entirely.")
        }
        if !localCopies.isEmpty {
            info.append("\(localCopies.count) local MP3 file(s) will be deleted. Device copies are preserved and can be re-downloaded.")
        }
        if nothingToDo > 0 {
            info.append("\(nothingToDo) selected item(s) have nothing to remove (on-device only) and will be ignored.")
        }
        alert.informativeText = info.joined(separator: "\n\n")
        alert.alertStyle = .warning
        alert.addButton(withTitle: "Remove")
        alert.addButton(withTitle: "Cancel")
        guard alert.runModal() == .alertFirstButtonReturn else { return }

        // Imports: drop file + JSON entry. Skip the per-item confirmation
        // since we just got a bulk one.
        for entry in imports {
            let name = entry.recording.name
            if let im = importedRecordings.first(where: { $0.name == name }) {
                try? FileManager.default.removeItem(atPath: im.outputPath)
            }
            importedRecordings.removeAll { $0.name == name }
            syncCheckedRecordings.remove(name)
        }
        if !imports.isEmpty {
            ImportedRecordingsStore.save(importedRecordings)
        }

        // HiDock local copies: delete MP3, unmark so catalogue reflects it.
        let group = DispatchGroup()
        for entry in localCopies {
            try? FileManager.default.removeItem(atPath: entry.recording.outputPath)
            let device = syncPairedDevices.first { $0.deviceId == entry.deviceId }
            var args: [String]
            let pid: Int?
            if let device = device, device.deviceType == .volume {
                args = ["unmark-downloaded", "--volume-name", device.volumeName ?? "", entry.recording.name]
                pid = nil
            } else {
                args = ["unmark-downloaded", entry.recording.name]
                pid = device?.productId
            }
            group.enter()
            runExtractor(arguments: args, productId: pid) { _ in group.leave() }
        }

        group.notify(queue: .main) { [weak self] in
            guard let self = self else { return }
            self.syncCheckedRecordings.removeAll()
            let total = imports.count + localCopies.count
            self.viewModel.syncStatus = "Removed \(total) recording(s)"
            self.viewModel.syncStatusLevel = .success
            self.rebuildSyncEntries()
            self.refreshSyncStatus()
        }
    }

    /// Remove an imported recording by filename — unlinks the audio file
    /// from ~/HiDock/Recordings/, drops the entry from the JSON, and
    /// refreshes the table. No-op for non-imported names.
    func removeImportedRecording(name: String) {
        guard let entry = importedRecordings.first(where: { $0.name == name }) else {
            log("removeImportedRecording: no entry named \(name)")
            return
        }
        try? FileManager.default.removeItem(atPath: entry.outputPath)
        importedRecordings.removeAll { $0.name == name }
        ImportedRecordingsStore.save(importedRecordings)
        syncCheckedRecordings.remove(name)
        rebuildSyncEntries()
        syncViewModelState()
        log("Removed import: \(name)")
    }

    // MARK: - Device Manager

    private func openDeviceManager() {
        if let existing = deviceManagerWindow, existing.isVisible {
            existing.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }

        let managerView = DeviceManagerView(viewModel: viewModel)

        let win = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 620, height: 480),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        win.center()
        win.title = "Device Manager"
        win.isReleasedWhenClosed = false
        win.minSize = NSSize(width: 560, height: 400)
        win.contentView = NSHostingView(rootView: managerView)

        deviceManagerWindow = win
        win.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func forgetDevice(_ device: HiDockPairedDevice) {
        var devices = syncPairedDevices
        devices.removeAll { $0.deviceId == device.deviceId }
        syncPairedDevices = devices
        syncDeviceConnected.removeValue(forKey: device.deviceId)
        viewModel.syncPairedDevices = syncPairedDevices
        viewModel.syncDeviceConnected = syncDeviceConnected
        viewModel.syncDeviceStorage = syncDeviceStorage
        viewModel.syncDeviceLastError = syncDeviceLastError
        viewModel.syncDeviceLastOK = syncDeviceLastOK
        viewModel.syncPaired = !syncPairedDevices.isEmpty
        log("Forgot device: \(device.cleanName) (\(device.deviceId))")
        updateMenuSyncStatus(connected: syncDeviceConnected.values.contains(true))

        // Remove entries from this device
        syncEntries.removeAll { $0.deviceId == device.deviceId }
        viewModel.syncEntries = syncEntries
    }

    private func pairVolume(volumeName: String, subpath: String?) {
        let device = HiDockPairedDevice(volumeName: volumeName, displayName: volumeName, subpath: subpath)

        // Check for duplicate
        if syncPairedDevices.contains(where: { $0.deviceId == device.deviceId }) {
            log("Volume '\(volumeName)' is already paired")
            return
        }

        var devices = syncPairedDevices
        devices.append(device)
        syncPairedDevices = devices
        viewModel.syncPairedDevices = syncPairedDevices
        viewModel.syncPaired = true
        log("Paired volume: \(volumeName) (subpath: \(subpath ?? "none"))")
    }

    private func scanVolumes(completion: @escaping ([VolumeScanResult]) -> Void) {
        guard ensureExtractorReady() else {
            completion([])
            return
        }
        runExtractor(arguments: ["scan-volumes"]) { [weak self] result in
            guard self != nil else { completion([]); return }
            switch result {
            case .success(let data):
                if let response = try? JSONDecoder().decode(VolumeScanResponse.self, from: data) {
                    completion(response.volumes)
                } else {
                    completion([])
                }
            case .failure:
                completion([])
            }
        }
    }

    // MARK: - Model Manager

    @objc private func openModelManagerMenu() {
        openModelManager()
    }

    private func openModelManager() {
        refreshModelStatuses()

        let managerView = ModelManagerView(viewModel: viewModel)

        let win = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 520, height: 400),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        win.center()
        win.title = "Models"
        win.isReleasedWhenClosed = false
        win.minSize = NSSize(width: 480, height: 300)
        win.contentView = NSHostingView(rootView: managerView)

        modelManagerWindow = win
        win.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func modelsScriptPath() -> String {
        let sharedDir: String
        if let root = bundledResourcesRoot {
            sharedDir = "\(root)/shared"
        } else {
            sharedDir = "\(repoRoot)/shared"
        }
        return "\(sharedDir)/models.py"
    }

    private func modelsPythonPath() -> String {
        // Reuse the same Python discovery as voice library
        return voiceLibraryPythonPath()
    }

    private func refreshModelStatuses() {
        let scriptPath = modelsScriptPath()
        let pythonPath = modelsPythonPath()

        guard FileManager.default.fileExists(atPath: scriptPath) else {
            log("Models script not found at \(scriptPath)")
            return
        }

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let process = Process()
            process.executableURL = URL(fileURLWithPath: pythonPath)
            process.arguments = [scriptPath, "status"]
            let pipe = Pipe()
            process.standardOutput = pipe
            process.standardError = Pipe()

            do {
                try process.run()
                process.waitUntilExit()
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                if let parsed = try? JSONSerialization.jsonObject(with: data) as? [String: [String: Any]] {
                    DispatchQueue.main.async {
                        guard let self = self else { return }
                        var statuses: [String: ModelStatus] = [:]
                        for (key, info) in parsed {
                            statuses[key] = ModelStatus(
                                id: key,
                                name: info["name"] as? String ?? key,
                                description: info["description"] as? String ?? "",
                                sizeMB: info["size_mb"] as? Int ?? 0,
                                installed: info["installed"] as? Bool ?? false
                            )
                        }
                        self.viewModel.modelStatuses = statuses
                    }
                }
            } catch {
                self?.log("Failed to get model statuses: \(error)")
            }
        }
    }

    private func downloadModelByKey(_ key: String) {
        let scriptPath = modelsScriptPath()
        let pythonPath = modelsPythonPath()

        guard FileManager.default.fileExists(atPath: scriptPath) else { return }

        // Mark as downloading
        DispatchQueue.main.async { [weak self] in
            self?.viewModel.modelStatuses[key]?.downloading = true
            self?.viewModel.modelStatuses[key]?.progress = 0
        }

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let process = Process()
            process.executableURL = URL(fileURLWithPath: pythonPath)
            process.arguments = [scriptPath, "download", key]
            let outPipe = Pipe()
            let errPipe = Pipe()
            process.standardOutput = outPipe
            process.standardError = errPipe

            // Read stderr for progress updates
            errPipe.fileHandleForReading.readabilityHandler = { handle in
                let data = handle.availableData
                guard !data.isEmpty, let line = String(data: data, encoding: .utf8) else { return }
                // Parse percentage from lines like "  42%"
                let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
                if let pctStr = trimmed.components(separatedBy: "%").first?.trimmingCharacters(in: .whitespaces),
                   let pct = Double(pctStr) {
                    DispatchQueue.main.async {
                        self?.viewModel.modelStatuses[key]?.progress = pct / 100.0
                    }
                }
            }

            do {
                try process.run()
                process.waitUntilExit()
                errPipe.fileHandleForReading.readabilityHandler = nil

                DispatchQueue.main.async {
                    self?.viewModel.modelStatuses[key]?.downloading = false
                    if process.terminationStatus == 0 {
                        self?.viewModel.modelStatuses[key]?.installed = true
                        self?.viewModel.modelStatuses[key]?.progress = 1.0
                        // Also update modelReady if whisper was downloaded
                        if key == "whisper" {
                            self?.viewModel.modelReady = true
                        }
                    }
                    self?.refreshModelStatuses()
                }
            } catch {
                self?.log("Failed to download model \(key): \(error)")
                DispatchQueue.main.async {
                    self?.viewModel.modelStatuses[key]?.downloading = false
                }
            }
        }
    }

    private func deleteModelByKey(_ key: String) {
        let scriptPath = modelsScriptPath()
        let pythonPath = modelsPythonPath()

        guard FileManager.default.fileExists(atPath: scriptPath) else { return }

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let process = Process()
            process.executableURL = URL(fileURLWithPath: pythonPath)
            process.arguments = [scriptPath, "delete", key]
            process.standardOutput = Pipe()
            process.standardError = Pipe()

            do {
                try process.run()
                process.waitUntilExit()
                DispatchQueue.main.async {
                    self?.viewModel.modelStatuses[key]?.installed = false
                    if key == "whisper" {
                        self?.viewModel.modelReady = false
                    }
                    self?.refreshModelStatuses()
                }
            } catch {
                self?.log("Failed to delete model \(key): \(error)")
            }
        }
    }

    // MARK: - Extractor

    private func extractorArguments(_ arguments: [String], productId: Int? = nil) -> [String] {
        if let pid = productId {
            return ["--product-id", "\(pid)"] + arguments
        }
        return arguments
    }

    /// Build extractor arguments for a volume device command.
    private func volumeExtractorArguments(_ command: String, device: HiDockPairedDevice, extra: [String] = []) -> [String] {
        var args = [command, "--volume-name", device.volumeName ?? ""]
        if let sub = device.subpath, !sub.isEmpty {
            args += ["--subpath", sub]
        }
        args += extra
        return args
    }

    private func ensureExtractorReady() -> Bool {
        let configHint = "\n\nTo fix, set the repo path via:\n  defaults write com.hidock.mic-trigger \(repoRootKey) /path/to/hidock-tools"
        guard FileManager.default.fileExists(atPath: extractorScriptPath) else {
            showError("HiDock extractor not found.\nExpected: \(extractorScriptPath)\(configHint)")
            return false
        }
        guard FileManager.default.isExecutableFile(atPath: extractorPythonPath) else {
            showError("Extractor Python venv not found.\nExpected executable: \(extractorPythonPath)\(configHint)")
            return false
        }
        return true
    }

    /// Outer kill-switch for extractor subprocesses. Per-USB-call timeout
    /// is separate (`--timeout-ms` arg). The slow operation is the initial
    /// file-list enumeration: each recording takes one or more USB
    /// round-trips, and for a HiDock with 280+ files the full enumeration
    /// empirically takes ~35s over USB 2.0. Subsequent calls are fast
    /// because the extractor's catalog cache short-circuits when the live
    /// file count matches the cached count. 30s used to be enough; the
    /// user's H1 crossed the threshold. 120s gives plenty of headroom and
    /// still keeps us from hanging forever on a genuinely wedged device.
    private let extractorProcessTimeout: TimeInterval = 120

    private func runExtractor(arguments: [String], productId: Int? = nil, completion: @escaping (Result<Data, Error>) -> Void) {
        let fullArgs = extractorArguments(arguments, productId: productId)
        log("runExtractor: \(fullArgs.joined(separator: " "))")
        let deviceKeyForBackoff: String? = productId.map { "hidock:\($0)" }
        syncExtractorQueue.async {
            let process = Process()
            process.currentDirectoryURL = URL(fileURLWithPath: self.extractorRoot)
            process.executableURL = URL(fileURLWithPath: self.extractorPythonPath)
            process.arguments = [self.extractorScriptPath] + fullArgs
            DispatchQueue.main.async { self.syncExtractorProcess = process }

            let tempDir = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
            let outURL = tempDir.appendingPathComponent("hidock-sync-\(UUID().uuidString).out")
            let errURL = tempDir.appendingPathComponent("hidock-sync-\(UUID().uuidString).err")

            do {
                FileManager.default.createFile(atPath: outURL.path, contents: nil)
                FileManager.default.createFile(atPath: errURL.path, contents: nil)
                let outHandle = try FileHandle(forWritingTo: outURL)
                let errHandle = try FileHandle(forWritingTo: errURL)
                process.standardOutput = outHandle
                process.standardError = errHandle
                try process.run()
                NSLog("runExtractor: process started (pid %d)", process.processIdentifier)

                let deadline = Date().addingTimeInterval(self.extractorProcessTimeout)
                while process.isRunning && Date() < deadline {
                    Thread.sleep(forTimeInterval: 0.2)
                }
                var weKilledIt = false
                if process.isRunning {
                    NSLog("runExtractor: killing hung process (pid %d) after %ds", process.processIdentifier, Int(self.extractorProcessTimeout))
                    weKilledIt = true
                    process.terminate()
                    Thread.sleep(forTimeInterval: 1)
                    if process.isRunning { process.interrupt() }
                    process.waitUntilExit()
                } else {
                    NSLog("runExtractor: process exited with status %d", process.terminationStatus)
                }

                // USB teardown cooldown. The kernel keeps IOUSBHost
                // ioregistry ownership attributed to the exited Python
                // process for a brief window after it terminates. If the
                // next extractor subprocess (on this serial queue) opens
                // the same HiDock immediately, it hits
                //   [Errno 13] Access denied — device held by Python (pid N)
                // where N is our own just-exited process. 250ms is
                // empirically enough for ioregistry to clear on M-series.
                // Only pay the cost for HiDock-targeted runs; volume /
                // list-devices / set-output don't need it.
                if productId != nil {
                    Thread.sleep(forTimeInterval: 0.25)
                }

                DispatchQueue.main.async { self.syncExtractorProcess = nil }
                try outHandle.close()
                try errHandle.close()

                let outData = (try? Data(contentsOf: outURL)) ?? Data()
                let errData = (try? Data(contentsOf: errURL)) ?? Data()
                try? FileManager.default.removeItem(at: outURL)
                try? FileManager.default.removeItem(at: errURL)

                if process.terminationStatus == 0 {
                    DispatchQueue.main.async { completion(.success(outData)) }
                } else if weKilledIt {
                    // A full timeout (we killed the process after
                    // extractorProcessTimeout) usually means the device
                    // firmware has stalled its USB endpoint. ffmpeg has
                    // already been paused by refresh/reconnect paths by
                    // the time we get here, so "busy recording" is no
                    // longer the most likely cause. Nudge the user toward
                    // the physical reseat that actually fixes it. This
                    // catches both .uncaughtSignal and the more common
                    // .exit path (the Python extractor installs a SIGTERM
                    // handler so process.terminationReason is .exit with
                    // status 143 after we kill it).
                    if let key = deviceKeyForBackoff {
                        let until = Date().addingTimeInterval(self.hungBackoffInterval)
                        DispatchQueue.main.async {
                            self.syncDeviceHungUntil[key] = until
                            self.log("Hung backoff: \(key) suppressed from auto-probes for \(Int(self.hungBackoffInterval))s — manual Reconnect will still try")
                        }
                    }
                    let error = NSError(domain: "HiDockSync", code: -1, userInfo: [
                        NSLocalizedDescriptionKey: "Device hung for \(Int(self.extractorProcessTimeout))s — the H1 firmware has stalled its USB endpoint. Unplug the HiDock and plug it back in to reset it."
                    ])
                    DispatchQueue.main.async { completion(.failure(error)) }
                } else {
                    let raw = String(data: errData.isEmpty ? outData : errData, encoding: .utf8) ?? ""
                    let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
                    // Empty-stderr non-zero exits are most often a silent USB
                    // disconnect or the device being busy recording. Surface
                    // something actionable instead of a bare blank line.
                    let message = trimmed.isEmpty
                        ? "Device not responding — unplug/replug, or wait if actively recording (exit \(process.terminationStatus))"
                        : trimmed
                    let error = NSError(domain: "HiDockSync", code: Int(process.terminationStatus), userInfo: [
                        NSLocalizedDescriptionKey: message
                    ])
                    DispatchQueue.main.async { completion(.failure(error)) }
                }
            } catch {
                DispatchQueue.main.async { completion(.failure(error)) }
            }
        }
    }

    private func runExtractorWithProgress(arguments: [String], productId: Int? = nil, onProgress: @escaping (Int, Int, Int) -> Void, completion: @escaping (Result<Data, Error>) -> Void) {
        let fullArgs = extractorArguments(arguments, productId: productId)
        syncExtractorQueue.async {
            let process = Process()
            process.currentDirectoryURL = URL(fileURLWithPath: self.extractorRoot)
            process.executableURL = URL(fileURLWithPath: self.extractorPythonPath)
            process.arguments = [self.extractorScriptPath] + fullArgs
            DispatchQueue.main.async { self.syncExtractorProcess = process }

            let outPipe = Pipe()
            let errPipe = Pipe()
            process.standardOutput = outPipe
            process.standardError = errPipe

            var outData = Data()
            let outQueue = DispatchQueue(label: "hidock.stdout")
            outPipe.fileHandleForReading.readabilityHandler = { handle in
                let chunk = handle.availableData
                if !chunk.isEmpty { outQueue.sync { outData.append(chunk) } }
            }

            var stderrData = Data()
            let errQueue = DispatchQueue(label: "hidock.stderr")
            var lineBuffer = ""
            errPipe.fileHandleForReading.readabilityHandler = { handle in
                let chunk = handle.availableData
                guard !chunk.isEmpty else { return }
                errQueue.sync {
                    stderrData.append(chunk)
                    guard let text = String(data: chunk, encoding: .utf8) else { return }
                    lineBuffer += text
                    while let range = lineBuffer.range(of: "\n") {
                        let line = String(lineBuffer[lineBuffer.startIndex..<range.lowerBound])
                        lineBuffer = String(lineBuffer[range.upperBound...])
                        if line.hasPrefix("PROGRESS:") {
                            let parts = line.dropFirst("PROGRESS:".count).split(separator: ":")
                            if parts.count >= 3,
                               let received = Int(parts[0]),
                               let total = Int(parts[1]),
                               let pct = Int(parts[2]) {
                                DispatchQueue.main.async { onProgress(received, total, pct) }
                            }
                        }
                    }
                }
            }

            do {
                try process.run()
                process.waitUntilExit()
                outPipe.fileHandleForReading.readabilityHandler = nil
                errPipe.fileHandleForReading.readabilityHandler = nil
                let trailingOut = outPipe.fileHandleForReading.readDataToEndOfFile()
                let trailingErr = errPipe.fileHandleForReading.readDataToEndOfFile()
                outQueue.sync { outData.append(trailingOut) }
                errQueue.sync { stderrData.append(trailingErr) }

                DispatchQueue.main.async { self.syncExtractorProcess = nil }

                let finalOut = outQueue.sync { outData }
                let finalErr = errQueue.sync { stderrData }

                if process.terminationStatus == 0 {
                    DispatchQueue.main.async { completion(.success(finalOut)) }
                } else {
                    let message = String(data: finalErr.isEmpty ? finalOut : finalErr, encoding: .utf8) ?? "Extractor failed"
                    let error = NSError(domain: "HiDockSync", code: Int(process.terminationStatus), userInfo: [
                        NSLocalizedDescriptionKey: message.trimmingCharacters(in: .whitespacesAndNewlines)
                    ])
                    DispatchQueue.main.async { completion(.failure(error)) }
                }
            } catch {
                outPipe.fileHandleForReading.readabilityHandler = nil
                errPipe.fileHandleForReading.readabilityHandler = nil
                DispatchQueue.main.async { completion(.failure(error)) }
            }
        }
    }

    // MARK: - Sync Actions

    private func renderSyncStatus(_ status: HiDockSyncStatusResponse, device: HiDockPairedDevice) {
        let importedBefore = syncEntries.filter { $0.deviceId == IMPORTED_DEVICE_ID }.count
        syncOutputFolder = status.outputDir
        UserDefaults.standard.set(status.outputDir, forKey: syncOutputFolderKey)
        syncDeviceConnected[device.deviceId] = status.connected
        if status.connected {
            syncDeviceLastOK[device.deviceId] = Date()
            syncDeviceLastError.removeValue(forKey: device.deviceId)
            syncDeviceHungUntil.removeValue(forKey: device.deviceId)
        }
        if let stats = status.storage {
            syncDeviceStorage[device.deviceId] = stats
            let gb = Double(stats.totalBytesReturned) / 1_073_741_824
            let caveat = stats.truncated ? " (firmware truncated list, actual usage higher)" : ""
            log("Storage[\(device.shortName)]: \(stats.totalFiles) files, \(String(format: "%.2f", gb)) GB used\(caveat)")
        }
        // Always populate entries when the extractor returns recordings —
        // whether connected:true (live device) or connected:false with
        // cached recordings (extractor serves state.json's catalog when
        // the device is unreachable). Only skip replacement when we
        // genuinely got NOTHING back, so a one-off transient failure
        // doesn't wipe the table.
        if !status.recordings.isEmpty {
            // Preserve transcription state across the rebuild. Without
            // this, every refresh constructs fresh entries with
            // transcribed=false by default, and the Transcribed column
            // briefly flickers empty until refreshTranscriptionState
            // runs async and re-populates it. If the lookup misses
            // (state.json gone, transcript .md moved, etc.) an entry
            // can get stuck as untranscribed for the rest of the
            // session. Carry the last-known values forward by name.
            let previousByName = Dictionary(
                uniqueKeysWithValues: syncEntries
                    .filter { $0.deviceId == device.deviceId }
                    .map { ($0.recording.name, $0) }
            )
            syncEntries.removeAll { $0.deviceId == device.deviceId }
            for recording in status.recordings {
                let prev = previousByName[recording.name]
                syncEntries.append(HiDockSyncRecordingEntry(
                    recording: recording,
                    deviceProductId: device.productId,
                    deviceId: device.deviceId,
                    deviceName: device.cleanName,
                    transcribed: prev?.transcribed ?? false,
                    transcriptPath: prev?.transcriptPath,
                    speakersTagged: prev?.speakersTagged ?? false,
                    summaryPath: prev?.summaryPath,
                    transcriptionSkipped: prev?.transcriptionSkipped ?? false
                ))
            }
        } else {
            log("renderSyncStatus[\(device.shortName)]: empty recordings (connected=\(status.connected)), preserving last-known \(syncEntries.filter { $0.deviceId == device.deviceId }.count) rows")
        }
        let importedAfter = syncEntries.filter { $0.deviceId == IMPORTED_DEVICE_ID }.count
        log("renderSyncStatus[\(device.shortName)]: \(status.recordings.count) device recs, imported before/after = \(importedBefore)/\(importedAfter), total syncEntries=\(syncEntries.count)")
        let validNames = Set(syncEntries.map(\.recording.name))
        syncCheckedRecordings = syncCheckedRecordings.intersection(validNames)

        if !syncBusy {
            let anyConnected = syncPairedDevices.contains(where: { syncDeviceConnected[$0.deviceId] == true })
            if anyConnected {
                // Per-device connection lives on the cards. Clear the
                // global status line so it's available for pipeline
                // messages only — no redundant "Connected — 🔊 P1" echo.
                viewModel.syncStatus = ""
                viewModel.syncStatusLevel = .normal
            } else {
                viewModel.syncStatus = "Not connected"
                viewModel.syncStatusLevel = .secondary
            }
            updateMenuSyncStatus(connected: status.connected)
        }
        syncViewModelState()
    }

    private func startSyncRefreshTimer() {
        syncRefreshStartDate = Date()
        syncRefreshTimer?.invalidate()
        syncRefreshTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            guard let self = self, let start = self.syncRefreshStartDate else { return }
            let elapsed = Int(Date().timeIntervalSince(start))
            self.viewModel.syncStatus = "Refreshing... \(elapsed)s"
        }
    }

    private func stopSyncRefreshTimer() {
        syncRefreshTimer?.invalidate()
        syncRefreshTimer = nil
        syncRefreshStartDate = nil
    }

    private var visibleSyncEntries: [HiDockSyncRecordingEntry] {
        viewModel.visibleEntries
    }

    private func selectedSyncEntries() -> [HiDockSyncRecordingEntry] {
        let entries = visibleSyncEntries
        if !syncCheckedRecordings.isEmpty {
            return entries.filter { syncCheckedRecordings.contains($0.recording.name) }
        }
        return []
    }

    private func toggleHideDownloaded() {
        syncHideDownloaded.toggle()
        UserDefaults.standard.set(syncHideDownloaded, forKey: syncHideDownloadedKey)
        syncViewModelState()
    }

    private func toggleAutoDownload() {
        syncAutoDownload.toggle()
        UserDefaults.standard.set(syncAutoDownload, forKey: syncAutoDownloadKey)
        syncViewModelState()
    }

    private func toggleAutoTranscribe() {
        syncAutoTranscribe.toggle()
        UserDefaults.standard.set(syncAutoTranscribe, forKey: syncAutoTranscribeKey)
        syncViewModelState()
    }

    private func toggleDiarize() {
        diarizeEnabled.toggle()
        UserDefaults.standard.set(diarizeEnabled, forKey: "diarizeEnabled")
        syncViewModelState()
    }

    private var lastToggledRecordingName: String?

    private func toggleSyncRecordingCheckbox(_ name: String, shiftHeld: Bool = false) {
        let visible = visibleSyncEntries
        let willCheck = !syncCheckedRecordings.contains(name)

        if shiftHeld, let anchor = lastToggledRecordingName,
           let anchorIdx = visible.firstIndex(where: { $0.recording.name == anchor }),
           let targetIdx = visible.firstIndex(where: { $0.recording.name == name }) {
            // Shift+click: select/deselect the range
            let range = min(anchorIdx, targetIdx)...max(anchorIdx, targetIdx)
            for i in range {
                let entryName = visible[i].recording.name
                if willCheck {
                    syncCheckedRecordings.insert(entryName)
                } else {
                    syncCheckedRecordings.remove(entryName)
                }
            }
        } else {
            if syncCheckedRecordings.contains(name) {
                syncCheckedRecordings.remove(name)
            } else {
                syncCheckedRecordings.insert(name)
            }
        }

        lastToggledRecordingName = name
        syncViewModelState()
    }

    private func selectAllSyncRecordings() {
        for entry in visibleSyncEntries {
            syncCheckedRecordings.insert(entry.recording.name)
        }
        syncViewModelState()
    }

    private func selectNoneSyncRecordings() {
        syncCheckedRecordings.removeAll()
        syncViewModelState()
    }

    private func selectNotDownloadedSyncRecordings() {
        syncCheckedRecordings.removeAll()
        for entry in visibleSyncEntries where !entry.recording.downloaded {
            syncCheckedRecordings.insert(entry.recording.name)
        }
        syncViewModelState()
    }

    private func filterSyncByDevice(_ deviceId: String?) {
        syncFilterDeviceId = deviceId
        syncViewModelState()
    }

    private func scheduleAutoDownloadNewRecordings() {
        guard syncAutoDownload, syncPaired, !syncBusy else { return }
        syncAutoDownloadTimer?.invalidate()
        syncAutoDownloadTimer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: false) { [weak self] _ in
            guard let self = self, self.syncAutoDownload, self.syncPaired, !self.syncBusy else { return }
            self.downloadNewSyncRecordings()
        }
    }

    private func refreshSyncStatus() {
        guard !syncBusy else {
            log("refreshSyncStatus: skipping, already busy")
            return
        }
        guard ensureExtractorReady() else { return }
        let devices = syncPairedDevices
        guard !devices.isEmpty else {
            viewModel.syncStatus = "Not paired"
            viewModel.syncStatusLevel = .secondary
            syncViewModelState()
            return
        }

        // Don't pause the mic trigger for refresh. If ffmpeg is
        // actively holding the HiDock audio interface right now, skip
        // HiDock probes (vendor-interface traffic during audio
        // streaming has wedged H1 firmware in the past) and keep
        // last-known state. Otherwise probe freely even if the trigger
        // child process is alive — its mere existence doesn't block
        // anything. Volumes are on a different interface; always safe.
        // Manual Reconnect (↻) remains the explicit "I want fresh
        // state" gesture.
        let recording = viewModel.hidockRecordingActive
        let probeDevices = devices.filter { device in
            if device.deviceType == .volume { return true }
            if !recording { return true }
            log("Refresh: skipping \(device.cleanName) — ffmpeg is currently recording, keeping last-known state")
            return false
        }
        if probeDevices.isEmpty {
            log("Refresh: no devices to probe (trigger active, all HiDocks skipped)")
            // Leave status/busy state untouched; nothing to do.
            return
        }
        performRefreshProbes(devices: probeDevices, restartTriggerAfter: false)
    }

    private func performRefreshProbes(devices: [HiDockPairedDevice], restartTriggerAfter: Bool) {
        syncBusy = true
        viewModel.syncStatus = "Refreshing..."
        viewModel.syncStatusLevel = .secondary
        startSyncRefreshTimer()
        syncViewModelState()

        let group = DispatchGroup()
        var anyConnected = false
        var deviceErrors: [String: String] = [:]
        let now = Date()

        for device in devices {
            // Skip devices currently in hung-backoff — they just timed
            // out at the USB level and a retry is only going to hang
            // again. Let the user press ↻ (which clears the backoff)
            // once they've physically reseated the device.
            if let until = syncDeviceHungUntil[device.deviceId], until > now {
                log("Refresh: skipping \(device.cleanName) — hung-backoff active for \(Int(until.timeIntervalSince(now)))s more")
                if syncDeviceConnected[device.deviceId] == true { anyConnected = true }
                continue
            }
            group.enter()

            // Choose extractor command based on device type
            let args: [String]
            let pid: Int?
            switch device.deviceType {
            case .hidock:
                args = ["status", "--timeout-ms", "5000"]
                pid = device.productId
            case .volume:
                args = volumeExtractorArguments("volume-status", device: device)
                pid = nil
            }

            runExtractor(arguments: args, productId: pid) { [weak self] result in
                guard let self = self else { group.leave(); return }
                switch result {
                case .success(let data):
                    do {
                        let payload = try JSONDecoder().decode(HiDockSyncStatusResponse.self, from: data)
                        // Extractor can return a JSON payload with
                        // connected:false and an error like "device held
                        // by Python (pid X)" when it races its own
                        // previous subprocess teardown, or "held by
                        // ffmpeg" when the mic trigger has the interface.
                        // Either way, that's a transient false-negative —
                        // don't clobber a previously-good state. Skip the
                        // render entirely so storage/recordings/connected
                        // all stay intact.
                        let transientHeldBy = !payload.connected
                            && device.deviceType == .hidock
                            && (payload.error?.contains("held by") ?? false)
                            && self.syncDeviceConnected[device.deviceId] == true
                        if transientHeldBy {
                            self.log("Ignoring transient 'held by' for \(device.cleanName): \(payload.error ?? "") — keeping last-known Connected state")
                            anyConnected = true
                        } else {
                            self.renderSyncStatus(payload, device: device)
                            if payload.connected {
                                anyConnected = true
                            } else if let err = payload.error {
                                self.log("Sync: \(device.cleanName) not connected: \(err)")
                                deviceErrors[device.cleanName] = err
                            }
                        }
                    } catch {
                        self.log("Sync decode failure for \(device.cleanName): \(error.localizedDescription)")
                        deviceErrors[device.cleanName] = error.localizedDescription
                    }
                case .failure(let error):
                    let desc = error.localizedDescription
                    let shortDesc = desc.components(separatedBy: "\n").last(where: { !$0.isEmpty }) ?? desc
                    self.log("Sync status error for \(device.cleanName): \(shortDesc)")
                    deviceErrors[device.cleanName] = shortDesc
                    // Only flip the card to Unreachable if the failure
                    // genuinely means the device is gone. For HiDocks we
                    // have a known false-positive: while the mic trigger's
                    // ffmpeg is holding the USB audio interface, a status
                    // query will either time out or return "held by". The
                    // device is fine — the query just can't get through.
                    // Don't clobber a previously-good state in that case;
                    // the card keeps its last-known "Connected" look until
                    // a real Refresh (which pauses ffmpeg) can re-probe.
                    let triggerHoldingHiDock = device.deviceType == .hidock && self.process != nil
                    if triggerHoldingHiDock && self.syncDeviceConnected[device.deviceId] == true {
                        self.log("Keeping \(device.cleanName) as Connected — ffmpeg is holding the interface, query failure is expected")
                    } else {
                        self.syncDeviceLastError[device.deviceId] = (shortDesc, Date())
                        self.syncDeviceConnected[device.deviceId] = false
                    }
                }
                group.leave()
            }
        }

        group.notify(queue: .main) { [weak self] in
            guard let self = self else { return }
            self.syncBusy = false
            self.stopSyncRefreshTimer()
            if anyConnected {
                // Per-device connection state lives on the device cards
                // now, so the global status line no longer needs to
                // duplicate it with "Connected — 🔊 P1 · 🎙 H1". Keep the
                // row available for pipeline messages (transcribing /
                // downloading / errors) by clearing it here on success.
                self.viewModel.syncStatus = ""
                self.viewModel.syncStatusLevel = .normal
            } else if !deviceErrors.isEmpty {
                let bestError = deviceErrors.values.first(where: { $0.contains("held by") })
                    ?? deviceErrors.values.first ?? "unknown"
                let message = syncErrorDescription(bestError)
                self.viewModel.syncStatus = message
                self.viewModel.syncStatusLevel = .error
            }
            self.updateMenuSyncStatus(connected: anyConnected)
            self.refreshTranscriptionState()
            self.syncViewModelState()
            if restartTriggerAfter {
                self.log("refreshSyncStatus: restarting mic trigger")
                self.startTrigger()
            }
        }
    }

    private func chooseSyncOutputFolder() {
        guard ensureExtractorReady() else { return }
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Choose Recordings Folder"
        if let current = syncOutputFolder {
            panel.directoryURL = URL(fileURLWithPath: current)
        }

        if panel.runModal() == .OK, let url = panel.url {
            runExtractor(arguments: ["set-output", url.path]) { [weak self] result in
                guard let self = self else { return }
                switch result {
                case .success:
                    self.syncOutputFolder = url.path
                    self.viewModel.syncOutputFolder = url.path
                    UserDefaults.standard.set(url.path, forKey: self.syncOutputFolderKey)
                    self.refreshSyncStatus()
                case .failure(let error):
                    self.log("HiDock sync set-output error: \(error.localizedDescription)")
                    self.showError("Failed to set recordings folder:\n\(error.localizedDescription)")
                }
            }
        }
    }

    private func chooseTranscriptOutputFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Choose Transcript Folder"
        if let current = syncTranscriptFolder {
            panel.directoryURL = URL(fileURLWithPath: current)
        }

        if panel.runModal() == .OK, let url = panel.url {
            syncTranscriptFolder = url.path
            viewModel.syncTranscriptFolder = url.path
            UserDefaults.standard.set(url.path, forKey: syncTranscriptFolderKey)
        }
    }

    private func pairSyncDock() {
        guard ensureExtractorReady() else { return }
        viewModel.syncStatus = "Searching for devices..."
        viewModel.syncStatusLevel = .secondary

        runExtractor(arguments: ["list-devices"]) { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success(let data):
                guard let response = try? JSONDecoder().decode(HiDockDeviceListResponse.self, from: data) else {
                    self.showError("Failed to parse device list")
                    return
                }
                let devices = response.devices
                let alreadyPaired = Set(self.syncPairedDevices.map(\.deviceId))
                let unpaired = devices.filter { !alreadyPaired.contains("hidock:\($0.productId)") }
                if unpaired.isEmpty && devices.isEmpty {
                    self.viewModel.syncStatus = "No HiDock devices found"
                    self.viewModel.syncStatusLevel = .error
                    self.showError("No HiDock devices found.\nConnect a HiDock via USB and try again.")
                    return
                }
                if unpaired.isEmpty {
                    self.viewModel.syncStatus = "All connected devices already paired"
                    self.viewModel.syncStatusLevel = .secondary
                    return
                }
                if unpaired.count == 1, let device = unpaired.first {
                    self.completePairing(device)
                    return
                }
                self.showDevicePicker(unpaired)
            case .failure(let error):
                self.viewModel.syncStatus = "Device search failed"
                self.viewModel.syncStatusLevel = .error
                self.showError("Failed to search for HiDock devices:\n\(error.localizedDescription)")
            }
        }
    }

    private func showDevicePicker(_ devices: [HiDockDevice]) {
        let alert = NSAlert()
        alert.messageText = "Select HiDock Devices"
        alert.informativeText = "Select which devices to pair with."
        alert.alertStyle = .informational
        alert.addButton(withTitle: "Pair Selected")
        alert.addButton(withTitle: "Cancel")

        let stackHeight = CGFloat(devices.count) * 26
        let container = NSView(frame: NSRect(x: 0, y: 0, width: 300, height: stackHeight))
        var checkboxes: [NSButton] = []
        for (i, device) in devices.enumerated() {
            let cb = NSButton(checkboxWithTitle: device.displayName, target: nil, action: nil)
            cb.state = .on
            cb.frame = NSRect(x: 0, y: stackHeight - CGFloat(i + 1) * 26, width: 300, height: 22)
            container.addSubview(cb)
            checkboxes.append(cb)
        }
        alert.accessoryView = container

        let response = alert.runModal()
        if response == .alertFirstButtonReturn {
            for (i, cb) in checkboxes.enumerated() where cb.state == .on {
                completePairing(devices[i])
            }
        }
    }

    private func completePairing(_ device: HiDockDevice) {
        let pairedDevice = HiDockPairedDevice(productId: device.productId, displayName: device.displayName)
        var devices = syncPairedDevices
        if !devices.contains(pairedDevice) {
            devices.append(pairedDevice)
            syncPairedDevices = devices
        }
        log("Paired with \(device.displayName) (product ID: \(device.productId)) — \(devices.count) device(s) total")
        syncViewModelState()
        refreshSyncStatus()
    }

    private func unpairSyncDock() {
        let devices = syncPairedDevices
        if devices.count > 1 {
            let alert = NSAlert()
            alert.messageText = "Unpair HiDock Device"
            alert.informativeText = "Choose which device to unpair, or unpair all."
            alert.alertStyle = .informational
            alert.addButton(withTitle: "Unpair All")
            for device in devices {
                alert.addButton(withTitle: "Unpair \(device.cleanName)")
            }
            alert.addButton(withTitle: "Cancel")

            let response = alert.runModal()
            if response == .alertFirstButtonReturn {
                syncPairedDevices = []
            } else {
                let buttonIndex = response.rawValue - NSApplication.ModalResponse.alertFirstButtonReturn.rawValue
                if buttonIndex >= 1 && buttonIndex <= devices.count {
                    var updated = devices
                    updated.remove(at: buttonIndex - 1)
                    syncPairedDevices = updated
                } else {
                    return
                }
            }
        } else {
            syncPairedDevices = []
        }

        syncEntries = syncEntries.filter { entry in
            syncPairedDevices.contains(where: { $0.deviceId == entry.deviceId })
        }
        syncCheckedRecordings = syncCheckedRecordings.intersection(Set(syncEntries.map(\.recording.name)))
        if syncPairedDevices.isEmpty {
            viewModel.syncStatus = "Unpaired"
            viewModel.syncStatusLevel = .secondary
        }
        updateMenuSyncStatus(connected: false)
        syncViewModelState()
    }

    /// Skip handles two distinct "I don't want to deal with this" intents,
    /// routing by the recording's current state:
    ///
    /// 1. **Not downloaded** → extractor `mark-downloaded` so the HiDock
    ///    catalogue treats it as handled (auto-download ignores it).
    /// 2. **Downloaded but not transcribed** → add to
    ///    `~/HiDock/skipped_transcriptions.json` so auto-transcribe filters
    ///    it out. The audio file stays on disk — the user can unskip and
    ///    transcribe later.
    ///
    /// Already-transcribed recordings are no-ops (they're done — Skip is
    /// redundant). Imports can only be skipped from transcription, not
    /// from download (they have no "download" concept).
    private func markSyncRecordingsAsDownloaded() {
        let entries = selectedSyncEntries()
        let toSkipDownload = entries.filter { !$0.recording.downloaded }
        let toSkipTranscription = entries.filter {
            $0.recording.downloaded
            && $0.recording.localExists
            && !$0.transcribed
            && !$0.transcriptionSkipped
        }
        let alreadyHandled = entries.count - toSkipDownload.count - toSkipTranscription.count

        log("Skip: \(entries.count) selected → \(toSkipDownload.count) skip-download, \(toSkipTranscription.count) skip-transcription, \(alreadyHandled) already handled")

        // Bail with a clear message if nothing is actionable.
        guard !toSkipDownload.isEmpty || !toSkipTranscription.isEmpty else {
            if entries.isEmpty {
                viewModel.syncStatus = "Skip: no recordings selected"
            } else {
                viewModel.syncStatus = "Skip: all \(entries.count) selected are already transcribed or skipped"
            }
            viewModel.syncStatusLevel = .warning
            syncViewModelState()
            return
        }

        viewModel.syncStatus = "Skipping \(toSkipDownload.count + toSkipTranscription.count) recording(s)..."
        viewModel.syncStatusLevel = .info
        syncViewModelState()

        // Apply transcription-skip immediately (no subprocess needed).
        // Use outputName (MP3 filename) so it matches the key used by
        // refreshTranscriptionState's lookup from transcribe.py status.
        // The on-device `recording.name` is a .hda filename and wouldn't match.
        if !toSkipTranscription.isEmpty {
            for entry in toSkipTranscription {
                skippedTranscriptions.insert(entry.recording.outputName)
            }
            SkippedTranscriptionsStore.save(skippedTranscriptions)
            for i in syncEntries.indices {
                if skippedTranscriptions.contains(syncEntries[i].recording.outputName) {
                    syncEntries[i].transcriptionSkipped = true
                }
            }
            log("Skip-transcription: recorded \(toSkipTranscription.count) filename(s) in \(SkippedTranscriptionsStore.path)")
        }

        guard !toSkipDownload.isEmpty else {
            // Only transcription-skip was performed — no extractor work needed.
            viewModel.syncStatus = "Skipped \(toSkipTranscription.count) recording(s) from transcription"
            viewModel.syncStatusLevel = .success
            syncCheckedRecordings.removeAll()
            syncViewModelState()
            return
        }

        // Skip-download path: dispatch to extractor per device.
        let byDevice = Dictionary(grouping: toSkipDownload, by: \.deviceId)
        let group = DispatchGroup()
        var anyError: String?

        for (deviceId, deviceEntries) in byDevice {
            let filenames = deviceEntries.map(\.recording.name)
            let device = syncPairedDevices.first { $0.deviceId == deviceId }

            var args: [String]
            let pid: Int?
            if let device = device, device.deviceType == .volume {
                args = ["mark-downloaded", "--volume-name", device.volumeName ?? ""] + filenames
                pid = nil
            } else {
                args = ["mark-downloaded"] + filenames
                pid = device?.productId
            }

            log("Skip-download[\(device?.shortName ?? deviceId)]: mark-downloaded \(filenames.count) file(s), pid=\(pid.map(String.init) ?? "nil")")
            group.enter()
            runExtractor(arguments: args, productId: pid) { [weak self] result in
                switch result {
                case .success(let data):
                    self?.log("Skip-download[\(device?.shortName ?? deviceId)]: ok (\(data.count) bytes)")
                case .failure(let error):
                    anyError = error.localizedDescription
                    self?.log("Skip-download[\(device?.shortName ?? deviceId)]: FAILED — \(error.localizedDescription)")
                }
                group.leave()
            }
        }

        group.notify(queue: .main) { [weak self] in
            guard let self = self else { return }
            if let error = anyError {
                self.showError("Failed to skip recordings:\n\(error)")
            } else {
                let total = toSkipDownload.count + toSkipTranscription.count
                self.viewModel.syncStatus = "Skipped \(total) recording(s)"
                self.viewModel.syncStatusLevel = .success
            }
            self.syncCheckedRecordings.removeAll()
            self.refreshSyncStatus()
        }
    }

    /// Unskip mirrors Skip: undoes whichever kind of skip is on the entry.
    /// For skip-download (file isn't local), calls the extractor's
    /// unmark-downloaded. For skip-transcription (file is local),
    /// removes the name from the skipped-transcriptions JSON.
    private func unmarkSyncRecordingsAsDownloaded() {
        let entries = selectedSyncEntries()
        let toUnskipDownload = entries.filter {
            $0.recording.downloaded && !$0.recording.localExists
        }
        let toUnskipTranscription = entries.filter { $0.transcriptionSkipped }

        guard !toUnskipDownload.isEmpty || !toUnskipTranscription.isEmpty else {
            viewModel.syncStatus = "Unskip: no skipped recordings in selection"
            viewModel.syncStatusLevel = .warning
            syncViewModelState()
            return
        }

        // Un-skip the transcription side immediately. MP3 name (outputName)
        // is the canonical key — mirrors the insert path.
        if !toUnskipTranscription.isEmpty {
            for entry in toUnskipTranscription {
                skippedTranscriptions.remove(entry.recording.outputName)
            }
            SkippedTranscriptionsStore.save(skippedTranscriptions)
            for i in syncEntries.indices {
                if !skippedTranscriptions.contains(syncEntries[i].recording.outputName) {
                    syncEntries[i].transcriptionSkipped = false
                }
            }
            log("Unskip-transcription: removed \(toUnskipTranscription.count) filename(s)")
        }

        guard !toUnskipDownload.isEmpty else {
            viewModel.syncStatus = "Un-skipped \(toUnskipTranscription.count) recording(s) from transcription"
            viewModel.syncStatusLevel = .success
            syncCheckedRecordings.removeAll()
            syncViewModelState()
            return
        }

        let byDevice = Dictionary(grouping: toUnskipDownload, by: \.deviceId)
        let group = DispatchGroup()
        var anyError: String?

        for (deviceId, deviceEntries) in byDevice {
            let filenames = deviceEntries.map(\.recording.name)
            let device = syncPairedDevices.first { $0.deviceId == deviceId }

            var args: [String]
            let pid: Int?
            if let device = device, device.deviceType == .volume {
                args = ["unmark-downloaded", "--volume-name", device.volumeName ?? ""] + filenames
                pid = nil
            } else {
                args = ["unmark-downloaded"] + filenames
                pid = device?.productId
            }

            group.enter()
            runExtractor(arguments: args, productId: pid) { result in
                if case .failure(let error) = result {
                    anyError = error.localizedDescription
                }
                group.leave()
            }
        }

        group.notify(queue: .main) { [weak self] in
            guard let self = self else { return }
            if let error = anyError {
                self.showError("Failed to unskip recordings:\n\(error)")
            }
            self.syncCheckedRecordings.removeAll()
            self.refreshSyncStatus()
        }
    }

    private func stopSyncDownload() {
        log("User requested stop download")
        syncDownloadStopping = true
        if let proc = syncExtractorProcess, proc.isRunning {
            proc.terminate()
        }
        syncExtractorProcess = nil
        stopDownloadTimer()
        syncBusy = false
        syncDownloading = false
        viewModel.syncStatus = "Download stopped"
        viewModel.syncStatusLevel = .warning
        syncViewModelState()
    }

    private func startDownloadTimer() {
        syncDownloadStartDate = Date()
        syncDownloadTimer?.invalidate()
        syncDownloadTimer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            guard let self = self, let start = self.syncDownloadStartDate else { return }
            let elapsed = Int(Date().timeIntervalSince(start))
            let mins = elapsed / 60
            let secs = elapsed % 60
            let timeStr = mins > 0 ? String(format: "%d:%02d", mins, secs) : "\(secs)s"
            self.viewModel.syncDownloadProgress = "Downloading... \(timeStr)"
        }
    }

    private func stopDownloadTimer() {
        syncDownloadTimer?.invalidate()
        syncDownloadTimer = nil
        syncDownloadStartDate = nil
        viewModel.syncDownloadProgress = nil
    }

    private func downloadSelectedSyncRecording() {
        guard ensureExtractorReady() else { return }
        let entries = selectedSyncEntries()
        guard !entries.isEmpty else { return }

        syncBusy = true
        syncDownloading = true
        startDownloadTimer()
        if entries.count == 1, let entry = entries.first {
            viewModel.syncStatus = "Downloading \(entry.recording.outputName)..."
        } else {
            viewModel.syncStatus = "Downloading \(entries.count) recordings..."
        }
        viewModel.syncStatusLevel = .secondary
        syncViewModelState()

        downloadSyncRecordings(entries, completed: []) { [weak self] result in
            guard let self = self else { return }
            self.stopDownloadTimer()
            self.syncBusy = false
            self.syncDownloading = false
            switch result {
            case .success(let downloaded):
                if let payload = downloaded.first, entries.count == 1 {
                    let body = "\(payload.filename.replacingOccurrences(of: ".hda", with: ".mp3")) saved successfully."
                    self.postSyncDownloadNotification(title: "✅ Download Complete", body: body)
                } else {
                    let body = entries.count == 1
                        ? "\(entries[0].recording.outputName) saved successfully."
                        : "\(entries.count) recordings were saved successfully."
                    self.postSyncDownloadNotification(title: "✅ Download Complete", body: body)
                }
                self.syncCheckedRecordings.subtract(entries.map(\.recording.name))
                self.refreshSyncStatus()
                // Intentionally NOT chaining into auto-transcribe here.
                // "Download Selected" is a manual action — the user
                // picked specific files and clicked Download, not
                // "Download and Transcribe". Auto-transcribe only
                // applies to the auto-download path (see
                // downloadNewFromDevices). If the user wants to
                // transcribe the just-downloaded files, they can
                // click Transcribe Selected.
            case .failure(let error):
                if self.syncDownloadStopping {
                    self.syncDownloadStopping = false
                    return
                }
                self.viewModel.syncStatus = "Download failed"
                self.viewModel.syncStatusLevel = .error
                let label = entries.count == 1 ? entries[0].recording.name : "\(entries.count) recordings"
                self.showError("Failed to download \(label):\n\(error.localizedDescription)")
                self.syncViewModelState()
            }
        }
    }

    /// Look up the paired device for a recording entry.
    private func pairedDevice(for entry: HiDockSyncRecordingEntry) -> HiDockPairedDevice? {
        syncPairedDevices.first { $0.deviceId == entry.deviceId }
    }

    private func downloadSyncRecordings(
        _ remaining: [HiDockSyncRecordingEntry],
        completed: [HiDockSyncDownloadResult],
        completion: @escaping (Result<[HiDockSyncDownloadResult], Error>) -> Void
    ) {
        guard let current = remaining.first else {
            completion(.success(completed))
            return
        }

        // Choose extractor command based on device type
        let args: [String]
        let pid: Int?
        if let device = pairedDevice(for: current), device.deviceType == .volume {
            args = volumeExtractorArguments("volume-import", device: device, extra: [current.recording.name])
            pid = nil
        } else {
            args = ["download", current.recording.name, "--length", "\(current.recording.length)"]
            pid = current.deviceProductId
        }

        runExtractorWithProgress(arguments: args, productId: pid, onProgress: { [weak self] received, total, pct in
            guard let self = self else { return }
            let receivedMB = String(format: "%.1f", Double(received) / 1_000_000)
            let totalMB = String(format: "%.1f", Double(total) / 1_000_000)
            self.viewModel.syncStatus = "Downloading \(current.recording.outputName) — \(pct)% (\(receivedMB)/\(totalMB) MB)"
            self.viewModel.syncDownloadProgress = "\(pct)% (\(receivedMB)/\(totalMB) MB)"
        }) { [weak self] result in
            guard self != nil else { return }
            switch result {
            case .success(let data):
                guard let payload = try? JSONDecoder().decode(HiDockSyncDownloadResult.self, from: data) else {
                    let error = NSError(domain: "HiDockSync", code: 2, userInfo: [
                        NSLocalizedDescriptionKey: "Failed to decode download result for \(current.recording.name)"
                    ])
                    completion(.failure(error))
                    return
                }
                self?.downloadSyncRecordings(Array(remaining.dropFirst()), completed: completed + [payload], completion: completion)
            case .failure(let error):
                completion(.failure(error))
            }
        }
    }

    private func downloadNewSyncRecordings() {
        guard ensureExtractorReady() else { return }
        let devices = syncPairedDevices
        guard !devices.isEmpty else { return }

        syncBusy = true
        syncDownloading = true
        startDownloadTimer()
        viewModel.syncStatus = "Downloading new recordings..."
        viewModel.syncStatusLevel = .secondary
        syncViewModelState()

        downloadNewFromDevices(devices, totalDownloaded: 0)
    }

    private func downloadNewFromDevices(_ remaining: [HiDockPairedDevice], totalDownloaded: Int) {
        guard let device = remaining.first else {
            stopDownloadTimer()
            syncBusy = false
            syncDownloading = false
            if totalDownloaded > 0 {
                let body = totalDownloaded == 1
                    ? "1 new recording was saved successfully."
                    : "\(totalDownloaded) new recordings were saved successfully."
                postSyncDownloadNotification(title: "✅ Downloads Complete", body: body)
            }
            viewModel.syncStatus = "Downloaded \(totalDownloaded) new recordings"
            viewModel.syncStatusLevel = .success
            refreshSyncStatus()
            // Auto-transcribe any untranscribed downloaded files
            if self.syncAutoTranscribe, ensureTranscriptionReady() {
                // Refresh entries first to get latest state
                let untranscribed = self.syncEntries
                    .filter { $0.recording.downloaded && $0.recording.localExists && !$0.transcribed && !$0.transcriptionSkipped }
                    .map(\.recording.outputPath)
                if !untranscribed.isEmpty {
                    self.log("Auto-transcribe: \(untranscribed.count) untranscribed recording(s) found")
                    self.enqueueTranscriptions(untranscribed)
                }
            }
            syncViewModelState()
            return
        }

        // Choose extractor command based on device type
        let args: [String]
        let pid: Int?
        switch device.deviceType {
        case .hidock:
            args = ["download-new"]
            pid = device.productId
        case .volume:
            args = volumeExtractorArguments("volume-import-new", device: device)
            pid = nil
        }

        runExtractorWithProgress(arguments: args, productId: pid, onProgress: { [weak self] received, total, pct in
            guard let self = self else { return }
            let receivedMB = String(format: "%.1f", Double(received) / 1_000_000)
            let totalMB = String(format: "%.1f", Double(total) / 1_000_000)
            self.viewModel.syncStatus = "Downloading (\(device.cleanName)) — \(pct)% (\(receivedMB)/\(totalMB) MB)"
            self.viewModel.syncDownloadProgress = "\(pct)% (\(receivedMB)/\(totalMB) MB)"
        }) { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success(let data):
                var deviceDownloaded = 0
                if let payload = try? JSONDecoder().decode(HiDockSyncDownloadNewResponse.self, from: data) {
                    if let error = payload.error {
                        self.log("download-new error for \(device.cleanName): \(error)")
                    }
                    deviceDownloaded = payload.downloaded.count
                }
                self.downloadNewFromDevices(Array(remaining.dropFirst()), totalDownloaded: totalDownloaded + deviceDownloaded)
            case .failure(let error):
                if self.syncDownloadStopping {
                    self.syncDownloadStopping = false
                    self.stopDownloadTimer()
                    self.syncBusy = false
                    self.syncDownloading = false
                    self.syncViewModelState()
                    return
                }
                self.log("download-new failed for \(device.cleanName): \(error.localizedDescription)")
                self.downloadNewFromDevices(Array(remaining.dropFirst()), totalDownloaded: totalDownloaded)
            }
        }
    }

    // MARK: - Transcription

    private func ensureTranscriptionReady() -> Bool {
        let configHint = "\n\nTo fix, set the repo path via:\n  defaults write com.hidock.mic-trigger \(repoRootKey) /path/to/hidock-tools"
        guard FileManager.default.fileExists(atPath: transcriptionScriptPath) else {
            showError("Transcription script not found.\nExpected: \(transcriptionScriptPath)\(configHint)")
            return false
        }
        guard FileManager.default.isExecutableFile(atPath: transcriptionPythonPath) else {
            showError("Transcription Python venv not found.\nExpected executable: \(transcriptionPythonPath)\n\nRun: cd \(transcriptionRoot) && ./setup-venv.sh")
            return false
        }
        return true
    }

    private let whisperModelURL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin"
    private var whisperModelPath: String {
        "\(NSHomeDirectory())/HiDock/Speech-to-Text/ggml-large-v3-turbo-q5_0.bin"
    }
    private var modelDownloadTask: URLSessionDownloadTask?
    private var modelDownloadDelegate: ModelDownloadDelegate?

    private func downloadWhisperModel() {
        let destPath = whisperModelPath
        let dir = (destPath as NSString).deletingLastPathComponent
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true, attributes: nil)

        guard let url = URL(string: whisperModelURL) else { return }
        log("Downloading whisper model from \(whisperModelURL)")

        viewModel.modelDownloading = true
        viewModel.modelDownloadProgress = 0
        viewModel.modelDownloadStatus = "Starting download..."

        let delegate = ModelDownloadDelegate(
            destPath: destPath,
            onProgress: { [weak self] bytesWritten, totalBytes, speed in
                DispatchQueue.main.async {
                    guard let self = self else { return }
                    let fraction = totalBytes > 0 ? Double(bytesWritten) / Double(totalBytes) : 0
                    self.viewModel.modelDownloadProgress = fraction
                    let mbDone = Double(bytesWritten) / (1024 * 1024)
                    let mbTotal = Double(totalBytes) / (1024 * 1024)
                    var status = String(format: "%.0f / %.0f MB", mbDone, mbTotal)
                    if !speed.isEmpty {
                        status += " · \(speed)"
                    }
                    self.viewModel.modelDownloadStatus = status
                }
            },
            onComplete: { [weak self] success, error in
                DispatchQueue.main.async {
                    guard let self = self else { return }
                    self.viewModel.modelDownloading = false
                    if success {
                        self.log("Whisper model downloaded successfully")
                        self.viewModel.modelReady = true
                        self.viewModel.modelDownloadProgress = 1.0
                        self.viewModel.modelDownloadStatus = "Download complete"
                    } else {
                        self.log("Model download failed: \(error ?? "unknown")")
                        self.viewModel.modelDownloadProgress = 0
                        self.viewModel.modelDownloadStatus = "Download failed: \(error ?? "unknown error")"
                    }
                    self.modelDownloadTask = nil
                    self.modelDownloadDelegate = nil
                }
            }
        )

        modelDownloadDelegate = delegate
        let session = URLSession(configuration: .default, delegate: delegate, delegateQueue: nil)
        let task = session.downloadTask(with: url)
        modelDownloadTask = task
        task.resume()
    }

    private func cancelModelDownload() {
        modelDownloadTask?.cancel()
        modelDownloadTask = nil
        modelDownloadDelegate = nil
        viewModel.modelDownloading = false
        viewModel.modelDownloadProgress = 0
        viewModel.modelDownloadStatus = ""
        log("Model download cancelled")
    }

    /// Return true if some ffmpeg process currently has a HiDock audio
    /// device open. Used as an initial-state probe on app launch so the
    /// 'Recording' pill reflects reality immediately rather than waiting
    /// for the next mic in/out transition from the trigger child.
    static func probeFFmpegHoldingHiDock() -> Bool {
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/pgrep")
        task.arguments = ["-f", "ffmpeg.*HiDock"]
        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = Pipe()
        do {
            try task.run()
            task.waitUntilExit()
        } catch {
            return false
        }
        // pgrep exits 0 when at least one match is found.
        return task.terminationStatus == 0
    }

    /// Compute a transcription timeout based on audio duration when known,
    /// falling back to file-size heuristics otherwise. Whisper on MPS runs at
    /// roughly 3–5× real-time; 1.5× audio duration + 10-min slack gives plenty
    /// of safety margin. Capped at 4 hours so a runaway process can't
    /// camp on the GPU indefinitely.
    static func computeTranscriptionTimeout(
        for path: String, knownDuration: Double = 0,
    ) -> TimeInterval {
        if knownDuration > 0 {
            return min(14400.0, max(600.0, knownDuration * 1.5 + 600.0))
        }
        // Fallback: probe via AVFoundation if we didn't get a duration upstream.
        let probed = ImportedRecordingsStore.probeDuration(at: path)
        if probed > 0 {
            return min(14400.0, max(600.0, probed * 1.5 + 600.0))
        }
        // Last resort: scale by file size, roughly MP3-calibrated. Overshoots
        // for WAV/FLAC but better to over-allocate than kill mid-transcription.
        let fileSizeMB = Double(
            (try? FileManager.default.attributesOfItem(atPath: path)[.size] as? Int) ?? 0
        ) / (1024 * 1024)
        return min(14400.0, max(600.0, fileSizeMB * 60.0 + 600.0))
    }

    private func runTranscription(arguments: [String], timeout: TimeInterval = 600, onProgress: ((Int) -> Void)? = nil, onStage: ((Int, Int, String) -> Void)? = nil, completion: @escaping (Result<Data, Error>) -> Void) {
        log("runTranscription: \(arguments.joined(separator: " "))")
        transcriptionDispatchQueue.async {
            let process = Process()
            process.currentDirectoryURL = URL(fileURLWithPath: self.transcriptionRoot)
            process.executableURL = URL(fileURLWithPath: self.transcriptionPythonPath)
            process.arguments = [self.transcriptionScriptPath] + arguments

            // Ensure the subprocess has a proper shell environment —
            // apps launched via LaunchAgent inherit a minimal env that
            // can break torch/MPS.
            var env = ProcessInfo.processInfo.environment
            let home = NSHomeDirectory()
            env["HOME"] = home
            if env["PATH"] == nil || !env["PATH"]!.contains("/opt/homebrew") {
                env["PATH"] = "\(NSHomeDirectory())/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
            } else if let existing = env["PATH"], !existing.contains("/.local/bin") {
                env["PATH"] = "\(NSHomeDirectory())/.local/bin:" + existing
            }
            // Metal / MPS needs access to the GPU frameworks
            env["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
            // Force unbuffered Python output so PROGRESS lines arrive immediately
            env["PYTHONUNBUFFERED"] = "1"
            process.environment = env

            let outPipe = Pipe()
            let errPipe = Pipe()
            process.standardOutput = outPipe
            process.standardError = errPipe

            var outData = Data()
            let outQueue = DispatchQueue(label: "hidock.transcription.stdout")
            outPipe.fileHandleForReading.readabilityHandler = { handle in
                let chunk = handle.availableData
                if !chunk.isEmpty { outQueue.sync { outData.append(chunk) } }
            }

            var stderrData = Data()
            let errQueue = DispatchQueue(label: "hidock.transcription.stderr")
            var lineBuffer = ""
            errPipe.fileHandleForReading.readabilityHandler = { handle in
                let chunk = handle.availableData
                guard !chunk.isEmpty else { return }
                errQueue.sync { stderrData.append(chunk) }
                if let text = String(data: chunk, encoding: .utf8) {
                    lineBuffer += text
                    while let range = lineBuffer.range(of: "\n") {
                        let line = String(lineBuffer[lineBuffer.startIndex..<range.lowerBound])
                        lineBuffer = String(lineBuffer[range.upperBound...])
                        if line.hasPrefix("STAGE:") {
                            // STAGE:2/5:Transcribing
                            let parts = String(line.dropFirst("STAGE:".count)).split(separator: ":", maxSplits: 1)
                            if parts.count >= 1 {
                                let nums = parts[0].split(separator: "/")
                                if nums.count == 2, let cur = Int(nums[0]), let tot = Int(nums[1]) {
                                    let label = parts.count > 1 ? String(parts[1]) : ""
                                    DispatchQueue.main.async { onStage?(cur, tot, label) }
                                }
                            }
                        } else if line.hasPrefix("PROGRESS:") {
                            if let pct = Int(line.dropFirst("PROGRESS:".count)) {
                                DispatchQueue.main.async { onProgress?(pct) }
                            }
                        }
                    }
                    if lineBuffer.hasPrefix("PROGRESS:") && lineBuffer.count < 15 {
                        let remainder = String(lineBuffer.dropFirst("PROGRESS:".count))
                        if let pct = Int(remainder) {
                            DispatchQueue.main.async { onProgress?(pct) }
                        }
                    }
                }
            }

            do {
                try process.run()
                NSLog("runTranscription: process started (pid %d)", process.processIdentifier)

                let deadline = Date().addingTimeInterval(timeout)
                while process.isRunning && Date() < deadline {
                    Thread.sleep(forTimeInterval: 0.5)
                }
                if process.isRunning {
                    NSLog("runTranscription: killing hung process (pid %d) after %ds", process.processIdentifier, Int(timeout))
                    process.terminate()
                    Thread.sleep(forTimeInterval: 1)
                    if process.isRunning { process.interrupt() }
                    process.waitUntilExit()
                } else {
                    NSLog("runTranscription: process exited with status %d", process.terminationStatus)
                }

                outPipe.fileHandleForReading.readabilityHandler = nil
                errPipe.fileHandleForReading.readabilityHandler = nil

                let remainingOut = outPipe.fileHandleForReading.readDataToEndOfFile()
                if !remainingOut.isEmpty { outQueue.sync { outData.append(remainingOut) } }

                let remainingErr = errPipe.fileHandleForReading.readDataToEndOfFile()
                if !remainingErr.isEmpty { errQueue.sync { stderrData.append(remainingErr) } }

                let finalOut = outQueue.sync { outData }
                let finalErr = errQueue.sync { stderrData }

                if process.terminationStatus == 0 {
                    DispatchQueue.main.async { completion(.success(finalOut)) }
                } else if process.terminationReason == .uncaughtSignal {
                    let error = NSError(domain: "HiDockTranscription", code: -1, userInfo: [
                        NSLocalizedDescriptionKey: "Transcription timed out after \(Int(timeout))s"
                    ])
                    DispatchQueue.main.async { completion(.failure(error)) }
                } else {
                    let message = String(data: finalErr.isEmpty ? finalOut : finalErr, encoding: .utf8) ?? "Transcription failed"
                    let error = NSError(domain: "HiDockTranscription", code: Int(process.terminationStatus), userInfo: [
                        NSLocalizedDescriptionKey: message.trimmingCharacters(in: .whitespacesAndNewlines)
                    ])
                    DispatchQueue.main.async { completion(.failure(error)) }
                }
            } catch {
                DispatchQueue.main.async { completion(.failure(error)) }
            }
        }
    }

    private func transcribeFile(mp3Path: String) {
        guard ensureTranscriptionReady() else { return }
        enqueueTranscriptions([mp3Path])
    }

    /// Legacy single-file transcription path (kept for auto-download→transcribe flow)
    private func transcribeFileDirect(mp3Path: String) {
        guard !transcriptionBusy else {
            // Already busy — enqueue instead
            enqueueTranscriptions([mp3Path])
            return
        }
        guard ensureTranscriptionReady() else { return }

        transcriptionBusy = true
        let filename = (mp3Path as NSString).lastPathComponent
        transcriptionCurrentFile = filename
        transcriptionProgress = 0
        transcriptionFileIndex = 0
        transcriptionFileCount = 1
        viewModel.syncStatus = "Transcribing \(filename)..."
        viewModel.syncStatusLevel = .secondary
        syncViewModelState()
        log("Starting transcription for \(filename)")

        var transcribeArgs = ["transcribe", mp3Path]
        if diarizeEnabled { transcribeArgs.append("--diarize") }
        // Always pass --summarize; transcribe.py gracefully skips if no LLM
        // CLI (claude/codex/gemini/ollama) is on PATH. Leaves action_items /
        // decisions / key_points / tags filled when claude is authed.
        transcribeArgs.append("--summarize")
        // Timeout scales by audio duration, not file size — WAV is ~10× bigger
        // than MP3 for the same audio length, so file-size-based budgets were
        // wildly overprovisioned. Whisper on MPS runs at ~3–5× real-time, so
        // 1.5× audio duration + 10min slack is safe. 4-hour cap protects
        // against runaway processes.
        let scaledTimeout = Self.computeTranscriptionTimeout(for: mp3Path)
        runTranscription(arguments: transcribeArgs, timeout: scaledTimeout, onProgress: { [weak self] pct in
            guard let self = self else { return }
            self.transcriptionProgress = pct
            self.viewModel.syncStatus = "Transcribing \(filename) — \(pct)%"
            self.syncViewModelState()
        }) { [weak self] result in
            guard let self = self else { return }
            self.transcriptionBusy = false
            self.transcriptionCurrentFile = nil
            self.transcriptionProgress = 0
            switch result {
            case .success(let data):
                if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                   let transcribed = json["transcribed"] as? Bool, transcribed {
                    let duration = json["duration_s"] as? Double ?? 0
                    let transcriptPath = json["transcript_path"] as? String
                    self.postTranscriptionNotification(
                        title: "📝 Transcription Complete",
                        body: "\(filename) transcribed in \(Int(duration))s",
                        transcriptPath: transcriptPath
                    )
                    self.viewModel.syncStatus = "Transcription complete"
                    self.viewModel.syncStatusLevel = .success
                } else {
                    let errorMsg = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["error"] as? String ?? "Unknown error"
                    self.log("Transcription failed for \(filename): \(errorMsg)")
                    self.viewModel.syncStatus = "Transcription failed"
                    self.viewModel.syncStatusLevel = .error
                }
                self.refreshTranscriptionState()
                self.syncViewModelState()
            case .failure(let error):
                self.log("Transcription process failed for \(filename): \(error.localizedDescription)")
                self.viewModel.syncStatus = "Transcription failed"
                self.viewModel.syncStatusLevel = .error
                self.refreshTranscriptionState()
                self.syncViewModelState()
            }
        }
    }

    private func transcribeSelectedRecordings() {
        guard ensureTranscriptionReady() else { return }
        guard !syncDownloading else { return }
        transcriptionCancelled = false

        // Collect paths from regular selections AND checked merge groups
        var mergedPaths: [String] = []
        for group in mergeGroups {
            if syncCheckedRecordings.contains("merge:\(group.id)"),
               FileManager.default.fileExists(atPath: group.outputPath) {
                mergedPaths.append(group.outputPath)
            }
        }

        let selected = selectedSyncEntries()
        guard !selected.isEmpty || !mergedPaths.isEmpty else {
            log("Transcribe selected: no recordings selected")
            viewModel.syncStatus = "No recordings selected"
            viewModel.syncStatusLevel = .warning
            syncViewModelState()
            return
        }

        // If only merged files selected, enqueue them directly
        if selected.isEmpty && !mergedPaths.isEmpty {
            log("Transcribe selected: \(mergedPaths.count) merged file(s)")
            enqueueTranscriptions(mergedPaths)
            syncCheckedRecordings.removeAll()
            syncViewModelState()
            return
        }

        let alreadyTranscribed = selected.filter { $0.transcribed }
        var all = selected.filter { !$0.transcribed }

        // If all selected are already transcribed, offer to re-transcribe
        if all.isEmpty && !alreadyTranscribed.isEmpty {
            let names = alreadyTranscribed.prefix(3).map { $0.recording.outputName }.joined(separator: ", ")
            let suffix = alreadyTranscribed.count > 3 ? " and \(alreadyTranscribed.count - 3) more" : ""
            log("Transcribe selected: all \(alreadyTranscribed.count) recording(s) already transcribed — \(names)\(suffix)")
            let alert = NSAlert()
            alert.messageText = "Already Transcribed"
            alert.informativeText = "\(alreadyTranscribed.count) selected recording\(alreadyTranscribed.count == 1 ? " is" : "s are") already transcribed.\n\nRe-transcribe?"
            alert.addButton(withTitle: "Re-transcribe")
            alert.addButton(withTitle: "Cancel")
            alert.alertStyle = .informational
            if alert.runModal() == .alertFirstButtonReturn {
                all = alreadyTranscribed
            } else {
                return
            }
        }
        guard !all.isEmpty else { return }

        let ready = all.filter { $0.recording.downloaded && $0.recording.localExists }
        let needsDownload = all.filter { !$0.recording.downloaded || !$0.recording.localExists }
        log("Transcribe selected: \(all.count) to process (\(ready.count) ready, \(needsDownload.count) need download)")

        if needsDownload.isEmpty {
            log("Transcribe selected: starting transcription for \(ready.count) ready recording(s)")
            startTranscription(entries: ready)
        } else {
            guard ensureExtractorReady() else { return }

            // Capture all entries we intend to transcribe *before* the
            // download+refresh cycle, which can clear checkbox selection.
            let allEntriesToTranscribe = all
            syncBusy = true
            syncDownloading = true
            startDownloadTimer()
            let label = needsDownload.count == 1
                ? needsDownload[0].recording.outputName
                : "\(needsDownload.count) recordings"
            viewModel.syncStatus = "Downloading \(label) before transcription..."
            viewModel.syncStatusLevel = .secondary
            syncViewModelState()

            downloadSyncRecordings(needsDownload, completed: []) { [weak self] result in
                guard let self = self else { return }
                self.stopDownloadTimer()
                self.syncBusy = false
                self.syncDownloading = false
                switch result {
                case .success(let downloaded):
                    self.refreshSyncStatus()
                    // Use the pre-captured entries instead of re-querying
                    // selections (which may have been cleared by refresh).
                    let readyNow = allEntriesToTranscribe.filter {
                        FileManager.default.fileExists(atPath: $0.recording.outputPath)
                    }
                    if !readyNow.isEmpty {
                        self.startTranscription(entries: readyNow)
                    } else {
                        self.viewModel.syncStatus = "Download complete but no files ready for transcription"
                        self.viewModel.syncStatusLevel = .warning
                        self.syncViewModelState()
                    }
                case .failure(let error):
                    if self.syncDownloadStopping {
                        self.syncDownloadStopping = false
                        return
                    }
                    self.viewModel.syncStatus = "Download failed"
                    self.viewModel.syncStatusLevel = .error
                    self.showError("Failed to download recordings for transcription:\n\(error.localizedDescription)")
                    self.syncViewModelState()
                }
            }
        }
    }

    private func startTranscription(entries: [HiDockSyncRecordingEntry]) {
        guard !entries.isEmpty else {
            log("startTranscription: called with empty entries")
            return
        }
        let paths = entries.map(\.recording.outputPath)
        enqueueTranscriptions(paths)
    }

    private func enqueueTranscriptions(_ paths: [String]) {
        for path in paths {
            if !pendingTranscriptionQueue.contains(where: { $0.path == path }) {
                pendingTranscriptionQueue.append(TranscriptionQueueItem(path: path))
            }
        }
        log("Enqueued \(paths.count) recording(s), queue size: \(pendingTranscriptionQueue.count)")
        syncViewModelState()
        processNextInQueue()
    }

    private func processNextInQueue() {
        guard !transcriptionBusy, !transcriptionPaused, !transcriptionCancelled else { return }
        guard let index = pendingTranscriptionQueue.firstIndex(where: { $0.status == .queued }) else {
            // Queue empty — all done
            let completed = pendingTranscriptionQueue.filter { $0.status == .completed }.count
            if completed > 0 {
                let transcriptFolder = syncTranscriptFolder ?? "\(NSHomeDirectory())/HiDock/Raw Transcripts"
                postTranscriptionNotification(title: "Transcription Complete", body: "\(completed) recording\(completed == 1 ? "" : "s") transcribed.", transcriptPath: transcriptFolder)

                // Post-meeting nudge (from minutes v0.11.2)
                // Subtle status bar suggestion — not a modal or popup
                let untagged = syncEntries.filter { $0.transcribed && !$0.speakersTagged }.count
                if untagged > 3 {
                    viewModel.syncStatus = "✅ \(completed) transcribed — \(untagged) need speaker tagging"
                    viewModel.syncStatusLevel = .info
                } else {
                    viewModel.syncStatus = "Transcription complete"
                    viewModel.syncStatusLevel = .success
                }
            }
            refreshTranscriptionState()
            syncViewModelState()
            return
        }

        transcriptionBusy = true
        pendingTranscriptionQueue[index].status = .transcribing
        let item = pendingTranscriptionQueue[index]
        let filename = item.filename
        let total = pendingTranscriptionQueue.count
        let position = pendingTranscriptionQueue.filter({ $0.status == .completed }).count + 1

        transcriptionCurrentFile = filename
        transcriptionProgress = 0
        transcriptionLastRealProgress = 0
        viewModel.syncStatus = "Transcribing \(filename) (\(position)/\(total))…"
        viewModel.syncStatusLevel = .secondary
        syncViewModelState()

        // Estimate duration from file size (~8KB/s for 16kHz mono MP3, Whisper ~2x realtime on MPS)
        let fileSize = (try? FileManager.default.attributesOfItem(atPath: item.path)[.size] as? Int) ?? 0
        let audioDuration = Double(fileSize) / 8000.0
        transcriptionEstimatedDuration = max(audioDuration / 2.0, 60)  // Whisper ~2x realtime
        transcriptionStartTime = Date()

        // Start a Swift-side timer for synthetic progress (Python GIL blocks the progress thread)
        transcriptionProgressTimer?.invalidate()
        transcriptionProgressTimer = Timer.scheduledTimer(withTimeInterval: 3.0, repeats: true) { [weak self] _ in
            guard let self = self, self.transcriptionBusy else { return }
            guard let startTime = self.transcriptionStartTime else { return }
            let elapsed = Date().timeIntervalSince(startTime)
            let frac = min(elapsed / self.transcriptionEstimatedDuration, 0.95)
            let syntheticPct = max(15, min(Int(15 + frac * 70), 84))
            // Only use synthetic if real progress hasn't overtaken it
            let displayPct = max(syntheticPct, self.transcriptionLastRealProgress)
            self.transcriptionProgress = displayPct
            if let idx = self.pendingTranscriptionQueue.firstIndex(where: { $0.path == item.path }) {
                self.pendingTranscriptionQueue[idx].progress = displayPct
            }
            self.viewModel.syncStatus = "Transcribing \(filename) — \(displayPct)% (\(position)/\(total))"
            self.syncViewModelState()
        }

        var args = ["transcribe", item.path]
        if diarizeEnabled { args.append("--diarize") }
        args.append("--summarize")  // see transcribeFileDirect for rationale
        // Prefer duration from the HiDock catalogue / import metadata; falls
        // back to file-size probing for edge cases where duration is unknown.
        let itemDuration = pendingTranscriptionQueue.first(where: { $0.path == item.path })
            .flatMap { queueItem -> Double? in
                syncEntries.first(where: { $0.recording.outputName == queueItem.filename })?.recording.duration
            } ?? 0
        let scaledTimeout = Self.computeTranscriptionTimeout(for: item.path, knownDuration: itemDuration)
        runTranscription(arguments: args, timeout: scaledTimeout, onProgress: { [weak self] pct in
            guard let self = self else { return }
            self.transcriptionLastRealProgress = pct
            self.transcriptionProgress = pct
            if let idx = self.pendingTranscriptionQueue.firstIndex(where: { $0.path == item.path }) {
                self.pendingTranscriptionQueue[idx].progress = pct
            }
            self.syncViewModelState()
        }, onStage: { [weak self] current, stageTotal, label in
            guard let self = self else { return }
            let pct = Int(Double(current) / Double(max(stageTotal, 1)) * 100)
            self.transcriptionProgress = pct
            if let idx = self.pendingTranscriptionQueue.firstIndex(where: { $0.path == item.path }) {
                self.pendingTranscriptionQueue[idx].progress = pct
            }
            self.viewModel.syncStatus = "\(label) (\(current)/\(stageTotal)) — \(filename) [\(position)/\(total)]"
            self.syncViewModelState()
        }) { [weak self] result in
            guard let self = self else { return }
            self.transcriptionProgressTimer?.invalidate()
            self.transcriptionProgressTimer = nil
            self.transcriptionBusy = false
            self.transcriptionCurrentFile = nil
            self.transcriptionProgress = 0

            if let idx = self.pendingTranscriptionQueue.firstIndex(where: { $0.path == item.path }) {
                switch result {
                case .success:
                    self.pendingTranscriptionQueue[idx].status = .completed
                case .failure:
                    self.pendingTranscriptionQueue[idx].status = .failed
                }
            }

            self.refreshTranscriptionState()
            self.syncViewModelState()
            // Process next item
            self.processNextInQueue()
        }
    }

    private func cancelTranscription() {
        log("Transcription cancelled by user")
        transcriptionCancelled = true
        transcriptionPaused = false

        // Stop the progress timer
        transcriptionProgressTimer?.invalidate()
        transcriptionProgressTimer = nil

        // Mark the currently transcribing item as cancelled
        for i in pendingTranscriptionQueue.indices {
            if pendingTranscriptionQueue[i].status == .transcribing {
                pendingTranscriptionQueue[i].status = .cancelled
            }
            if pendingTranscriptionQueue[i].status == .queued {
                pendingTranscriptionQueue[i].status = .cancelled
            }
        }

        transcriptionBusy = false
        transcriptionCurrentFile = nil
        transcriptionProgress = 0
        viewModel.syncStatus = "Transcription cancelled"
        viewModel.syncStatusLevel = .warning
        syncViewModelState()
    }

    private func pauseTranscription() {
        log("Transcription paused by user")
        transcriptionPaused = true
        viewModel.syncStatus = "Transcription paused"
        viewModel.syncStatusLevel = .warning
        syncViewModelState()
    }

    private func resumeTranscription() {
        log("Transcription resumed by user")
        transcriptionPaused = false
        transcriptionCancelled = false
        syncViewModelState()
        processNextInQueue()
    }

    private func removeFromTranscriptionQueue(_ path: String) {
        pendingTranscriptionQueue.removeAll { $0.path == path && $0.status == .queued }
        syncViewModelState()
    }

    private func moveInTranscriptionQueue(from: Int, to: Int) {
        guard from >= 0, from < pendingTranscriptionQueue.count, to >= 0, to <= pendingTranscriptionQueue.count else { return }
        let item = pendingTranscriptionQueue.remove(at: from)
        let insertAt = to > from ? to - 1 : to
        pendingTranscriptionQueue.insert(item, at: min(insertAt, pendingTranscriptionQueue.count))
        syncViewModelState()
    }

    private func showTranscriptionQueueWindow() {
        if let existing = transcriptionQueueWindow, existing.isVisible {
            existing.makeKeyAndOrderFront(nil)
            return
        }
        let queueView = TranscriptionQueueView(viewModel: viewModel)
        let win = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 450, height: 400),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered,
            defer: false
        )
        win.center()
        win.title = "Transcription Queue"
        win.isReleasedWhenClosed = false
        win.minSize = NSSize(width: 350, height: 250)
        win.contentView = NSHostingView(rootView: queueView)
        win.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        transcriptionQueueWindow = win
    }

    private func transcribeAllRecordings() {
        guard ensureTranscriptionReady() else { return }
        let paths = viewModel.visibleEntries
            .filter { $0.recording.downloaded && $0.recording.localExists && !$0.transcribed && !$0.transcriptionSkipped }
            .map(\.recording.outputPath)

        guard !paths.isEmpty else {
            viewModel.syncStatus = "All recordings already transcribed"
            viewModel.syncStatusLevel = .success
            return
        }

        enqueueTranscriptions(paths)
    }

    private func refreshTranscriptionState() {
        guard FileManager.default.fileExists(atPath: transcriptionScriptPath),
              FileManager.default.isExecutableFile(atPath: transcriptionPythonPath) else {
            log("refreshTranscriptionState: skipping — script=\(FileManager.default.fileExists(atPath: transcriptionScriptPath)), python=\(FileManager.default.isExecutableFile(atPath: transcriptionPythonPath)) at \(transcriptionScriptPath)")
            return
        }

        transcriptionDispatchQueue.async {
            let process = Process()
            process.currentDirectoryURL = URL(fileURLWithPath: self.transcriptionRoot)
            process.executableURL = URL(fileURLWithPath: self.transcriptionPythonPath)
            process.arguments = [self.transcriptionScriptPath, "status"]

            // Set environment (same as runTranscription) so the subprocess
            // works when launched from a LaunchAgent's minimal env.
            var env = ProcessInfo.processInfo.environment
            env["HOME"] = NSHomeDirectory()
            if env["PATH"] == nil || !env["PATH"]!.contains("/opt/homebrew") {
                env["PATH"] = "\(NSHomeDirectory())/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
            } else if let existing = env["PATH"], !existing.contains("/.local/bin") {
                env["PATH"] = "\(NSHomeDirectory())/.local/bin:" + existing
            }
            process.environment = env

            let pipe = Pipe()
            process.standardOutput = pipe
            let errPipe = Pipe()
            process.standardError = errPipe

            do {
                try process.run()
                process.waitUntilExit()
            } catch {
                self.log("refreshTranscriptionState: failed to run process: \(error.localizedDescription)")
                return
            }

            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            if process.terminationStatus != 0 {
                let errData = errPipe.fileHandleForReading.readDataToEndOfFile()
                let errMsg = String(data: errData, encoding: .utf8) ?? "unknown error"
                self.log("refreshTranscriptionState: exit=\(process.terminationStatus), stderr=\(errMsg.prefix(400))")
                return
            }
            guard let lookup = try? JSONSerialization.jsonObject(with: data) as? [String: [String: Any]] else {
                self.log("refreshTranscriptionState: failed to decode JSON (\(data.count) bytes, first 200 chars=\(String(data: data, encoding: .utf8)?.prefix(200) ?? "?"))")
                return
            }

            DispatchQueue.main.async {
                self.log("refreshTranscriptionState: got \(lookup.count) entries from status")
                var matched = 0
                for i in self.syncEntries.indices {
                    let mp3Name = self.syncEntries[i].recording.outputName
                    if let info = lookup[mp3Name] {
                        self.syncEntries[i].transcribed = info["transcribed"] as? Bool ?? false
                        self.syncEntries[i].transcriptPath = info["transcript_path"] as? String
                        // Check speaker tagging state from diarized JSON
                        self.syncEntries[i].speakersTagged = self.checkSpeakersTagged(transcriptPath: info["transcript_path"] as? String)
                        // Check if summary exists
                        self.syncEntries[i].summaryPath = self.findSummaryPath(for: mp3Name)
                        if self.syncEntries[i].transcribed { matched += 1 }
                    } else {
                        self.syncEntries[i].transcribed = false
                        self.syncEntries[i].transcriptPath = nil
                        self.syncEntries[i].speakersTagged = false
                        self.syncEntries[i].summaryPath = nil
                    }
                    // Apply the transcription-skip flag from the user's
                    // persisted opt-out list, regardless of transcription state.
                    self.syncEntries[i].transcriptionSkipped =
                        self.skippedTranscriptions.contains(mp3Name)
                }
                self.log("refreshTranscriptionState: matched \(matched) transcribed entries out of \(self.syncEntries.count)")
                self.syncViewModelState()
            }
        }
    }

    // MARK: - Speaker Tagging & Summary Helpers

    private func checkSpeakersTagged(transcriptPath: String?) -> Bool {
        guard let mdPath = transcriptPath else { return false }
        let mdURL = URL(fileURLWithPath: mdPath)
        let baseName = mdURL.deletingPathExtension().lastPathComponent
        let dirURL = mdURL.deletingLastPathComponent()
        let diarizedPath = dirURL.appendingPathComponent(baseName + "_diarized.json").path

        guard FileManager.default.fileExists(atPath: diarizedPath),
              let data = FileManager.default.contents(atPath: diarizedPath),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let speakerNames = json["speaker_names"] as? [String: String] else {
            return false
        }

        if speakerNames.isEmpty { return false }
        let untaggedPattern = try? NSRegularExpression(pattern: "^Speaker \\d+$")
        for (_, name) in speakerNames {
            let range = NSRange(name.startIndex..., in: name)
            if untaggedPattern?.firstMatch(in: name, range: range) != nil {
                return false
            }
        }
        return true
    }

    private func findSummaryPath(for mp3Name: String) -> String? {
        let summariesDir = (NSHomeDirectory() as NSString).appendingPathComponent("HiDock/Summaries")
        let baseName = (mp3Name as NSString).deletingPathExtension
        guard let contents = try? FileManager.default.contentsOfDirectory(atPath: summariesDir) else { return nil }
        // Match summary files that contain the recording base name
        if let match = contents.first(where: { $0.hasSuffix(".md") && $0.contains(baseName) }) {
            return (summariesDir as NSString).appendingPathComponent(match)
        }
        return nil
    }

    // MARK: - Logging

    private func log(_ message: String) {
        let line = "[\(Date())] \(message)\n"
        NSLog("%@", message)
        guard let data = line.data(using: .utf8) else { return }

        if let attrs = try? FileManager.default.attributesOfItem(atPath: logPath),
           let size = attrs[.size] as? UInt64, size > 5 * 1024 * 1024 {
            let oldPath = logPath + ".old"
            try? FileManager.default.removeItem(atPath: oldPath)
            try? FileManager.default.moveItem(atPath: logPath, toPath: oldPath)
        }

        do {
            let logURL = URL(fileURLWithPath: logPath)
            if FileManager.default.fileExists(atPath: logPath) {
                let handle = try FileHandle(forWritingTo: logURL)
                handle.seekToEndOfFile()
                handle.write(data)
                try handle.close()
            } else {
                let logDir = (logPath as NSString).deletingLastPathComponent
                try FileManager.default.createDirectory(atPath: logDir, withIntermediateDirectories: true)
                try data.write(to: logURL)
            }
        } catch {
            NSLog("Failed to write log: %@", error.localizedDescription)
        }
    }

    // MARK: - Build

    private func buildTriggerBinaryAsync(completion: @escaping (Bool) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            let success = self.buildTriggerBinary()
            DispatchQueue.main.async { completion(success) }
        }
    }

    private func buildTriggerBinary() -> Bool {
        let source = sourcePath
        guard FileManager.default.fileExists(atPath: source) else {
            log("Source not found at \(source)")
            return false
        }

        let dir = "\(repoRoot)/mic-trigger"
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        p.currentDirectoryURL = URL(fileURLWithPath: dir)
        p.arguments = ["swiftc", "MicTrigger.swift", "-o", "hidock-mic-trigger"]

        let errPipe = Pipe()
        p.standardOutput = Pipe()
        p.standardError = errPipe

        do {
            try p.run()
            p.waitUntilExit()
        } catch {
            log("Build process failed to launch: \(error)")
            return false
        }

        if p.terminationStatus != 0 {
            let errData = errPipe.fileHandleForReading.readDataToEndOfFile()
            if let errStr = String(data: errData, encoding: .utf8), !errStr.isEmpty {
                log("Build failed:\n\(errStr)")
            }
            return false
        }

        return FileManager.default.isExecutableFile(atPath: binaryPath)
    }

    // MARK: - Actions

    @objc private func quitApp() {
        NSApp.terminate(nil)
    }

    private func showError(_ message: String) {
        log("ERROR: \(message)")
        let alert = NSAlert()
        alert.alertStyle = .critical
        alert.messageText = "HiDock Mic Trigger"
        alert.informativeText = message
        alert.runModal()
    }
}

// MARK: - Model Download Delegate

final class ModelDownloadDelegate: NSObject, URLSessionDownloadDelegate {
    private let destPath: String
    private let onProgress: (Int64, Int64, String) -> Void  // written, total, speed
    private let onComplete: (Bool, String?) -> Void
    private var lastBytes: Int64 = 0
    private var lastTime: Date = Date()

    init(destPath: String, onProgress: @escaping (Int64, Int64, String) -> Void, onComplete: @escaping (Bool, String?) -> Void) {
        self.destPath = destPath
        self.onProgress = onProgress
        self.onComplete = onComplete
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        let dest = URL(fileURLWithPath: destPath)
        do {
            if FileManager.default.fileExists(atPath: destPath) {
                try FileManager.default.removeItem(atPath: destPath)
            }
            try FileManager.default.moveItem(at: location, to: dest)
            onComplete(true, nil)
        } catch {
            onComplete(false, error.localizedDescription)
        }
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData bytesWritten: Int64, totalBytesWritten: Int64, totalBytesExpectedToWrite: Int64) {
        let now = Date()
        let elapsed = now.timeIntervalSince(lastTime)
        var speed = ""
        if elapsed > 0.5 {
            let bytesPerSec = Double(totalBytesWritten - lastBytes) / elapsed
            lastBytes = totalBytesWritten
            lastTime = now
            if bytesPerSec > 1_000_000 {
                speed = String(format: "%.1f MB/s", bytesPerSec / 1_000_000)
            } else {
                speed = String(format: "%.0f KB/s", bytesPerSec / 1_000)
            }
        }
        onProgress(totalBytesWritten, totalBytesExpectedToWrite, speed)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        if let error = error {
            let nsError = error as NSError
            if nsError.code == NSURLErrorCancelled {
                return // Cancelled by user, don't report as failure
            }
            onComplete(false, error.localizedDescription)
        }
    }
}
