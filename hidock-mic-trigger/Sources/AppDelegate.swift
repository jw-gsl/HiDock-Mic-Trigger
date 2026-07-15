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
    private var summaryViewerWindow: NSWindow?
    private var voiceLibraryWindow: NSWindow?
    private var voiceTrainingWindow: NSWindow?
    private var modelManagerWindow: NSWindow?
    private var templatesManagerWindow: NSWindow?
    private var deviceManagerWindow: NSWindow?
    private var plaudLoginController: PlaudLoginWindowController?
    private var terminalWindow: NSWindow?
    private weak var speakerLabelsMenuItem: NSMenuItem?
    private var importedRecordings: [ImportedRecordingEntry] = []
    /// Filenames the user has opted out of transcribing. Persisted to
    /// ~/HiDock/skipped_transcriptions.json; loaded at launch.
    private var skippedTranscriptions: Set<String> = []
    let viewModel = HiDockViewModel()

    private var syncOutputFolder: String?
    private var syncTranscriptFolder: String?
    private var syncEntries: [HiDockSyncRecordingEntry] = [] {
        didSet { syncEntriesVersion &+= 1 }
    }
    /// Bumped on every `syncEntries` mutation (incl. element edits, since it's a
    /// value-type array). Lets syncViewModelState skip pushing an unchanged
    /// array to the view model (see #4).
    private var syncEntriesVersion = 0
    private var lastPushedSyncEntriesVersion = -1
    /// While true, syncViewModelState won't push the entries array — used during
    /// launch so the imported-only list doesn't paint before the cache load.
    private var suppressSyncEntriesPush = false
    /// CoreAudio mic-name cache + last-enumeration time, so syncViewModelState
    /// doesn't re-enumerate devices on every call (see #3).
    private var cachedMicNames: [String] = []
    private var lastMicEnumeration: Date = .distantPast
    private var syncCheckedRecordings: Set<String> = []
    private var syncAutoDownload = false
    private var syncAutoTranscribe = false
    private let syncAutoTranscribeKey = "hidockSyncAutoTranscribe"
    private var syncAutoSummarise = false
    private let syncAutoSummariseKey = "hidockSyncAutoSummarise"
    // Serial summarise queue — shared by the row action, the
    // "Summarise Selected" toolbar button, and the auto-summarise
    // pipeline. One Claude Code summarise runs at a time so we never
    // fan out N concurrent CLI processes.
    private var summariseQueue: [HiDockSyncRecordingEntry] = []
    private var summariseBusy = false
    // Reclassify requests (summary viewer dropdown) waiting behind a
    // running summarise — same one-at-a-time gate as summariseQueue, so a
    // reclassify never runs concurrently with an auto/manual summarise
    // and interleaves output into the shared summaryTranscript.
    private var pendingReclassifies: [(transcriptPath: String, template: String)] = []
    // mp3 outputNames that finished transcribing this session and are
    // waiting for their transcriptPath to be populated by the next
    // refreshTranscriptionState, at which point auto-summarise queues them.
    private var pendingAutoSummariseNames: Set<String> = []
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
    private let plaudSignedOutMessage = "Plaud is not signed in"
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
    /// Lightweight periodic check for new Plaud recordings. Plaud is an API, not
    /// a USB device, so nothing else surfaces new recordings on it — this poll
    /// does. It only runs the expensive refresh/auto-download when the Plaud
    /// catalog actually changed (see pollPlaudForChanges).
    private var plaudPollTimer: Timer?
    private var syncExtractorProcess: Process?
    private let syncExtractorQueue = DispatchQueue(label: "hidock.extractor", qos: .userInitiated)
    // Concurrent queue used ONLY for launch cache-paint reads (cached-status /
    // plaud-cached-status). Those are pure catalog reads — no USB open, no
    // token mutation — so they can run in parallel, letting HiDock and Plaud
    // paint together instead of one-after-another on the serial queue above.
    private let cacheReadQueue = DispatchQueue(label: "hidock.cacheread", qos: .userInitiated, attributes: .concurrent)
    private var syncDownloadStartDate: Date?
    private var syncDownloadTimer: Timer?
    private var syncDownloadStopping = false
    private var syncDownloading = false

    // Transcription
    private let transcriptionDispatchQueue = DispatchQueue(label: "hidock.transcription", qos: .background)
    private var transcriptionBusy = false
    private var transcriptionCancelled = false
    private var transcriptionPaused = false
    /// The currently-running `transcribe.py` subprocess, if any. Held
    /// so `cancelTranscription()` can actually terminate it — without
    /// this reference the local variable in runTranscription's closure
    /// was unreachable, and clicking Cancel only updated the UI while
    /// the whisper/diarize/summarize pipeline kept burning CPU.
    private var transcriptionSubprocess: Process?
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

    /// Device-side filename of the recording the extractor is actively
    /// pulling. Mirrored to `viewModel.currentlyDownloadingName` so the
    /// recordings table can render that one row "Downloading" (yellow)
    /// while the rest of the pending batch stays "On device". Set on
    /// `FILE_START:`, cleared on `FILE_DONE:` and again on batch
    /// completion as a belt-and-braces guard.
    private var currentlyDownloadingName: String?

    /// True once the trigger CLI has confirmed it found both devices
    /// and is actively polling. `process != nil` alone isn't enough —
    /// a process can be alive but stuck in waitForDevice. Mirrored to
    /// `viewModel.triggerHealthy` and drives the green/amber dot in
    /// MicTriggerSection.
    private var triggerHealthy = false
    /// When the trigger last became healthy (HiDock connected). Drives the
    /// uptime readout as "time connected" — reset on each (re)connect, cleared
    /// while waiting so the timer doesn't count up before a device is present.
    private var triggerConnectedSince: Date?
    /// Human-readable wait status from the CLI's waitForDevice loop.
    /// Cleared when the trigger goes healthy or when the process exits.
    private var triggerWaitMessage: String?
    /// Wall-clock of the most recent successful start. Mirrored to the
    /// view model so the user can see a restart actually happened even
    /// when the amber→green flip was sub-second.
    private var triggerLastStartedAt: Date?
    /// Hold the amber state for at least this long after a restart so
    /// the eye can catch the transition. The "healthy" log line from
    /// the CLI can fire within ~50ms of process spawn when both
    /// devices were already enumerated; without this, the dot never
    /// visibly leaves green even though a restart happened.
    private static let minAmberDuration: TimeInterval = 1.5
    private var pendingHealthyTimer: Timer?

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
    private let syncAutoDownloadKey = "hidockSyncAutoDownload"
    private let hasCompletedOnboardingKey = "hasCompletedOnboarding"
    private let notifyTranscriptionKey = "notifyTranscriptionComplete"
    private let notifyDownloadKey = "notifyDownloadComplete"
    private let notifyMicChangesKey = "notifyMicChanges"

    // Summarisation provider (LLM engine used after transcription). "auto" lets
    // the pipeline detect/config-choose; a specific id is passed via
    // --summarize-engine. Mirrors shared/llm_cli.py's engine ids.
    private let summarizeEngineKey = "summarizeEngine"
    private var summarizeEngine: String { UserDefaults.standard.string(forKey: summarizeEngineKey) ?? "auto" }
    private let showCLIWhileSummarisingKey = "showCLIWhileSummarising"
    /// Defaults to true when never set.
    private var showCLIWhileSummarising: Bool {
        UserDefaults.standard.object(forKey: showCLIWhileSummarisingKey) == nil
            ? true : UserDefaults.standard.bool(forKey: showCLIWhileSummarisingKey)
    }
    private let summarizeEngineOptions: [(id: String, label: String)] = [
        ("auto", "Auto (detect)"),
        ("claude", "Claude"),
        ("codex", "Codex"),
        ("gemini", "Gemini"),
        ("ollama", "Ollama (local)"),
    ]
    private var summarizeSubmenu: NSMenu!
    private var summarizeMenuItem: NSMenuItem!

    /// Which engine "auto" currently resolves to, as reported by the pipeline's
    /// `detect-engine` (PATH detection, same logic the auto summariser uses).
    /// Refreshed at launch and whenever the engine is set to auto, so the
    /// interactive Ask AI / template commands pick the *same* engine the auto
    /// summariser would. Defaults to claude until the first detect returns.
    private var resolvedAutoEngine: String = "claude"

    /// Map an engine name to the interactive CLI invocation used in the pane.
    private func cliBinary(forEngine name: String) -> String {
        switch name {
        case "codex": return "codex"
        case "gemini": return "gemini"
        case "ollama": return "ollama run llama3.2"
        default: return "claude"
        }
    }

    /// The interactive CLI binary for the user's selected AI engine, used to
    /// launch "Ask AI" / template iteration in the CLI pane. "auto" uses the
    /// detected engine (resolvedAutoEngine) so it matches the summariser. The
    /// summarise subprocess itself receives the engine via --summarize-engine.
    private var aiCliBinary: String {
        let engine = (summarizeEngine == "auto") ? resolvedAutoEngine : summarizeEngine
        return cliBinary(forEngine: engine)
    }

    /// Ask the pipeline which engine 'auto' resolves to and cache it.
    private func refreshResolvedAutoEngine() {
        runTranscription(arguments: ["detect-engine"], timeout: 30) { [weak self] result in
            guard let self = self else { return }
            guard case .success(let data) = result,
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let name = json["engine"] as? String, !name.isEmpty else { return }
            self.resolvedAutoEngine = name
            self.log("AI auto-detect resolved to: \(name)")
        }
    }

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
    /// Timestamps of recent USB audio device-list changes — used to detect a
    /// "flap storm" (the bus re-enumerating repeatedly under contention). While
    /// flapping, we back off HiDock USB probing so the app stops fighting the
    /// mic trigger / other holders for the interface.
    private var recentDeviceChanges: [Date] = []
    private var usbFlapBackoffUntil: Date = .distantPast
    private var inUsbFlapBackoff: Bool { Date() < usbFlapBackoffUntil }
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
        // Build imported rows into syncEntries but DON'T push to the view yet —
        // loadCachedCatalogsForPaintOnLaunch pushes imported + device catalogs
        // together in one pass, so imported no longer flashes up first. The
        // suppress flag stops any intervening syncViewModelState (e.g. from
        // showSyncWindow) pushing the imported-only list first.
        suppressSyncEntriesPush = true
        viewModel.recordingsLoading = true
        mergeImportedIntoSyncEntries()
        let imp = syncEntries.filter { $0.deviceId == IMPORTED_DEVICE_ID }
        log("After rebuildSyncEntries: syncEntries=\(syncEntries.count) imported=\(imp.count)")
        if let first = imp.first {
            log("First imported entry: name=\(first.recording.name), deviceId=\(first.deviceId), downloaded=\(first.recording.downloaded), localExists=\(first.recording.localExists), outputPath=\(first.recording.outputPath)")
        }
        let vis = viewModel.visibleEntries.filter { $0.deviceId == IMPORTED_DEVICE_ID }
        log("viewModel.visibleEntries imported count = \(vis.count) (deviceFilter=\(viewModel.syncFilterDeviceId ?? "nil"))")
        // Fast-path: paint the recordings table from cached state before
        // USB enumeration even starts. The user sees their already-
        // downloaded + transcribed rows with correct status immediately
        // instead of watching the list populate device-by-device as live
        // probes resolve, then flip from "Downloaded" to "Transcribed"
        // when refreshTranscriptionState catches up.
        showSyncWindow()
        // Paint the full list from local cache FIRST (all devices + imported at
        // once), THEN run the live probes as enrichment — otherwise the fast H1
        // live probe lands before the cache and devices appear one-by-one.
        loadCachedCatalogsForPaintOnLaunch { [weak self] in
            self?.autoConnectSyncIfPaired(startTriggerOnCompletion: true)
        }

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
        // (autoConnect now runs AFTER the cache paint — see the
        // loadCachedCatalogsForPaintOnLaunch completion above.)

        // Start the lightweight Plaud new-recording poll (no-op until a Plaud
        // account is paired). App-open already probed via autoConnect above.
        startPlaudPollTimer()

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
        } else if menu == summarizeSubmenu {
            rebuildSummarizeSubmenu()
        }
    }

    private func rebuildSummarizeSubmenu() {
        summarizeSubmenu.removeAllItems()
        let current = summarizeEngine
        for opt in summarizeEngineOptions {
            let item = NSMenuItem(title: opt.label, action: #selector(selectSummarizeEngine(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = opt.id
            item.state = (opt.id == current) ? .on : .off
            summarizeSubmenu.addItem(item)
        }
    }

    @objc private func selectSummarizeEngine(_ sender: NSMenuItem) {
        guard let id = sender.representedObject as? String else { return }
        setSummarizeEngine(id)
    }

    /// Single setter for the AI summariser engine — used by both the menu-bar
    /// submenu and the Models-window picker so they stay in sync.
    private func setSummarizeEngine(_ id: String) {
        UserDefaults.standard.set(id, forKey: summarizeEngineKey)
        viewModel.summarizeEngine = id
        rebuildSummarizeSubmenu()
        if id == "auto" { refreshResolvedAutoEngine() }
        log("Summarisation provider set to \(id)")
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
        refreshMicNamesNow()
        viewModel.syncPairedDevices = syncPairedDevices
        viewModel.syncPaired = syncPaired
        viewModel.syncAutoDownload = UserDefaults.standard.bool(forKey: syncAutoDownloadKey)
        syncAutoDownload = viewModel.syncAutoDownload
        viewModel.plaudPollIntervalSeconds = plaudPollInterval
        viewModel.syncAutoTranscribe = UserDefaults.standard.bool(forKey: syncAutoTranscribeKey)
        syncAutoTranscribe = viewModel.syncAutoTranscribe
        viewModel.syncAutoSummarise = UserDefaults.standard.bool(forKey: syncAutoSummariseKey)
        syncAutoSummarise = viewModel.syncAutoSummarise
        viewModel.summarizeEngine = summarizeEngine
        viewModel.showCLIWhileSummarising = showCLIWhileSummarising
        refreshResolvedAutoEngine()
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
        viewModel.onRefreshSync = { [weak self] in self?.refreshSyncStatus(manual: true) }
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
        viewModel.onStopDownload = { [weak self] in self?.stopSyncDownload() }
        viewModel.onMarkDownloaded = { [weak self] in self?.markSyncRecordingsAsDownloaded() }
        viewModel.onSelectAll = { [weak self] in self?.selectAllSyncRecordings() }
        viewModel.onSelectNone = { [weak self] in self?.selectNoneSyncRecordings() }
        viewModel.onSelectNotDownloaded = { [weak self] in self?.selectNotDownloadedSyncRecordings() }
        viewModel.onFilterByDevice = { [weak self] deviceId in self?.filterSyncByDevice(deviceId) }
        viewModel.onToggleChecked = { [weak self] name, shift in self?.toggleSyncRecordingCheckbox(name, shiftHeld: shift) }
        viewModel.onUnmarkDownloaded = { [weak self] in self?.unmarkSyncRecordingsAsDownloaded() }
        viewModel.onToggleAutoDownload = { [weak self] in self?.toggleAutoDownload() }
        viewModel.onToggleAutoTranscribe = { [weak self] in self?.toggleAutoTranscribe() }
        viewModel.onToggleAutoSummarise = { [weak self] in self?.toggleAutoSummarise() }
        viewModel.onSetPlaudPollInterval = { [weak self] seconds in self?.setPlaudPollInterval(seconds) }
        viewModel.onToggleMergeExpand = { [weak self] id in self?.toggleMergeExpand(id) }
        viewModel.onTranscribeSelected = { [weak self] in self?.transcribeSelectedRecordings() }
        viewModel.onSummariseSelected = { [weak self] in self?.summariseSelectedRecordings() }
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
        viewModel.onSummariseRecording = { [weak self] entry in self?.summariseRecording(entry) }
        viewModel.onAskClaudeRecording = { [weak self] entry in self?.askClaudeAboutRecording(entry) }
        viewModel.onSendChat = { [weak self] text in self?.runChatTurn(text) }
        viewModel.onOpenRawTerminal = { [weak self] in self?.openRawTerminalPane() }
        // LED idle ticker — supply live stats on demand.
        viewModel.ledMatrix.idleProvider = { [weak self] content in
            guard let self = self else { return nil }
            let cal = Calendar.current
            switch content {
            case .clock:
                let f = DateFormatter(); f.dateFormat = "HH:mm"
                return f.string(from: Date())
            case .meetingsToday:
                let today = cal.startOfDay(for: Date())
                let n = self.viewModel.meetingActivityByDay[today]?.count ?? 0
                return n > 0 ? "\(n) TODAY" : nil
            case .streak:
                let s = self.meetingStreak()
                return s > 1 ? "\(s) DAY STREAK" : nil
            case .queue:
                let tq = self.viewModel.transcriptionQueue.filter { $0.status == .queued }.count
                let q = tq + self.summariseQueue.count
                return q > 0 ? "Q:\(q)" : nil
            }
        }
        // NB: don't start the LED matrix here — the LEDMatrixView starts it in
        // onAppear when it's actually shown, and events (notify/setRecording)
        // start it on demand for takeover. Starting at launch would spin the
        // idle ticker off-screen.
        viewModel.onViewSummary = { [weak self] path in
            self?.openSummaryViewer(summaryMdPath: path)
        }
        viewModel.onMergeSelected = { [weak self] in self?.mergeSelectedRecordings() }
        viewModel.onTrimRecording = { [weak self] path in self?.showTrimDialog(for: path) }
        viewModel.onShowTranscriptionQueue = { [weak self] in self?.showTranscriptionQueueWindow() }
        viewModel.onScanMergeCandidates = { [weak self] in self?.scanMergeCandidates() }
        viewModel.onMergeCandidate = { [weak self] cand in self?.executeMergeCandidate(cand) }
        viewModel.onDismissMergeCandidate = { [weak self] cand in self?.dismissMergeCandidate(cand) }
        viewModel.onMergeTickedCandidates = { [weak self] in self?.mergeTickedCandidates() }
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
        viewModel.onSetSummarizeEngine = { [weak self] id in self?.setSummarizeEngine(id) }
        viewModel.onSetShowCLIWhileSummarising = { [weak self] on in
            UserDefaults.standard.set(on, forKey: self?.showCLIWhileSummarisingKey ?? "showCLIWhileSummarising")
            self?.viewModel.showCLIWhileSummarising = on
        }
        viewModel.onShowTemplatesManager = { [weak self] in self?.openTemplatesManager() }
        viewModel.onIterateTemplate = { [weak self] url in self?.iterateTemplate(url) }
        viewModel.onCreateTemplate = { [weak self] in self?.createTemplateWithClaude() }
        viewModel.onShowDeviceManager = { [weak self] in self?.openDeviceManager() }
        viewModel.onForgetDevice = { [weak self] device in self?.forgetDevice(device) }
        viewModel.onPairVolume = { [weak self] volumeName, subpath in self?.pairVolume(volumeName: volumeName, subpath: subpath) }
        viewModel.onPairPlaud = { [weak self] region in self?.pairPlaud(region: region) }
        viewModel.onSignOutPlaud = { [weak self] device in self?.signOutPlaud(device) }
        viewModel.onScanVolumes = { [weak self] completion in self?.scanVolumes(completion: completion) }
        viewModel.onRefreshModelStatuses = { [weak self] in self?.refreshModelStatuses() }
        viewModel.onDownloadModelByKey = { [weak self] key in self?.downloadModelByKey(key) }
        viewModel.onDeleteModelByKey = { [weak self] key in self?.deleteModelByKey(key) }
        viewModel.onSetActiveModelByKey = { [weak self] key in self?.setActiveModelByKey(key) }
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
        let uptimeStr = formatUptime() ?? ""
        if viewModel.triggerUptime != uptimeStr { viewModel.triggerUptime = uptimeStr }
        // Anchor for the Mic Trigger row's self-ticking uptime label (changes
        // only on connect/disconnect, so this guarded write is rare).
        if viewModel.triggerConnectedSince != triggerConnectedSince {
            viewModel.triggerConnectedSince = triggerConnectedSince
        }
        viewModel.autoStartOnLaunch = autoStartOnLaunch
        viewModel.selectedMicName = selectedMicName
        // #3: don't enumerate CoreAudio devices on every state sync (this is
        // called from ~90 sites). The mic list only changes on a device-change
        // event, which updates it directly; throttle any stray refresh here.
        viewModel.availableMics = throttledMicNames()
        viewModel.syncBusy = syncBusy
        viewModel.syncDownloading = syncDownloading
        // #4: only push the (now ~1700-element) entries array when it actually
        // changed — an unchanged reassignment still fires @Published and marks
        // the derived-list cache dirty for nothing.
        // During launch, hold the entries push until the cache paint is ready so
        // the imported-only list doesn't flash up before the devices. Everything
        // else in this method still syncs normally.
        if syncEntriesVersion != lastPushedSyncEntriesVersion, !suppressSyncEntriesPush {
            viewModel.syncEntries = syncEntries
            lastPushedSyncEntriesVersion = syncEntriesVersion
        }
        viewModel.syncCheckedRecordings = syncCheckedRecordings
        viewModel.syncAutoDownload = syncAutoDownload
        viewModel.syncAutoTranscribe = syncAutoTranscribe
        viewModel.syncAutoSummarise = syncAutoSummarise
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
        viewModel.currentlyDownloadingName = currentlyDownloadingName
        viewModel.triggerHealthy = triggerHealthy
        viewModel.triggerWaitMessage = triggerWaitMessage
        viewModel.triggerLastStartedAt = triggerLastStartedAt
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

    /// Flip the trigger to "Active" green, but only after the
    /// minAmberDuration window has elapsed since process spawn. This
    /// makes restart cycles visible to the eye even when the CLI emits
    /// "Using HiDock audio device" sub-second after spawn (which
    /// happens whenever both devices were already enumerated). Also
    /// captures triggerLastStartedAt so the UI's "↻ HH:MM:SS" stamp
    /// updates on every restart, and fires the "Active" notification
    /// (gated to the user's preferred mic so a transient fallback
    /// doesn't double-notify).
    private func confirmTriggerHealthy(deviceName: String) {
        pendingHealthyTimer?.invalidate()
        let elapsed = processStartDate.map { Date().timeIntervalSince($0) } ?? Self.minAmberDuration
        let remaining = max(0, Self.minAmberDuration - elapsed)
        let fire: () -> Void = { [weak self] in
            guard let self = self else { return }
            // Drop the flip if the process exited in the meantime.
            guard self.process != nil else { return }
            let micName = self.selectedMicName ?? ""
            self.triggerHealthy = true
            if self.triggerConnectedSince == nil { self.triggerConnectedSince = Date() }
            self.triggerLastStartedAt = self.processStartDate
            self.syncViewModelState()
            let preferredOK = self.preferredMicName == nil
                || (self.preferredMicName ?? "").isEmpty
                || self.preferredMicName == self.selectedMicName
            if preferredOK, !micName.isEmpty {
                self.log("Trigger Active — \(shortenMicName(micName)) → \(deviceName)")
                self.postNotification(
                    title: "HiDock Mic Trigger Active",
                    body: "Watching \(shortenMicName(micName)); \(deviceName) ready to record."
                )
            } else {
                self.log("Trigger healthy on fallback mic '\(micName)' — suppressing 'Active' notification (preferred is '\(self.preferredMicName ?? "?")')")
            }
        }
        if remaining <= 0 {
            fire()
        } else {
            pendingHealthyTimer = Timer.scheduledTimer(withTimeInterval: remaining, repeats: false) { _ in fire() }
        }
    }

    private func handleCLIOutput(_ text: String) {
        for line in text.components(separatedBy: .newlines) {
            // Capture which HiDock audio device ffmpeg is attached to,
            // so the Recording chip can appear on only the matching
            // device card. MicTrigger prints this once on startup:
            //   "Using HiDock audio device: HiDock H1"
            // This is also our "trigger is healthy and polling" signal —
            // the CLI only reaches this print after both the USB mic and
            // the HiDock have been resolved by waitForDevice.
            if let range = line.range(of: "Using HiDock audio device: ") {
                let name = line[range.upperBound...].trimmingCharacters(in: .whitespaces)
                if !name.isEmpty {
                    log("Trigger: recording device = '\(name)' — confirmed (will flip Active after minAmberDuration)")
                    DispatchQueue.main.async {
                        self.viewModel.hidockRecordingDeviceName = name
                        // triggerWaitMessage cleared immediately — we
                        // know the wait ended. triggerHealthy waits.
                        self.triggerWaitMessage = nil
                        self.confirmTriggerHealthy(deviceName: name)
                    }
                }
            }
            // CLI's waitForDevice loop announces what it's waiting on.
            // Surface it in the UI so the user can see why the trigger
            // is alive but not yet healthy (e.g. dock not plugged in).
            if line.hasPrefix("Waiting for ") || line.hasPrefix("Still waiting for ") {
                let msg = String(line)
                log("Trigger: \(msg)")
                DispatchQueue.main.async {
                    self.triggerWaitMessage = msg
                    // Waiting again (device gone) — stop the connected timer.
                    self.triggerConnectedSince = nil
                    self.syncViewModelState()
                }
            } else if line.contains(" appeared after ") {
                log("Trigger: \(line)")
                DispatchQueue.main.async {
                    self.triggerWaitMessage = nil
                    // Reconnected — restart the connected timer from now.
                    self.triggerConnectedSince = Date()
                    self.syncViewModelState()
                }
            }
            if line.contains("IN USE") && line.contains("holding HiDock") {
                log("Trigger: USB mic in use, HiDock recording started")
                postNotification(title: "HiDock Recording Started", body: "USB mic is in use — HiDock input held open.")
                DispatchQueue.main.async {
                    self.viewModel.hidockRecordingActive = true
                    self.viewModel.ledMatrix.setRecording(true)
                }
            } else if line.contains("NOT IN USE") && line.contains("releasing HiDock") {
                log("Trigger: USB mic idle, HiDock recording stopped")
                postNotification(title: "HiDock Recording Stopped", body: "USB mic went idle — HiDock input released.")
                DispatchQueue.main.async {
                    self.viewModel.hidockRecordingActive = false
                    self.viewModel.ledMatrix.setRecording(false)
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

    /// Re-enumerate mics now and refresh the cache — call on device-change /
    /// setup where the list genuinely may have changed.
    private func refreshMicNamesNow() {
        cachedMicNames = getInputDeviceNames()
        lastMicEnumeration = Date()
        viewModel.availableMics = cachedMicNames
    }

    /// Cached mic names, re-enumerated at most every 5s. Used on the hot
    /// syncViewModelState() path so we don't hit CoreAudio on every call.
    private func throttledMicNames() -> [String] {
        if cachedMicNames.isEmpty || Date().timeIntervalSince(lastMicEnumeration) > 5 {
            cachedMicNames = getInputDeviceNames()
            lastMicEnumeration = Date()
        }
        return cachedMicNames
    }

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
            // Flap-storm detection: if the device list changed ≥3 times in the
            // last 30s, the USB audio bus is re-enumerating under contention.
            // Back off HiDock probing for 25s so we stop piling status probes
            // onto the contended interface (which fights the mic trigger).
            let now = Date()
            recentDeviceChanges.append(now)
            recentDeviceChanges.removeAll { now.timeIntervalSince($0) > 30 }
            if recentDeviceChanges.count >= 3 {
                if !inUsbFlapBackoff {
                    log("USB flap storm detected (\(recentDeviceChanges.count) changes/30s) — backing off HiDock probes for 25s")
                }
                usbFlapBackoffUntil = now.addingTimeInterval(25)
            }

            refreshMicNamesNow()
            if syncPaired && !syncBusy {
                log("USB device change detected, refreshing sync status")
                autoConnectSyncIfPaired(usbTriggered: true)
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
            refreshMicNamesNow()
            updateMenuState()
            if process == nil {
                // Same recovery as selectMic: if the trigger died on a
                // previous fallback mic (e.g. it crashed-out trying to
                // open a built-in name) and autoStartOnLaunch is on,
                // the preferred-mic-reconnect event is a natural moment
                // to retry. Without this, plugging Samson back in just
                // logs "auto-switching" and does nothing.
                if autoStartOnLaunch {
                    log("Preferred-mic auto-switch: no running trigger — starting fresh with '\(preferred)'")
                    startTrigger()
                }
            } else if preferred != oldMic {
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
                refreshMicNamesNow()
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

        // Generic recovery: if there's no running trigger, autoStartOnLaunch
        // is on, and the currently-selected mic is now present in the
        // device list, start a fresh trigger. Catches the case the
        // auto-switch block above misses — when the selected mic equals
        // the preferred mic (so `selected != preferred` is false), the
        // auto-switch block returns without starting. Without this
        // clause, the trigger would stay dead even though everything is
        // back to normal.
        if process == nil, autoStartOnLaunch,
           let selected = selected, !selected.isEmpty,
           currentDevices.contains(selected) {
            log("USB change recovery: trigger not running, '\(selected)' is present — starting")
            startTrigger()
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

        summarizeSubmenu = NSMenu()
        summarizeSubmenu.delegate = self
        summarizeMenuItem = NSMenuItem(title: "Summarisation Provider", action: nil, keyEquivalent: "")
        summarizeMenuItem.submenu = summarizeSubmenu

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
        menu.addItem(summarizeMenuItem)
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
        let buildLibraryItem = NSMenuItem(title: "Build Voice Library from Tagged Meetings", action: #selector(buildVoiceLibraryMenu), keyEquivalent: "")
        buildLibraryItem.target = self
        menu.addItem(buildLibraryItem)
        let rematchAllItem = NSMenuItem(title: "Re-match Untagged Meetings", action: #selector(rematchUntaggedMenu), keyEquivalent: "")
        rematchAllItem.target = self
        menu.addItem(rematchAllItem)
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
        if process == nil {
            // Trigger isn't running — either it was never started, or
            // it crashed past max retries on a previous mic (typical
            // path: USB mic unplugged → fallback to a built-in name
            // CoreAudio doesn't expose with that exact spelling → 3
            // crashes → giving up). Picking a mic from the menu is an
            // explicit "make it watch this one" gesture, so start a
            // fresh attempt with the new selection rather than silently
            // dropping it. Respects autoStartOnLaunch so users who have
            // explicitly disabled auto-start aren't second-guessed.
            if autoStartOnLaunch {
                log("selectMic: no running trigger — starting fresh with '\(micName)'")
                startTrigger()
            }
        } else if micName != oldMic {
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
        // Time CONNECTED, not time since the process started — nil while
        // waiting for a device (so the readout disappears rather than ticking
        // up before anything's connected).
        guard let start = triggerConnectedSince else { return nil }
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
        // Menu-bar fallback for the transcribed badge (the Dock badge only shows
        // in .regular mode / when a window is open).
        if viewModel.sessionTranscribedCount > 0 {
            title += " · ✓\(viewModel.sessionTranscribedCount)"
        }
        statusItem.button?.title = title
        syncViewModelState()
    }

    /// Reflect the session transcribed count on the Dock icon (badge) and the
    /// menu-bar title. Call after the count changes.
    private func updateTranscribedBadge() {
        let n = viewModel.sessionTranscribedCount
        NSApp.dockTile.badgeLabel = n > 0 ? String(n) : nil
        updateMenuState()
    }

    /// Focusing the main window means the user has seen recent completions —
    /// clear the transcribed activity badge.
    func windowDidBecomeKey(_ notification: Notification) {
        guard let win = notification.object as? NSWindow, win === syncWindow else { return }
        if viewModel.sessionTranscribedCount != 0 {
            viewModel.sessionTranscribedCount = 0
            updateTranscribedBadge()
        }
    }

    private func startUptimeTimer() {
        guard uptimeTimer == nil else { return }
        uptimeTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            // Menu-bar item only (AppKit, no SwiftUI). The Mic Trigger row's
            // uptime is now driven by a local TimelineView off
            // viewModel.triggerConnectedSince, so we deliberately do NOT write
            // any @Published state here — that used to re-render the whole
            // window (incl. the 1700-row table) once a second.
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
        // Fresh launch — clear any stale healthy/wait state from a
        // previous run before the CLI even starts emitting output.
        triggerHealthy = false
        triggerConnectedSince = nil
        triggerWaitMessage = nil
        pendingHealthyTimer?.invalidate()
        pendingHealthyTimer = nil

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

        // Line-buffer the CLI's stdout across chunks — availableData can
        // split a line mid-marker ("Using HiDock audio device: …",
        // "IN USE … holding HiDock"), and a missed marker leaves sticky
        // wrong state (e.g. hidockRecordingActive stuck true, which then
        // suppresses every HiDock probe). Same pattern as the extractor
        // runners' lineBuffer. Buffer is mutated on the main queue only.
        var lineBuffer = ""
        outPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            DispatchQueue.main.async {
                lineBuffer += text
                var lines: [String] = []
                while let range = lineBuffer.range(of: "\n") {
                    lines.append(String(lineBuffer[lineBuffer.startIndex..<range.lowerBound]))
                    lineBuffer = String(lineBuffer[range.upperBound...])
                }
                if !lines.isEmpty {
                    self?.handleCLIOutput(lines.joined(separator: "\n"))
                }
            }
        }

        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async {
                guard let self = self else { return }
                let status = proc.terminationStatus
                self.log("Process terminated with status \(status)")

                outPipe.fileHandleForReading.readabilityHandler = nil

                // Only mutate shared trigger state if this handler still
                // owns self.process. stopTrigger/restartTrigger race this
                // block via their own waitUntilExit continuations (both
                // dispatch async to main with no ordering guarantee): if
                // the continuation ran first it has already nil'd process
                // — and restartTrigger may have started a NEW child.
                // Without this guard, the stale handler would orphan that
                // new process from the app's tracking, and (because the
                // continuation also cleared stoppingIntentionally) treat
                // a user-requested stop as a crash and auto-restart it.
                guard proc === self.process else { return }

                self.process = nil
                self.processStartDate = nil
                self.triggerHealthy = false
                self.triggerConnectedSince = nil
                self.triggerWaitMessage = nil
                self.pendingHealthyTimer?.invalidate()
                self.pendingHealthyTimer = nil
                self.stopUptimeTimer()
                self.updateMenuState()
                self.syncViewModelState()

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
                self?.viewModel.ledMatrix.setRecording(false)
                self?.viewModel.hidockRecordingDeviceName = nil
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

    /// Paint the recordings table from state.json's cached catalog for
    /// each paired HiDock, before autoConnectSyncIfPaired fires its
    /// live USB probes. The extractor's `cached-status` command reads
    /// only from state.json (no USB) and returns in ~60ms, so the user
    /// sees the full list of already-downloaded + transcribed rows
    /// immediately on launch instead of watching them appear device-by-
    /// device as live probes resolve.
    ///
    /// Follow-up: `refreshTranscriptionState` fires here so the Status
    /// column lands on "Transcribed" directly — no initial "Downloaded"
    /// → "Transcribed" flicker.
    ///
    /// The live probe path (autoConnectSyncIfPaired) still runs after
    /// this; its renderSyncStatus call replaces the cached rows with
    /// fresh device-returned data when it completes, which is fine —
    /// preserves any new recordings that have appeared on the device
    /// since state.json was last written.
    private func loadCachedCatalogsForPaintOnLaunch(onComplete: @escaping () -> Void = {}) {
        let hidocks = syncPairedDevices.filter { $0.deviceType == .hidock }
        // Plaud accounts paint from cache too, so already-downloaded recordings
        // show instantly on launch instead of waiting for the (slow, networked)
        // live cloud probe to connect.
        let plauds = syncPairedDevices.filter { $0.deviceType == .plaud }
        // No paired devices → nothing cached to load, but still paint the
        // imported rows (which the launch path deliberately didn't push yet).
        guard !hidocks.isEmpty || !plauds.isEmpty, ensureExtractorReady() else {
            suppressSyncEntriesPush = false
            viewModel.recordingsLoading = false
            applyTranscribedFromDiskScan()
            viewModel.syncEntries = syncEntries
            refreshTranscriptionState()
            syncViewModelState()
            onComplete()
            return
        }
        log("Paint-from-cache: \(hidocks.count) HiDock(s), \(plauds.count) Plaud account(s)")
        let group = DispatchGroup()
        // Collect every device's cached catalog, then render them all together in
        // one pass — the list appears at once instead of trickling in device by
        // device as each subprocess returns.
        var cached: [(HiDockPairedDevice, HiDockSyncStatusResponse)] = []
        let cacheLock = NSLock()
        for device in hidocks {
            group.enter()
            runCachedExtractor(arguments: ["cached-status"], productId: device.productId) { [weak self] result in
                defer { group.leave() }
                guard let self = self else { return }
                guard case .success(let data) = result,
                      let payload = try? JSONDecoder().decode(HiDockSyncStatusResponse.self, from: data) else {
                    self.log("Paint-from-cache: no cached data for \(device.cleanName)")
                    return
                }
                cacheLock.lock(); cached.append((device, payload)); cacheLock.unlock()
            }
        }
        for device in plauds {
            group.enter()
            // Network-free cached read — no tokens needed, so pass an empty env
            // (avoids the signed-out side effect of plaudEnvironment).
            runCachedExtractor(arguments: plaudExtractorArguments("plaud-cached-status", device: device),
                               productId: device.productId,
                               environment: [:]) { [weak self] result in
                defer { group.leave() }
                guard let self = self else { return }
                guard case .success(let data) = result,
                      let payload = try? JSONDecoder().decode(HiDockSyncStatusResponse.self, from: data) else {
                    self.log("Paint-from-cache: no cached Plaud data for \(device.cleanName)")
                    return
                }
                cacheLock.lock(); cached.append((device, payload)); cacheLock.unlock()
            }
        }
        group.notify(queue: .main) { [weak self] in
            guard let self = self else { return }
            // Keep the push suppressed THROUGH the render loop (renderSyncStatus
            // may sync internally) so no device paints alone; release it just
            // before the single combined push below.
            for (device, payload) in cached { self.renderSyncStatus(payload, device: device) }
            self.suppressSyncEntriesPush = false
            self.viewModel.recordingsLoading = false
            self.log("Paint-from-cache: \(cached.count) cached catalog(s) loaded together, refreshing transcription state")
            // Two-phase transcribed-state population: the sync disk
            // scan below lands the table on Transcribed immediately
            // (prevents Downloaded→Transcribed flash), then the async
            // Python refresh fills in speakersTagged / summary paths
            // that the filesystem scan can't tell us.
            self.applyTranscribedFromDiskScan()
            self.viewModel.syncEntries = self.syncEntries
            self.refreshTranscriptionState()
            self.syncViewModelState()
            // Live probes run AFTER the full cache paint — they update rows in
            // place instead of the fast H1 probe landing before the cache and
            // making devices appear one-by-one.
            onComplete()
        }
    }

    /// - Parameter usbTriggered: true when called from a USB/CoreAudio
    ///   device-change event. Plaud is an API account, not a physical USB
    ///   device, so a USB change tells us nothing about it — we skip re-probing
    ///   Plaud in that case (it's refreshed on app open and manual refresh).
    ///   This also stops a USB audio blip from needlessly re-fetching the Plaud
    ///   catalog and rebuilding its rows.
    private func autoConnectSyncIfPaired(startTriggerOnCompletion: Bool = false, usbTriggered: Bool = false) {
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

        // Decouple mic-trigger startup from the (potentially slow, ~25s)
        // catalog probe. The trigger is the app's core job and only needs the
        // device present, not the full file list — waiting for the probe meant
        // the mic wasn't armed for ~25s after launch (and recordings started in
        // that window could be missed). Arm it now; the probe still runs below
        // and paintFromCache has already populated the table, so the recordings
        // UI isn't blocked either. The completion block keeps its own
        // `process == nil`-guarded start as a fallback (no-op once armed here).
        if startTriggerOnCompletion && autoStartOnLaunch && process == nil {
            log("Auto-connect: arming mic trigger immediately (catalog probe continues in background)")
            startTrigger()
        }

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
            let flapBackoff = usbTriggered && self.inUsbFlapBackoff
            let probeDevices = devices.filter { device in
                // Plaud is an API, not a USB device — a USB/audio device change
                // is irrelevant to it, so don't re-probe it on USB churn.
                if usbTriggered && device.deviceType == .plaud {
                    self.log("Auto-connect: skipping Plaud \(device.cleanName) — USB-change trigger doesn't affect an API account")
                    return false
                }
                if device.deviceType == .volume { return true }   // separate interface, always safe
                // During a USB flap storm, don't probe HiDock over USB — the bus
                // is re-enumerating under contention and another status probe
                // just fights the mic trigger and prolongs the churn.
                if flapBackoff, device.deviceType == .hidock {
                    self.log("Auto-connect: skipping \(device.cleanName) — USB flap back-off active")
                    return false
                }
                if device.deviceType == .plaud { return true }
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
            case .plaud:
                args = self.plaudExtractorArguments("plaud-status", device: device)
                pid = nil
            }

            self.runExtractor(arguments: args, productId: pid, environment: self.plaudEnvironment(for: device)) { [weak self] result in
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
                // Suppress the global amber when every paired device just
                // isn't plugged in. The per-device cards already render
                // "Not connected" themselves, and a global "HiDock device
                // not found" banner on top of that is redundant noise —
                // it implies something's wrong when in fact the user
                // simply hasn't plugged in. Surface the global warning
                // only for genuinely unusual errors (held-by-another-
                // process, access denied, USB busy, etc.).
                let allJustMissing = deviceErrors.values.allSatisfy {
                    $0.localizedCaseInsensitiveContains("not found")
                        && !$0.contains("held by")
                        && !$0.localizedCaseInsensitiveContains("access denied")
                        && !$0.contains("Errno 13")
                }
                if !allJustMissing {
                    let bestError = deviceErrors.values.first(where: { $0.contains("held by") })
                        ?? deviceErrors.values.first ?? "unknown"
                    let message = syncErrorDescription(bestError)
                    self.viewModel.syncStatus = message
                    self.viewModel.syncStatusLevel = .warning
                }
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
            .filter { $0.recording.localExists }
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

            // Pre-flight: ffmpeg's concat demuxer parses the list with
            // single-quoted paths, so any path containing a `'`, newline,
            // backslash, or NUL would break the format and could let
            // ffmpeg interpret the rest of the line as metadata or a
            // filter directive. Today every path in `entries` is built
            // from `outputFolder + sanitised HiDock filename`, so this
            // check is defence-in-depth against (a) a future code path
            // that lets a less-strict source reach this list, and (b) a
            // user-chosen `outputFolder` that itself contains a quote.
            // We keep `-safe 0` because our paths are absolute and
            // `-safe 1` would reject all of them, breaking Merge.
            let unsafePathChars = CharacterSet(charactersIn: "'\n\r\\\0")
            let allPaths = entries.map(\.recording.outputPath) + [outputPath]
            if let bad = allPaths.first(where: { $0.rangeOfCharacter(from: unsafePathChars) != nil }) {
                DispatchQueue.main.async {
                    self.viewModel.syncStatus = "Merge aborted (unsafe path)"
                    self.viewModel.syncStatusLevel = .error
                    self.showError("Merge aborted — refusing to pass a path with quote/newline/NUL/backslash to ffmpeg:\n\n\(bad)")
                    self.syncViewModelState()
                }
                return
            }

            // Write concat list to temp file
            let listPath = NSTemporaryDirectory() + "hidock-merge-list.txt"
            let listContent = entries.map { "file '\($0.recording.outputPath)'" }.joined(separator: "\n")
            try? listContent.write(toFile: listPath, atomically: true, encoding: .utf8)

            let process = Process()
            process.executableURL = URL(fileURLWithPath: self.ffmpegPath)
            process.arguments = ["-y", "-f", "concat", "-safe", "0", "-i", listPath, "-c", "copy", outputPath]
            process.standardOutput = FileHandle.nullDevice
            // Capture stderr so failures surface a real message via the
            // same pattern Trim uses, instead of an opaque exit code.
            let errPipe = Pipe()
            process.standardError = errPipe
            var stderrData = Data()
            let errQueue = DispatchQueue(label: "hidock.merge.stderr")
            errPipe.fileHandleForReading.readabilityHandler = { handle in
                let chunk = handle.availableData
                guard !chunk.isEmpty else { return }
                errQueue.sync { stderrData.append(chunk) }
            }

            do {
                try process.run()
                process.waitUntilExit()
            } catch {
                errPipe.fileHandleForReading.readabilityHandler = nil
                DispatchQueue.main.async {
                    self.viewModel.syncStatus = "Merge failed"
                    self.viewModel.syncStatusLevel = .error
                    self.showError("Merge failed: \(error.localizedDescription)")
                    self.syncViewModelState()
                }
                return
            }
            errPipe.fileHandleForReading.readabilityHandler = nil
            let tail = errPipe.fileHandleForReading.readDataToEndOfFile()
            if !tail.isEmpty { errQueue.sync { stderrData.append(tail) } }
            let stderrText = errQueue.sync { String(data: stderrData, encoding: .utf8) ?? "" }

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
                    // Build the merged transcript. Originally I queued
                    // a fresh full-Whisper pass against the merged
                    // audio (re-transcribe). On reflection that was
                    // overkill: Whisper output is essentially
                    // deterministic, so a fresh pass produces the same
                    // words as concatenating each piece's existing
                    // _whisper.json. The ONE thing the merge actually
                    // needs is cross-segment speaker continuity, which
                    // is purely a diarization concern. So we now stitch
                    // the per-piece whisper JSONs (with cumulative
                    // timestamp offsets) and only re-run the
                    // diarization stage on the merged audio.
                    // Saves ~10–15 minutes per 60-min merge with
                    // identical text quality.
                    //
                    // If any child is missing its _whisper.json (e.g.
                    // never transcribed), fall back to the original
                    // full-Whisper enqueue — better than failing.
                    if self.ensureTranscriptionReady() {
                        let allChildrenHaveWhisperJson = entries.allSatisfy { entry in
                            let stem = (entry.recording.outputName as NSString).deletingPathExtension
                            let dir = self.syncTranscriptFolder ?? "\(NSHomeDirectory())/HiDock/Raw Transcripts"
                            return FileManager.default.fileExists(atPath: "\(dir)/\(stem)_whisper.json")
                        }
                        if allChildrenHaveWhisperJson {
                            self.log("Merge: stitching \(entries.count) per-piece transcripts + rediarize on \(outputPath)")
                            self.runMergeRediarize(mergedPath: outputPath, pieceEntries: entries)
                        } else {
                            self.log("Merge: at least one child has no _whisper.json — falling back to full re-transcribe")
                            self.enqueueTranscriptions([outputPath])
                        }
                    }
                    // Finder pop-out removed — distracting and
                    // unnecessary now that the merged row is visible
                    // in the table. Right-click → Show in Finder
                    // remains available.
                } else {
                    self.viewModel.syncStatus = "Merge failed"
                    self.viewModel.syncStatusLevel = .error
                    let lastLines = stderrText
                        .split(separator: "\n")
                        .suffix(6)
                        .joined(separator: "\n")
                    self.log("Merge failed (exit \(process.terminationStatus)):\n\(stderrText)")
                    let detail = lastLines.isEmpty
                        ? "ffmpeg exited with status \(process.terminationStatus) (no stderr output)"
                        : "ffmpeg exited with status \(process.terminationStatus):\n\n\(lastLines)"
                    self.showError(detail)
                }
                self.syncViewModelState()
            }
        }
    }

    // MARK: - Merge candidate detection

    /// Subprocess `extractor.py merge-candidates --include-low-confidence`
    /// and decode the chains into the view model. Cheap (~1s on a few
    /// hundred recordings) so we can fire it on launch + after every
    /// auto-transcribe completion. The toggle in the UI decides
    /// whether to surface only high-confidence or all chains.
    private func scanMergeCandidates() {
        guard ensureExtractorReady() else { return }
        runExtractor(arguments: ["merge-candidates", "--include-low-confidence"], productId: nil) { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success(let data):
                guard let payload = try? JSONDecoder().decode(MergeCandidatesPayload.self, from: data) else {
                    self.log("merge-candidates: failed to decode JSON (\(data.count) bytes)")
                    return
                }
                DispatchQueue.main.async {
                    self.viewModel.mergeCandidates = payload.chains
                    self.log("merge-candidates: \(payload.high_confidence_count) high-conf, \(payload.total_count) total")
                }
            case .failure(let err):
                self.log("merge-candidates failed: \(err.localizedDescription)")
            }
        }
    }

    /// One-click merge action from the candidate sheet. Reuses the
    /// existing merge flow by constructing entries that match
    /// `mergeSelectedRecordings`'s expectations and routing through
    /// the same code path — so users get the same auto-rediarize
    /// transcript output they'd get from a manual select-and-merge.
    private func executeMergeCandidate(_ cand: MergeCandidate) {
        let names = Set(cand.pieces.map { ($0.mp3_name as NSString).deletingPathExtension }
                        .map { "\($0).hda" })
        let entries = syncEntries.filter { entry in
            names.contains(entry.recording.name) && entry.recording.localExists
        }
        guard entries.count == cand.pieces.count else {
            log("Merge candidate aborted: expected \(cand.pieces.count) entries, found \(entries.count)")
            showError("Merge aborted — could not locate all pieces in the current table. Try Refresh first.")
            return
        }
        // Pre-populate the checked set so the existing merge code path
        // picks these up. mergeSelectedRecordings reads from
        // syncCheckedRecordings, so this single-line reuse keeps the
        // logic in one place.
        syncCheckedRecordings = Set(entries.map(\.recording.name))
        log("Merge candidate \(cand.pair_key): firing merge on \(cand.pieces.count) pieces")
        mergeSelectedRecordings()
    }

    /// Merge whatever the user has ticked across candidate rows. The
    /// ticks are explicit "include this in the merge" decisions, so
    /// we don't filter by chain — if the user ticked rows from
    /// different detected chains, we still respect their choice.
    /// Pre-condition: `canMergeTickedCandidates` (≥2 ticks).
    private func mergeTickedCandidates() {
        let paths = viewModel.mergeCandidatesTicked
        guard paths.count >= 2 else { return }
        let entries = syncEntries.filter {
            paths.contains($0.recording.outputPath)
                && $0.recording.localExists
        }
        guard entries.count == paths.count else {
            log("Merge ticked: expected \(paths.count) entries, found \(entries.count)")
            showError("Merge aborted — could not locate all ticked rows in the current table. Try Refresh first.")
            return
        }
        // Find every candidate chain that overlaps with the ticked
        // paths — once we merge, those chains shouldn't keep
        // suggesting themselves. The children files stay on disk +
        // their transcripts stay on disk, so the detector would
        // re-flag the same chain on next scan unless we explicitly
        // dismiss it. dismissChain is sticky (persisted to
        // merge_candidates.json), which is what we want here: the
        // user explicitly accepted the suggestion, that's the strongest
        // possible signal that we shouldn't surface it again.
        let chainsToDismiss = viewModel.mergeCandidates.filter { cand in
            cand.pieces.contains { paths.contains($0.mp3_path) }
        }

        // Reuse the existing merge plumbing — it reads from
        // syncCheckedRecordings and runs the full ffmpeg + rediarize
        // path. Keeps merging logic in one place.
        syncCheckedRecordings = Set(entries.map(\.recording.name))
        log("Merge ticked: \(entries.count) candidate rows; will dismiss \(chainsToDismiss.count) overlapping chain(s)")
        mergeSelectedRecordings()

        // Clear ticks immediately so the toolbar collapses back to
        // "merge suggestions" or hides entirely.
        viewModel.mergeCandidatesTicked.removeAll()

        // Optimistically remove the dismissed chains from the local
        // candidate list so the row highlights / toolbar count update
        // without waiting for the next rescan to re-fetch.
        let dismissedKeys = Set(chainsToDismiss.map(\.pair_key))
        viewModel.mergeCandidates.removeAll { dismissedKeys.contains($0.pair_key) }

        // Persist the dismissals so the next merge-candidates scan
        // (which fires after the transcription queue drains) doesn't
        // resurface them.
        for cand in chainsToDismiss {
            dismissMergeCandidate(cand)
        }
    }

    /// Sticky dismissal — calls `extractor.py dismiss-merge-pair` so
    /// the chain stops re-appearing on rescans, then refreshes the
    /// candidate list locally.
    private func dismissMergeCandidate(_ cand: MergeCandidate) {
        let names = cand.pieces.map(\.device_name)
        runExtractor(arguments: ["dismiss-merge-pair"] + names, productId: nil) { [weak self] _ in
            DispatchQueue.main.async {
                self?.scanMergeCandidates()
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
        // NSWindow defaults to isReleasedWhenClosed = true, which would
        // let AppKit drop the window during close() while ARC still has
        // a strong ref via `trimWindow` AND the in-flight close
        // animation (_NSWindowTransformAnimation) still expects the
        // window to be live. That's the double-free that crashed the
        // app in objc_release during the next autorelease pool pop
        // after clicking Trim. Every other NSWindow in this codebase
        // sets this flag — this one was the outlier.
        window.isReleasedWhenClosed = false
        window.title = "Trim Audio"
        window.contentView = hostingView
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        trimWindow = window
    }

    /// After an overwrite-trim succeeds, replace the matching
    /// `HiDockSyncRecording` in `syncEntries` with one whose `length`
    /// (byte size) and `duration` reflect the newly-trimmed local
    /// file. We don't call refreshSyncStatus here because that would
    /// re-read the device catalog, which still reports the original
    /// (pre-trim) metadata — the recording on the HiDock itself isn't
    /// touched by a local trim. `humanLength` is recomputed from the
    /// new byte count so the Size column agrees with the file on disk.
    private func updateEntryAfterOverwriteTrim(path: String, newDuration: Double) {
        guard let idx = syncEntries.firstIndex(where: { $0.recording.outputPath == path }) else { return }
        let newBytes: Int = {
            guard let attrs = try? FileManager.default.attributesOfItem(atPath: path),
                  let n = attrs[.size] as? Int else { return 0 }
            return n
        }()
        let old = syncEntries[idx].recording
        let humanLength: String
        if newBytes >= 1_048_576 {
            humanLength = String(format: "%.1f MB", Double(newBytes) / 1_048_576.0)
        } else if newBytes >= 1024 {
            humanLength = String(format: "%.0f KB", Double(newBytes) / 1024.0)
        } else {
            humanLength = "\(newBytes) B"
        }
        let updated = HiDockSyncRecording(
            name: old.name,
            createDate: old.createDate,
            createTime: old.createTime,
            length: newBytes,
            duration: newDuration,
            version: old.version,
            mode: old.mode,
            signature: old.signature,
            outputPath: old.outputPath,
            outputName: old.outputName,
            downloaded: old.downloaded,
            localExists: old.localExists,
            downloadedAt: old.downloadedAt,
            lastError: old.lastError,
            status: old.status,
            humanLength: humanLength,
            trimmed: true,  // instant UI feedback — persist via mark-trimmed below
            durationEstimated: false,  // we just trimmed it; the new duration is `end - start`
            removed: old.removed  // preserve any prior removed flag (typically nil/false)
        )
        let existing = syncEntries[idx]
        syncEntries[idx] = HiDockSyncRecordingEntry(
            recording: updated,
            deviceProductId: existing.deviceProductId,
            deviceId: existing.deviceId,
            deviceName: existing.deviceName,
            transcribed: existing.transcribed,
            transcriptPath: existing.transcriptPath,
            transcribedDate: existing.transcribedDate,
            speakersTagged: existing.speakersTagged,
            speakersAutoMatched: existing.speakersAutoMatched,
            summaryPath: existing.summaryPath,
            transcriptionSkipped: existing.transcriptionSkipped
        )
        viewModel.syncEntries = syncEntries
        log("updateEntryAfterOverwriteTrim: \(old.outputName) length=\(old.length)→\(newBytes), duration=\(String(format: "%.1f", old.duration))→\(String(format: "%.1f", newDuration))")

        // Persist the trimmed flag in state.json so it survives app
        // restarts and is visible to subsequent `status` calls — the
        // in-memory update above is for instant paint; this is the
        // source of truth.
        let device = syncPairedDevices.first { $0.deviceId == syncEntries[idx].deviceId }
        let pid = device?.productId
        runExtractor(arguments: ["mark-trimmed", old.name], productId: pid) { [weak self] result in
            guard let self = self else { return }
            if case .failure(let err) = result {
                self.log("mark-trimmed failed for \(old.name): \(err.localizedDescription)")
            }
        }
    }

    private func trimRecording(path: String, start: Double, end: Double, saveAsCopy: Bool) {
        let url = URL(fileURLWithPath: path)
        let outputPath: String
        if saveAsCopy {
            let stem = url.deletingPathExtension().lastPathComponent
            let dir = url.deletingLastPathComponent().path
            outputPath = "\(dir)/\(stem)-trimmed.mp3"
        } else {
            // Keep the `.mp3` extension on the temp path — the earlier
            // `path + ".tmp"` form ended in `.mp3.tmp`, and ffmpeg
            // couldn't guess the container format from `.tmp`, failing
            // with "Unable to choose an output format" (exit 234). A
            // hidden dotfile sibling avoids showing a transient file
            // in the Recordings folder listing.
            let basename = url.lastPathComponent
            let dir = url.deletingLastPathComponent().path
            outputPath = "\(dir)/.\(basename).trim-tmp.mp3"
        }

        viewModel.trimBusy = true
        syncViewModelState()

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            let process = Process()
            process.executableURL = URL(fileURLWithPath: self.ffmpegPath)
            // Re-encode instead of `-c copy`. Stream-copying MP3 with
            // `-ss`/`-to` was exiting 234 (EINVAL) because MP3 frames
            // can't be split at arbitrary byte offsets — ffmpeg lands
            // mid-frame and bails. libmp3lame re-encode is frame-exact
            // and is available in the standard Homebrew ffmpeg build.
            // `-q:a 2` picks VBR ~190kbps, comparable quality to the
            // HiDock source, and is fast on local CPU.
            process.arguments = [
                "-y", "-i", path,
                "-ss", String(format: "%.2f", start),
                "-to", String(format: "%.2f", end),
                "-c:a", "libmp3lame", "-q:a", "2",
                "-f", "mp3",  // explicit container — don't rely on output extension
                outputPath
            ]
            process.standardOutput = FileHandle.nullDevice
            let errPipe = Pipe()
            process.standardError = errPipe
            // Drain the pipe concurrently; otherwise ffmpeg blocks on
            // a full stderr buffer partway through a long file.
            var stderrData = Data()
            let errQueue = DispatchQueue(label: "hidock.trim.stderr")
            errPipe.fileHandleForReading.readabilityHandler = { handle in
                let chunk = handle.availableData
                guard !chunk.isEmpty else { return }
                errQueue.sync { stderrData.append(chunk) }
            }

            do {
                try process.run()
                process.waitUntilExit()
            } catch {
                errPipe.fileHandleForReading.readabilityHandler = nil
                DispatchQueue.main.async {
                    self.viewModel.trimBusy = false
                    self.viewModel.syncStatus = "Trim failed"
                    self.viewModel.syncStatusLevel = .error
                    self.showError("Trim failed: \(error.localizedDescription)")
                    self.syncViewModelState()
                }
                return
            }
            errPipe.fileHandleForReading.readabilityHandler = nil
            let tail = errPipe.fileHandleForReading.readDataToEndOfFile()
            if !tail.isEmpty { errQueue.sync { stderrData.append(tail) } }
            let stderrText = errQueue.sync { String(data: stderrData, encoding: .utf8) ?? "" }

            // If replacing original, swap files. replaceItemAt is the
            // failure-safe primitive: it renames the trimmed file into
            // place and only discards the original once the swap has
            // succeeded. The earlier remove-then-move sequence could
            // delete the original and then fail the move, leaving the
            // recording surviving only as a hidden temp dotfile — and
            // `try?` swallowed both errors.
            var swapError: Error?
            if !saveAsCopy && process.terminationStatus == 0 {
                do {
                    _ = try FileManager.default.replaceItemAt(
                        URL(fileURLWithPath: path),
                        withItemAt: URL(fileURLWithPath: outputPath)
                    )
                } catch {
                    swapError = error
                    // The original is untouched — just drop the orphaned temp.
                    try? FileManager.default.removeItem(atPath: outputPath)
                }
            }

            DispatchQueue.main.async {
                self.viewModel.trimBusy = false
                if let swapError = swapError {
                    self.viewModel.syncStatus = "Trim failed"
                    self.viewModel.syncStatusLevel = .error
                    self.log("Trim swap failed for \(path): \(swapError.localizedDescription)")
                    self.showError("Trim finished but the original couldn't be replaced (it is unchanged):\n\(swapError.localizedDescription)")
                } else if process.terminationStatus == 0 {
                    let name = URL(fileURLWithPath: path).lastPathComponent
                    self.log("Trimmed \(name) (\(start)s–\(end)s)")
                    self.viewModel.syncStatus = "Trimmed \(name)"
                    self.viewModel.syncStatusLevel = .success
                    // Overwrite-trim produces a shorter local file, but
                    // refreshSyncStatus would re-read the HiDock catalog
                    // and clobber our new size/duration with the
                    // device's still-original numbers — so the list
                    // would keep showing pre-trim length & size. Update
                    // the entry in place from the local file instead:
                    // size comes from the filesystem, duration from the
                    // requested (end - start). Save-as-copy doesn't
                    // touch the listed entry, so nothing to update there.
                    if !saveAsCopy {
                        self.updateEntryAfterOverwriteTrim(path: path, newDuration: end - start)
                    }
                } else {
                    self.viewModel.syncStatus = "Trim failed"
                    self.viewModel.syncStatusLevel = .error
                    // Surface the last few lines of ffmpeg stderr — the
                    // exit-code number alone is useless to the user.
                    let lastLines = stderrText
                        .split(separator: "\n")
                        .suffix(6)
                        .joined(separator: "\n")
                    self.log("Trim failed (exit \(process.terminationStatus)):\n\(stderrText)")
                    let detail = lastLines.isEmpty
                        ? "ffmpeg exited with status \(process.terminationStatus) (no stderr output)"
                        : "ffmpeg exited with status \(process.terminationStatus):\n\n\(lastLines)"
                    self.showError(detail)
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

    /// Layer 2 of the voice-training plan. Fires
    /// `transcribe.py recluster-with-anchors` against the diarized
    /// JSON; the Python side treats every user-named segment as an
    /// anchor centroid and reassigns every other segment to its
    /// closest anchor. Closes + reopens the viewer on success so the
    /// new assignments paint immediately.
    private func reclusterTranscriptWithLabels(jsonPath: String) {
        guard ensureTranscriptionReady() else { return }
        log("Re-clustering \(jsonPath) using user labels as anchors")
        viewModel.syncStatus = "Re-clustering from your labels…"
        viewModel.syncStatusLevel = .secondary
        syncViewModelState()

        let args = ["recluster-with-anchors", jsonPath]
        runTranscription(arguments: args) { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success:
                self.log("Re-cluster complete")
                self.viewModel.syncStatus = "Re-cluster complete — reopen transcript to see changes"
                self.viewModel.syncStatusLevel = .success
                self.transcriptViewerWindow?.close()
                self.transcriptViewerWindow = nil
                let mdPath = jsonPath.replacingOccurrences(of: "_diarized.json", with: ".md")
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                    self.openTranscriptViewer(transcriptMdPath: mdPath)
                }
            case .failure(let error):
                self.log("Re-cluster failed: \(error.localizedDescription)")
                self.viewModel.syncStatus = "Re-cluster failed"
                self.viewModel.syncStatusLevel = .error
            }
            self.syncViewModelState()
        }
    }

    /// Background voice-match scoring for the transcript viewer's verify panel.
    /// Runs the fast `speaker-confidence` verb (no audio/model load) and returns
    /// {speaker-id-string: confidence 0–1}. Best-effort — empty map on any error.
    private func scoreSpeakers(jsonPath: String, completion: @escaping ([String: SpeakerScore]) -> Void) {
        guard ensureTranscriptionReady() else { completion([:]); return }
        runTranscription(arguments: ["speaker-confidence", jsonPath]) { result in
            var map: [String: SpeakerScore] = [:]
            if case .success(let data) = result,
               let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let conf = obj["confidence"],
               let confData = try? JSONSerialization.data(withJSONObject: conf),
               let decoded = try? JSONDecoder().decode([String: SpeakerScore].self, from: confData) {
                map = decoded
            }
            DispatchQueue.main.async { completion(map) }
        }
    }

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

    /// Batch "close the loop": after enrolling new voices, sweep every
    /// transcribed-but-unconfirmed meeting and re-match its still-generic
    /// speakers against the current voice library. Runs the meetings
    /// sequentially — rematch re-embeds audio for legacy sidecars, which is
    /// CPU-heavy, so we deliberately don't fan them out against the
    /// transcription queue. New matches surface as the blue "confirm me" state.
    private func rematchUntaggedMeetings() {
        guard ensureTranscriptionReady() else { return }

        func diarizedPath(fromTranscript mdPath: String) -> String? {
            let url = URL(fileURLWithPath: mdPath)
            let base = url.deletingPathExtension().lastPathComponent
            let dir = url.deletingLastPathComponent()
            let p = dir.appendingPathComponent(base + "_diarized.json").path
            return FileManager.default.fileExists(atPath: p) ? p : nil
        }

        // Every transcribed meeting that isn't confirmed yet (needsTagging or
        // autoMatched) is a rematch candidate — including merged files.
        var paths: [String] = []
        for entry in syncEntries where entry.transcribed && !entry.speakersTagged {
            if let md = entry.transcriptPath, let jp = diarizedPath(fromTranscript: md) { paths.append(jp) }
        }
        for name in viewModel.mergedFileTranscribed.subtracting(viewModel.mergedFileTagged) {
            if let md = viewModel.mergedFileTranscriptPaths[name], let jp = diarizedPath(fromTranscript: md) { paths.append(jp) }
        }
        let jsonPaths = Array(Set(paths))

        guard !jsonPaths.isEmpty else {
            viewModel.syncStatus = "No unconfirmed meetings to re-match"
            viewModel.syncStatusLevel = .secondary
            syncViewModelState()
            return
        }

        log("Re-matching \(jsonPaths.count) unconfirmed meeting(s) against the voice library")
        var remaining = jsonPaths
        var totalRematched = 0

        func runNext() {
            guard let jp = remaining.first else {
                self.viewModel.syncStatus = totalRematched > 0
                    ? "Re-match complete — \(totalRematched) speaker(s) newly matched, confirm them"
                    : "Re-match complete — no new matches"
                self.viewModel.syncStatusLevel = .success
                self.syncViewModelState()
                self.refreshTranscriptionState()   // repaint the tag-column icons
                return
            }
            remaining.removeFirst()
            let done = jsonPaths.count - remaining.count
            self.viewModel.syncStatus = "Re-matching meeting \(done)/\(jsonPaths.count)…"
            self.viewModel.syncStatusLevel = .secondary
            self.syncViewModelState()

            self.runTranscription(arguments: ["rematch", jp]) { [weak self] result in
                guard let self = self else { return }
                if case .success(let data) = result,
                   let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                   let n = obj["rematched"] as? Int {
                    totalRematched += n
                }
                runNext()
            }
        }
        runNext()
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

        showDetailTab(id: "feedback", title: "My Feedback", icon: "bubble.left.and.text.bubble.right", view: FeedbackHistoryView(items: items))
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
            // Floor matches the full recordings-table column set (~1141pt) so
            // the user can't drag the window narrower than the content and
            // clip the left edge. Updated live by WindowMinSizeEnforcer when
            // the detail pane opens/closes (see MainWindowMetrics).
            win.minSize = MainWindowMetrics.minSize(detailPaneVisible: false)

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
    /// Guard for the launch-time auto-transcribe sweep. `refreshTranscriptionState`
    /// runs multiple times as the launch sequence unfolds (cache paint,
    /// then after each USB status probe returns), and we only want the
    /// backlog-catchup to fire once per session — otherwise it could
    /// trigger twice before the user even sees the UI.
    private var didRunLaunchAutoTranscribe = false

    /// Last-seen device-side file count per deviceId. Used to detect
    /// "a new recording appeared on the device since we last looked"
    /// so auto-download can fire even when the user records directly
    /// on the HiDock without going through the USB mic trigger (the
    /// trigger-release path was the only auto-download trigger before;
    /// recording directly meant the file just sat there).
    private var syncDeviceLastSeenCount: [String: Int] = [:]

    /// Catalog size for which the fresh-connect "catch-all" download-new sweep
    /// (auto-download trigger #3) was last run, per deviceId. The connection
    /// state flaps (a cached probe reports connected:false, clobbering the
    /// stored flag, so the next live probe looks "freshly connected"), which
    /// would otherwise re-fire the catch-all every probe. Skipping when the
    /// count is unchanged makes the catch-all idempotent per catalog — a
    /// genuine new recording (count change) re-enables it. Recorded when the
    /// sweep actually runs (not at schedule time) so a skipped timer can't
    /// permanently consume it.
    private var syncDeviceCatchAllSweptCount: [String: Int] = [:]

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
            },
            onReclusterWithLabels: { [weak self] jsonPath in
                self?.reclusterTranscriptWithLabels(jsonPath: jsonPath)
            },
            onRematch: { [weak self] jsonPath in
                self?.rematchTranscript(jsonPath: jsonPath)
            },
            onEnrollSpeakerFromDiarized: { [weak self] name, jsonPath, speakerId in
                self?.enrollSpeakerFromDiarized(name: name, diarizedPath: jsonPath, speakerId: speakerId)
            },
            onRenameVoiceLibrary: { [weak self] oldName, newName in
                self?.renameVoiceLibrarySpeaker(oldName: oldName, newName: newName)
            },
            onScoreSpeakers: { [weak self] jsonPath, completion in
                self?.scoreSpeakers(jsonPath: jsonPath, completion: completion)
            },
            onListVoiceNames: { [weak self] completion in
                self?.listVoiceLibraryNames(completion: completion)
            }
        )

        // Host as a tab in the right pane (one per transcript — re-opening the
        // same meeting focuses its existing tab).
        let title = (transcript.audioFile as NSString).lastPathComponent
        showDetailTab(id: "transcript:\(diarizedPath)", title: title, icon: "waveform", view: viewer)
    }

    /// Open (or focus) a view as a tab in the right-hand detail pane, and bring
    /// the main window forward. Replaces the old per-view NSWindow.
    private func showDetailTab(id: String, title: String, icon: String, view: some View) {
        viewModel.showDetailTab(HiDockViewModel.DetailTab(id: id, title: title, icon: icon, content: AnyView(view)))
        syncWindow?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    /// Open a generated summary .md in an in-app window (mirrors the
    /// transcript viewer) instead of launching the external editor.
    /// Clean template names (emoji prefix stripped) — matches the keys
    /// `typed_summarize.available_templates()` uses, so they can be passed to
    /// `summarize --template`.
    private func availableTemplateNames() -> [String] {
        let dir = templatesDir()
        guard let urls = try? FileManager.default.contentsOfDirectory(atPath: dir) else { return [] }
        return urls
            .filter { $0.lowercased().hasSuffix(".md") }
            .map { name -> String in
                let stem = (name as NSString).deletingPathExtension
                let cleaned = String(stem.drop(while: { !$0.isLetter && !$0.isNumber }))
                    .trimmingCharacters(in: .whitespaces)
                return cleaned.isEmpty ? stem : cleaned
            }
            .sorted()
    }

    private func openSummaryViewer(summaryMdPath: String) {
        guard FileManager.default.fileExists(atPath: summaryMdPath) else {
            showError("Summary file not found:\n\(summaryMdPath)")
            return
        }
        // Replace any previously-open summary window so reclassify reopens
        // cleanly on the new file.
        summaryViewerWindow?.close()
        let viewer = SummaryViewerView(
            summaryPath: summaryMdPath,
            templates: availableTemplateNames(),
            onReclassify: { [weak self] transcriptPath, template in
                self?.reclassifySummary(transcriptPath: transcriptPath, template: template)
            }
        )
        let title = (summaryMdPath as NSString).lastPathComponent
        showDetailTab(id: "summary:\(summaryMdPath)", title: title, icon: "doc.text", view: viewer)
    }

    /// Re-run the AI summary for a recording against a user-chosen template
    /// (from the viewer's Reclassify dropdown). Streams into the CLI pane,
    /// replaces the old summary file, and reopens the viewer on the new one.
    /// Routed through the same serial gate as the summarise queue — if a
    /// summarise (auto or manual) is in flight, the reclassify waits its
    /// turn instead of spawning a second concurrent CLI process.
    private func reclassifySummary(transcriptPath: String, template: String) {
        guard !transcriptPath.isEmpty else { return }
        guard !summariseBusy else {
            pendingReclassifies.append((transcriptPath, template))
            log("Reclassify queued behind running summarise: \((transcriptPath as NSString).lastPathComponent) -> \(template)")
            return
        }
        runReclassify(transcriptPath: transcriptPath, template: template)
    }

    /// Actually run a reclassify. Callers must hold the serial summarise
    /// gate (summariseBusy == false); this sets it for the duration and
    /// pumps processNextSummary on completion.
    private func runReclassify(transcriptPath: String, template: String) {
        summariseBusy = true
        viewModel.chatTitle = "Reclassifying as \(template)"
        if showCLIWhileSummarising {
            viewModel.cliPaneMode = .summary
            viewModel.cliPaneVisible = true
        }
        viewModel.summaryTranscript.reset()
        var args = ["summarize", transcriptPath, "--template", template, "--events"]
        if summarizeEngine != "auto" { args.append(contentsOf: ["--summarize-engine", summarizeEngine]) }
        runTranscription(arguments: args, timeout: 300, onLine: { [weak self] line in
            self?.viewModel.summaryTranscript.ingest(line: line)
        }) { [weak self] result in
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.summariseBusy = false
                self.viewModel.summaryTranscript.running = false
                if case .success(let data) = result,
                   let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                   (json["summarized"] as? Bool) == true,
                   let path = json["summary_path"] as? String {
                    self.refreshTranscriptionState()   // updates the Summary tick / entry path
                    self.openSummaryViewer(summaryMdPath: path)
                } else {
                    self.log("Reclassify failed for \(transcriptPath) -> \(template)")
                    if self.viewModel.summaryTranscript.errorMessage == nil {
                        self.viewModel.summaryTranscript.errorMessage = "Reclassify failed."
                    }
                    self.showError("Reclassify failed — see the CLI pane for details.")
                }
                self.processNextSummary()
            }
        }
    }

    // MARK: - Voice Library

    // MARK: - Voice Training

    private func showVoiceTraining() {
        let view = VoiceTrainingView(
            onEnroll: { [weak self] name, audioPath, start, end in
                self?.enrollSpeakerInVoiceLibrary(name: name, audioPath: audioPath, start: start, end: end)
            },
            onRefresh: { [weak self] completion in
                self?.loadVoiceTrainingData(completion: completion)
            }
        )
        showDetailTab(id: "voiceTraining", title: "Voice Training", icon: "waveform.badge.mic", view: view)
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
            } catch {
                DispatchQueue.main.async { completion([]) }
                return
            }

            // Drain stdout BEFORE waitUntilExit — the clusters JSON can
            // exceed the ~64 KB pipe buffer, and read-after-wait deadlocks
            // (child blocks on write, we block in waitUntilExit). Same
            // order as refreshMeetingExtraStats.
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            guard process.terminationStatus == 0,
                  let clusters = try? JSONDecoder().decode([VoiceClusterData].self, from: data) else {
                self.log("Voice training: failed to decode clusters (\(data.count) bytes)")
                DispatchQueue.main.async { completion([]) }
                return
            }

            DispatchQueue.main.async { completion(clusters) }
        }
    }

    @objc private func openVoiceLibraryMenu() {
        openVoiceLibrary()
    }

    @objc private func openVoiceTrainingMenu() {
        showVoiceTraining()
    }

    @objc private func buildVoiceLibraryMenu() {
        buildVoiceLibraryFromTranscripts()
    }

    @objc private func rematchUntaggedMenu() {
        rematchUntaggedMeetings()
    }

    /// Group the secondary tool windows into one macOS tabbed window
    /// ("tabs, not separate windows"). Windows sharing this identifier with
    /// tabbingMode .preferred are merged into a tab group by AppKit.
    private func applyPanelTabbing(_ win: NSWindow) {
        win.tabbingMode = .preferred
        win.tabbingIdentifier = NSWindow.TabbingIdentifier("com.hidock.tools.panels")
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
            configureVoiceLibraryProcess(process)
            process.arguments = [scriptPath, "list"]
            let pipe = Pipe()
            process.standardOutput = pipe
            process.standardError = Pipe()
            do {
                try process.run()
                // Read before waitUntilExit — read-after-wait deadlocks once
                // the child's output exceeds the pipe buffer (see
                // refreshMeetingExtraStats for the canonical ordering).
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
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
            },
            meetingCounts: viewModel.personMeetingCounts,
            onFilterToPerson: { [weak self] name in
                guard let self = self else { return }
                self.viewModel.syncFilterPeople = [name]
                self.viewModel.syncPeopleFilterMode = .any
                self.syncWindow?.makeKeyAndOrderFront(nil)
                NSApp.activate(ignoringOtherApps: true)
            }
        )

        showDetailTab(id: "voiceLibrary", title: "Voice Library", icon: "person.2.wave.2", view: libraryView)
    }

    /// List enrolled voice-library names (for the transcript viewer's
    /// map-to-existing-speaker autocomplete). Best-effort — empty on error.
    private func listVoiceLibraryNames(completion: @escaping ([String]) -> Void) {
        let scriptPath = voiceLibraryScriptPath()
        guard FileManager.default.fileExists(atPath: scriptPath) else { completion([]); return }
        DispatchQueue.global(qos: .userInitiated).async {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: self.voiceLibraryPythonPath())
            self.configureVoiceLibraryProcess(process)
            process.arguments = [scriptPath, "list"]
            let pipe = Pipe()
            process.standardOutput = pipe
            process.standardError = Pipe()
            var names: [String] = []
            do {
                try process.run()
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                if let parsed = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] {
                    names = parsed.compactMap { $0["name"] as? String }
                }
            } catch {
                self.log("listVoiceLibraryNames failed: \(error.localizedDescription)")
            }
            DispatchQueue.main.async { completion(names.sorted { $0.localizedCaseInsensitiveCompare($1) == .orderedAscending }) }
        }
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

    /// Configure a `voice_library_lite.py` subprocess so its top-level
    /// `from shared...` imports resolve: put the repo root on PYTHONPATH and use
    /// it as cwd (mirrors loadVoiceTrainingData). Without this the process exits
    /// non-zero with empty output and the UI shows "No voices enrolled".
    private func configureVoiceLibraryProcess(_ process: Process) {
        let root = bundledResourcesRoot ?? repoRoot
        process.currentDirectoryURL = URL(fileURLWithPath: root)
        var env = ProcessInfo.processInfo.environment
        env["HOME"] = NSHomeDirectory()
        env["PYTHONPATH"] = root
        process.environment = env
    }

    private func enrollSpeakerInVoiceLibrary(name: String, audioPath: String, start: Double, end: Double) {
        let scriptPath = voiceLibraryScriptPath()
        guard FileManager.default.fileExists(atPath: scriptPath) else { return }

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let process = Process()
            process.executableURL = URL(fileURLWithPath: self?.voiceLibraryPythonPath() ?? "/usr/bin/python3")
            if let self = self { self.configureVoiceLibraryProcess(process) }
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

    /// Enrol a speaker from the diarizer's stored centroid (name + sidecar path +
    /// speaker id) — a far more robust voiceprint than a single audio segment,
    /// and no audio decode (works for Opus/Plaud too).
    private func enrollSpeakerFromDiarized(name: String, diarizedPath: String, speakerId: Int) {
        let scriptPath = voiceLibraryScriptPath()
        guard FileManager.default.fileExists(atPath: scriptPath) else { return }
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let process = Process()
            process.executableURL = URL(fileURLWithPath: self?.voiceLibraryPythonPath() ?? "/usr/bin/python3")
            if let self = self { self.configureVoiceLibraryProcess(process) }
            process.arguments = [
                scriptPath, "enroll-diarized",
                "--name", name, "--json", diarizedPath, "--id", "\(speakerId)",
            ]
            let output = Pipe()
            let errors = Pipe()
            process.standardOutput = output
            process.standardError = errors
            do {
                try process.run()
                // Drain the pipes before waiting: a full pipe can otherwise
                // leave the child blocked while waitUntilExit() is waiting.
                _ = output.fileHandleForReading.readDataToEndOfFile()
                let stderrData = errors.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                let stderr = String(
                    data: stderrData,
                    encoding: .utf8
                ) ?? ""
                if process.terminationStatus == 0 {
                    DispatchQueue.main.async { self?.log("Enrolled '\(name)' in voice library") }
                } else {
                    DispatchQueue.main.async {
                        self?.log("Failed to enroll '\(name)' from diarized: \(stderr.trimmingCharacters(in: .whitespacesAndNewlines))")
                    }
                }
            } catch {
                DispatchQueue.main.async { self?.log("Failed to enroll '\(name)' from diarized: \(error)") }
            }
        }
    }

    /// Build the voice library from the tagged backlog — enrol every trustworthy
    /// named speaker across all transcripts (excludes unverified auto-matches).
    private func buildVoiceLibraryFromTranscripts() {
        let scriptPath = voiceLibraryScriptPath()
        guard FileManager.default.fileExists(atPath: scriptPath) else { return }
        let dir = syncTranscriptFolder ?? "\(NSHomeDirectory())/HiDock/Raw Transcripts"
        viewModel.syncStatus = "Building voice library from tagged meetings…"
        viewModel.syncStatusLevel = .secondary
        syncViewModelState()
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let process = Process()
            process.executableURL = URL(fileURLWithPath: self?.voiceLibraryPythonPath() ?? "/usr/bin/python3")
            if let self = self { self.configureVoiceLibraryProcess(process) }
            process.arguments = [scriptPath, "enroll-from-transcripts", "--dir", dir]
            let out = Pipe(); process.standardOutput = out
            let err = Pipe(); process.standardError = err
            // Stream PROGRESS:i/total from stderr → live percentage.
            err.fileHandleForReading.readabilityHandler = { [weak self] h in
                guard let line = String(data: h.availableData, encoding: .utf8) else { return }
                for token in line.split(whereSeparator: { $0 == "\n" || $0 == "\r" }) {
                    guard token.hasPrefix("PROGRESS:") else { continue }
                    let parts = token.dropFirst("PROGRESS:".count).split(separator: "/")
                    guard parts.count == 2, let i = Int(parts[0]), let t = Int(parts[1]), t > 0 else { continue }
                    let pct = Int(Double(i) / Double(t) * 100)
                    DispatchQueue.main.async {
                        self?.viewModel.syncStatus = "Building voice library… \(pct)% (\(i)/\(t) meetings)"
                        self?.viewModel.syncStatusLevel = .secondary
                        self?.syncViewModelState()
                    }
                }
            }
            var enrolled = 0
            do {
                try process.run()
                let data = out.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
                if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                   let n = obj["enrolled"] as? Int { enrolled = n }
            } catch {
                self?.log("Build voice library failed: \(error)")
            }
            err.fileHandleForReading.readabilityHandler = nil
            DispatchQueue.main.async {
                self?.viewModel.syncStatus = "Voice library updated — \(enrolled) voice sample(s) added"
                self?.viewModel.syncStatusLevel = .success
                self?.syncViewModelState()
            }
        }
    }

    /// Re-match a single transcript's still-generic speakers against the library.
    private func rematchTranscript(jsonPath: String) {
        guard ensureTranscriptionReady() else { return }
        viewModel.syncStatus = "Re-matching speakers…"
        viewModel.syncStatusLevel = .secondary
        syncViewModelState()
        runTranscription(arguments: ["rematch", jsonPath]) { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success:
                self.viewModel.syncStatus = "Re-match complete — reopen to see changes"
                self.viewModel.syncStatusLevel = .success
                self.transcriptViewerWindow?.close()
                self.transcriptViewerWindow = nil
                let mdPath = jsonPath.replacingOccurrences(of: "_diarized.json", with: ".md")
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {
                    self.openTranscriptViewer(transcriptMdPath: mdPath)
                }
                self.refreshTranscriptionState()
            case .failure(let error):
                self.viewModel.syncStatus = "Re-match failed"
                self.viewModel.syncStatusLevel = .error
                self.log("Re-match failed: \(error.localizedDescription)")
            }
            self.syncViewModelState()
        }
    }

    private func deleteVoiceLibrarySpeaker(name: String) {
        let scriptPath = voiceLibraryScriptPath()
        guard FileManager.default.fileExists(atPath: scriptPath) else { return }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: voiceLibraryPythonPath())
        configureVoiceLibraryProcess(process)
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
        configureVoiceLibraryProcess(process)
        process.arguments = [scriptPath, "rename", "--old", oldName, "--new", newName]
        let output = Pipe()
        let errors = Pipe()
        process.standardOutput = output
        process.standardError = errors
        do {
            try process.run()
            // Drain the pipes before waiting: a full pipe can otherwise leave
            // the child blocked while waitUntilExit() is waiting.
            _ = output.fileHandleForReading.readDataToEndOfFile()
            let stderrData = errors.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            let stderr = String(
                data: stderrData,
                encoding: .utf8
            ) ?? ""
            if process.terminationStatus == 0 {
                log("Renamed speaker '\(oldName)' to '\(newName)' in voice library")
            } else {
                log("Voice library rename skipped/failed for '\(oldName)' → '\(newName)': \(stderr.trimmingCharacters(in: .whitespacesAndNewlines))")
            }
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
        applyPanelTabbing(win)
        win.isReleasedWhenClosed = false
        win.minSize = NSSize(width: 600, height: 320)
        win.contentView = NSHostingView(rootView: view)

        terminalWindow = win
        win.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    /// One-shot typed summary of an already-transcribed recording via Claude
    /// Code. Marks the row "Summarising", runs `transcribe_cpp.py summarize`,
    /// then flips it to "Summarised" when the summary file is produced.
    /// Row-action entry point — queue a single recording for a typed summary.
    private func summariseRecording(_ entry: HiDockSyncRecordingEntry) {
        guard let transcript = entry.transcriptPath, !transcript.isEmpty else {
            showError("No transcript found for \(entry.recording.outputName). Transcribe it first.")
            return
        }
        enqueueSummaries([entry])
    }

    /// "Summarise Selected" toolbar button — summarise every ticked
    /// transcribed row. If all selected rows already have a summary, offer
    /// to re-summarise (mirrors the Transcribe Selected re-run prompt).
    private func summariseSelectedRecordings() {
        let selected = selectedSyncEntries().filter {
            $0.transcribed && ($0.transcriptPath.map { !$0.isEmpty } ?? false)
        }
        guard !selected.isEmpty else {
            viewModel.syncStatus = "No transcribed recordings selected to summarise"
            viewModel.syncStatusLevel = .warning
            syncViewModelState()
            return
        }
        let pending = selected.filter { $0.summaryPath == nil }
        let toRun: [HiDockSyncRecordingEntry]
        if pending.isEmpty {
            let alert = NSAlert()
            alert.messageText = "Already Summarised"
            alert.informativeText = "\(selected.count) selected recording\(selected.count == 1 ? " is" : "s are") already summarised.\n\nRe-summarise?"
            alert.addButton(withTitle: "Re-summarise")
            alert.addButton(withTitle: "Cancel")
            alert.alertStyle = .informational
            guard alert.runModal() == .alertFirstButtonReturn else { return }
            toRun = selected
        } else {
            toRun = pending
        }
        log("Summarise selected: \(toRun.count) recording(s)")
        enqueueSummaries(toRun)
    }

    /// Add transcribed entries to the serial summarise queue (deduped
    /// against the queue and anything already in flight), then kick the
    /// pump. Used by the row action, the toolbar button, and auto-summarise.
    private func enqueueSummaries(_ entries: [HiDockSyncRecordingEntry]) {
        var added = 0
        for entry in entries {
            let name = entry.recording.outputName
            guard let t = entry.transcriptPath, !t.isEmpty else { continue }
            guard !viewModel.summarisingNames.contains(name) else { continue }
            guard !summariseQueue.contains(where: { $0.recording.outputName == name }) else { continue }
            summariseQueue.append(entry)
            added += 1
        }
        if added > 0 { log("Summarise: queued \(added) recording(s) (\(summariseQueue.count) pending)") }
        processNextSummary()
    }

    private func processNextSummary() {
        guard !summariseBusy else { return }
        // Reclassify requests queued while a summarise was running take
        // the gate first — they're user-initiated and there's at most a
        // handful of them.
        if !pendingReclassifies.isEmpty {
            let next = pendingReclassifies.removeFirst()
            runReclassify(transcriptPath: next.transcriptPath, template: next.template)
            return
        }
        guard !summariseQueue.isEmpty else { return }
        let entry = summariseQueue.removeFirst()
        let name = entry.recording.outputName
        guard let transcript = entry.transcriptPath, !transcript.isEmpty else {
            processNextSummary(); return
        }
        summariseBusy = true
        viewModel.summarisingNames.insert(name)
        // Surface the activity in the CLI pane as a live formatted readout: the
        // pipeline streams normalized events (--events), which we ingest into
        // the summary transcript. Both auto- and manual summarise flow here.
        if showCLIWhileSummarising {
            viewModel.cliPaneMode = .summary
            viewModel.cliPaneVisible = true
        }
        viewModel.summaryTranscript.reset()
        viewModel.ledMatrix.notify(LEDEvent(kind: .summarise, text: "SUMMARISING \(name)"))
        syncViewModelState()

        var args = ["summarize", transcript, "--events"]
        if summarizeEngine != "auto" { args.append(contentsOf: ["--summarize-engine", summarizeEngine]) }
        // Feed the subprocess's stderr event stream into the formatted readout.
        runTranscription(arguments: args, timeout: 300, onLine: { [weak self] line in
            self?.viewModel.summaryTranscript.ingest(line: line)
        }) { [weak self] result in
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.viewModel.summarisingNames.remove(name)
                self.summariseBusy = false
                self.viewModel.summaryTranscript.running = false
                if case .success(let data) = result,
                   let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                   (json["summarized"] as? Bool) == true,
                   let path = json["summary_path"] as? String {
                    if let i = self.syncEntries.firstIndex(where: { $0.recording.outputName == name }) {
                        self.syncEntries[i].summaryPath = path
                    }
                    self.log("Summarised \(name) -> \(path)")
                    self.viewModel.ledMatrix.notify(LEDEvent(kind: .summarise, text: "\(LEDFont.check) SUMMARY \(name)"))
                } else {
                    self.log("Summarise: no summary produced for \(name)")
                    if self.viewModel.summaryTranscript.errorMessage == nil {
                        self.viewModel.summaryTranscript.errorMessage = "No summary produced for \(name)."
                    }
                    self.viewModel.ledMatrix.notify(LEDEvent(kind: .error, text: "\(LEDFont.cross) SUMMARISE FAILED"))
                }
                self.syncViewModelState()
                self.processNextSummary()
            }
        }
    }

    /// Run the selected AI CLI on this recording's transcript inside the
    /// embedded CLI pane — interactive, uses the user's CLI login (no keys).
    /// Opens the pane (so the user sees it) and types the command into the
    /// pane's shell.
    private func askClaudeAboutRecording(_ entry: HiDockSyncRecordingEntry) {
        guard let transcript = entry.transcriptPath, !transcript.isEmpty else {
            showError("No transcript found for \(entry.recording.outputName). Transcribe it first.")
            return
        }
        let dir = (transcript as NSString).deletingLastPathComponent
        let file = (transcript as NSString).lastPathComponent
        // Start a fresh formatted Ask-AI conversation. Read-only tools so the
        // engine can read the transcript and answer without write access.
        chatWorkingDir = dir
        chatSessionId = nil
        viewModel.chatTitle = "Ask AI — \(entry.recording.outputName)"
        viewModel.chatTranscript.reset()
        viewModel.chatTranscript.running = false   // set true by runChatTurn
        viewModel.cliPaneMode = .chat
        viewModel.cliPaneVisible = true
        runChatTurn("Read the transcript '\(file)' and help me summarise it and answer questions about it.")
    }

    /// Working directory + session for the active Ask-AI conversation. The
    /// session id (from claude's `meta` event) lets follow-ups resume context.
    private var chatWorkingDir: String?
    private var chatSessionId: String?

    /// Run one Ask-AI turn via the pipeline's `ask` subcommand, streaming
    /// normalized events into the chat transcript. Multi-turn resumes the
    /// prior claude session.
    private func runChatTurn(_ prompt: String) {
        guard !viewModel.chatRunning else { return }
        viewModel.chatRunning = true
        viewModel.chatTranscript.addUserMessage(prompt)
        viewModel.chatTranscript.running = true

        var args = ["ask", "--allowed-tools", "Read,Grep,Glob"]
        if let dir = chatWorkingDir { args += ["--cwd", dir] }
        if let sid = chatSessionId { args += ["--resume", sid] }
        if summarizeEngine != "auto" { args += ["--engine", summarizeEngine] }
        runTranscription(arguments: args, timeout: 600, stdin: prompt, onLine: { [weak self] line in
            self?.viewModel.chatTranscript.ingest(line: line)
        }) { [weak self] result in
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.viewModel.chatRunning = false
                self.viewModel.chatTranscript.running = false
                if case .success(let data) = result,
                   let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                   let sid = json["session_id"] as? String, !sid.isEmpty {
                    self.chatSessionId = sid
                }
            }
        }
    }

    /// Show the raw SwiftTerm shell pane — kept for `claude auth login` and
    /// power use now that the AI flows use the formatted views.
    private func openRawTerminalPane() {
        viewModel.cliPaneMode = .terminal
        viewModel.cliPaneVisible = true
        viewModel.terminalController.ensureStarted()
    }

    /// Consecutive days (ending today or yesterday) that have ≥1 meeting — for
    /// the LED idle ticker's "N DAY STREAK".
    private func meetingStreak() -> Int {
        let cal = Calendar.current
        let activity = viewModel.meetingActivityByDay
        var day = cal.startOfDay(for: Date())
        // Allow the streak to count even if today has no meeting yet.
        if (activity[day]?.count ?? 0) == 0 {
            day = cal.date(byAdding: .day, value: -1, to: day) ?? day
        }
        var streak = 0
        while (activity[day]?.count ?? 0) > 0 {
            streak += 1
            guard let prev = cal.date(byAdding: .day, value: -1, to: day) else { break }
            day = prev
        }
        return streak
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
        let sources = panel.urls

        // Copy + probe on a background queue — large or network-mounted
        // files can take seconds and would beachball the UI if done on
        // the main thread. State mutation + UI updates hop back to main.
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }
            try? FileManager.default.createDirectory(
                at: recordingsURL, withIntermediateDirectories: true
            )
            var entries: [ImportedRecordingEntry] = []
            for source in sources {
                if let entry = self.importSingleFile(source, into: recordingsURL) {
                    entries.append(entry)
                }
            }
            guard !entries.isEmpty else { return }
            DispatchQueue.main.async {
                self.importedRecordings.append(contentsOf: entries)
                ImportedRecordingsStore.save(self.importedRecordings)
                self.rebuildSyncEntries()
                let added = entries.count
                self.viewModel.syncStatus = "Imported \(added) file\(added == 1 ? "" : "s")"
                self.viewModel.syncStatusLevel = .success
                self.syncViewModelState()
                self.refreshTranscriptionState()
            }
        }
    }

    /// Copy a single source file into the recordings folder, gather its
    /// basic metadata, and return a persistable ImportedRecordingEntry.
    /// Runs on a background queue (see importAudioFile) — the copy and
    /// duration probe can take seconds for large/network files.
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
        // Carry transcription/tagging state forward across the rebuild (by name)
        // so a reconnect/refresh doesn't blank an imported row's icon.
        let previousByName = Dictionary(
            syncEntries.filter { $0.deviceId == IMPORTED_DEVICE_ID }
                .map { ($0.recording.name, $0) },
            uniquingKeysWith: { a, _ in a }
        )
        syncEntries.removeAll { $0.deviceId == IMPORTED_DEVICE_ID }
        let stableImportedPid = Int(truncatingIfNeeded: IMPORTED_DEVICE_ID.hashValue)
        for entry in importedRecordings {
            let rec = ImportedRecordingsStore.asSyncRecording(entry)
            let prev = previousByName[rec.name]
            let sync = HiDockSyncRecordingEntry(
                recording: rec,
                deviceProductId: stableImportedPid,
                deviceId: IMPORTED_DEVICE_ID,
                deviceName: IMPORTED_DEVICE_NAME,
                transcribed: prev?.transcribed ?? false,
                transcriptPath: prev?.transcriptPath,
                transcribedDate: prev?.transcribedDate,
                speakersTagged: prev?.speakersTagged ?? false,
                speakersAutoMatched: prev?.speakersAutoMatched ?? false,
                summaryPath: prev?.summaryPath,
                transcriptionSkipped: prev?.transcriptionSkipped ?? false
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
        guard entry.recording.localExists else {
            log("transcribeWithSpeakerCount: \(name) not on disk yet")
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
            let environment: [String: String]
            if let device = device, device.deviceType == .volume {
                args = ["unmark-downloaded", "--volume-name", device.volumeName ?? "", entry.recording.name]
                pid = nil
                environment = [:]
            } else if let device = device, device.deviceType == .plaud {
                args = ["unmark-downloaded", "--plaud-account", device.plaudAccountId ?? "", entry.recording.name]
                pid = nil
                environment = plaudEnvironment(for: device)
            } else {
                args = ["unmark-downloaded", entry.recording.name]
                pid = device?.productId
                environment = [:]
            }
            runExtractor(arguments: args, productId: pid, environment: environment) { [weak self] _ in
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
        let environment: [String: String]
        if device.deviceType == .volume {
            args = ["volume-status", "--volume-name", device.volumeName ?? "", "--timeout-ms", "5000"]
            pid = nil
            environment = [:]
        } else if device.deviceType == .plaud {
            args = plaudExtractorArguments("plaud-status", device: device)
            pid = nil
            environment = plaudEnvironment(for: device)
        } else {
            args = ["status", "--timeout-ms", "5000"]
            pid = device.productId
            environment = [:]
        }

        runExtractor(arguments: args, productId: pid, environment: environment) { [weak self] result in
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

        // Imports: drop file + transcript artifacts + JSON entry.
        // Without this the .md / .srt / *_diarized.json / *_whisper.json
        // and the summary file would orphan in their respective folders
        // every time the user removed an imported recording.
        for entry in imports {
            let name = entry.recording.name
            if let im = importedRecordings.first(where: { $0.name == name }) {
                try? FileManager.default.removeItem(atPath: im.outputPath)
            }
            removeTranscriptArtifacts(forRecordingName: entry.recording.outputName)
            importedRecordings.removeAll { $0.name == name }
            syncCheckedRecordings.remove(name)
        }
        if !imports.isEmpty {
            ImportedRecordingsStore.save(importedRecordings)
        }

        // HiDock local copies: delete MP3 + transcript artifacts. Mark
        // the entry as `removed` in state.json so download-new skips
        // it on the next auto-cycle (status pill flips to "Removed",
        // same UX as Skipped). Volumes use `unmark-downloaded` (the
        // older flow) because they don't go through the same removed
        // semantics — re-importing them is the natural way back.
        let group = DispatchGroup()
        for entry in localCopies {
            try? FileManager.default.removeItem(atPath: entry.recording.outputPath)
            removeTranscriptArtifacts(forRecordingName: entry.recording.outputName)
            let device = syncPairedDevices.first { $0.deviceId == entry.deviceId }
            var args: [String]
            let pid: Int?
            let environment: [String: String]
            if let device = device, device.deviceType == .volume {
                args = ["unmark-downloaded", "--volume-name", device.volumeName ?? "", entry.recording.name]
                pid = nil
                environment = [:]
            } else if let device = device, device.deviceType == .plaud {
                args = ["mark-removed", "--plaud-account", device.plaudAccountId ?? "", entry.recording.name]
                pid = nil
                environment = plaudEnvironment(for: device)
            } else {
                args = ["mark-removed", entry.recording.name]
                pid = device?.productId
                environment = [:]
            }
            group.enter()
            runExtractor(arguments: args, productId: pid, environment: environment) { _ in group.leave() }
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

    /// Delete every transcript-pipeline artifact that belongs to a
    /// recording (by mp3 base name): the markdown transcript, the SRT,
    /// the diarized JSON, the raw whisper JSON, and any summary in
    /// ~/HiDock/Summaries/. Called from Remove and from any future
    /// "wipe and re-transcribe" flow. Best-effort — `try?` swallows
    /// the missing-file case which is the common one.
    private func removeTranscriptArtifacts(forRecordingName outputName: String) {
        let stem = (outputName as NSString).deletingPathExtension
        let transcriptDir = syncTranscriptFolder ?? "\(NSHomeDirectory())/HiDock/Raw Transcripts"
        let summariesDir = (NSHomeDirectory() as NSString).appendingPathComponent("HiDock/Summaries")

        let transcriptArtifacts = [
            "\(transcriptDir)/\(stem).md",
            "\(transcriptDir)/\(stem).srt",
            "\(transcriptDir)/\(stem)_diarized.json",
            "\(transcriptDir)/\(stem)_whisper.json",
        ]
        for path in transcriptArtifacts {
            try? FileManager.default.removeItem(atPath: path)
        }

        // Summaries are matched by the "<stem> - <Type> - …" prefix — same
        // lookup findSummaryPath uses. A `contains(stem)` sweep was
        // dangerous: a merged file's summary name ("Merged-<childStem>-to-…")
        // contains its children's stems, so removing a child recording
        // deleted the merged recording's summary too.
        if let entries = try? FileManager.default.contentsOfDirectory(atPath: summariesDir) {
            for entry in entries where entry.hasPrefix(stem + " - ") {
                try? FileManager.default.removeItem(atPath: "\(summariesDir)/\(entry)")
            }
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
        showDetailTab(id: "deviceManager", title: "Device Manager", icon: "externaldrive.connected.to.line.below", view: DeviceManagerView(viewModel: viewModel))
    }

    private func forgetDevice(_ device: HiDockPairedDevice) {
        var devices = syncPairedDevices
        devices.removeAll { $0.deviceId == device.deviceId }
        syncPairedDevices = devices
        if device.deviceType == .plaud, let accountId = device.plaudAccountId {
            PlaudAuthStore.delete(accountId: accountId)
        }
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

    /// Sign out of a Plaud account but keep it linked as a device. Clears the
    /// Keychain session (so sync pauses and the next sign-in requires a code)
    /// while leaving the paired device in place — distinct from `forgetDevice`,
    /// which removes the account entirely. Re-signing in reuses `pairPlaud`,
    /// which updates the existing entry in place (it keys on accountId).
    private func signOutPlaud(_ device: HiDockPairedDevice) {
        guard device.deviceType == .plaud, let accountId = device.plaudAccountId else { return }
        PlaudAuthStore.delete(accountId: accountId)
        markPlaudSignedOut(device)
        log("Signed out of Plaud account: \(device.cleanName) (still linked)")
        refreshSyncStatus()
    }

    private func pairPlaud(region: String) {
        log("Starting Plaud sign-in (region=\(region))")
        let controller = PlaudLoginWindowController(region: region, log: { [weak self] message in
            self?.log(message)
        }) { [weak self] result in
            guard let self = self else { return }
            self.plaudLoginController = nil
            switch result {
            case .success(let session):
                do {
                    try PlaudAuthStore.save(session)
                } catch {
                    self.showError("Plaud signed in but the session could not be saved:\n\(error.localizedDescription)")
                    return
                }
                let device = HiDockPairedDevice(
                    plaudAccountId: session.accountId,
                    displayName: session.displayName,
                    email: session.email,
                    region: session.region
                )
                var devices = self.syncPairedDevices
                if let idx = devices.firstIndex(where: { $0.deviceId == device.deviceId }) {
                    devices[idx] = device
                } else {
                    devices.append(device)
                }
                self.syncPairedDevices = devices
                self.syncDeviceConnected.removeValue(forKey: device.deviceId)
                self.syncDeviceLastError.removeValue(forKey: device.deviceId)
                self.viewModel.syncPairedDevices = self.syncPairedDevices
                self.viewModel.syncDeviceConnected = self.syncDeviceConnected
                self.viewModel.syncDeviceLastError = self.syncDeviceLastError
                self.viewModel.syncPaired = true
                self.log("Paired Plaud account: \(session.displayName) (\(session.region))")
                self.refreshSyncStatus()
            case .failure(let error):
                self.log("Plaud sign-in failed: \(error.localizedDescription)")
                self.showError("Plaud sign-in failed:\n\(error.localizedDescription)")
            }
        }
        plaudLoginController = controller
        controller.show()
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
        // Single-instance: focus the existing window instead of spawning a duplicate.
        refreshModelStatuses()
        showDetailTab(id: "models", title: "Models", icon: "shippingbox", view: ModelManagerView(viewModel: viewModel))
    }

    // MARK: - Summary Templates Manager

    private func templatesDir() -> String { "\(NSHomeDirectory())/HiDock/Summary Templates" }

    /// Drop a PreToolUse hook into the templates folder's `.claude/settings.json`
    /// so any AI CLI session launched there is *deterministically* forced to ask
    /// for the user's approval before Write/Edit/MultiEdit — regardless of the
    /// user's permission mode (accept-edits / auto would otherwise skip the
    /// built-in prompt). A prompt instruction alone can't guarantee this; the
    /// hook can. Idempotent: only writes if our hook isn't already present.
    private func ensureTemplatesApprovalHook() {
        let claudeDir = "\(templatesDir())/.claude"
        let settingsPath = "\(claudeDir)/settings.json"
        let reason = "HiDock: approve this template change before it is saved"
        if let existing = try? String(contentsOfFile: settingsPath, encoding: .utf8),
           existing.contains("permissionDecision"), existing.contains(reason) {
            return  // our hook is already installed
        }
        // The hook command prints an "ask" decision for the matched edit tools.
        let hookCommand = "echo '{\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"permissionDecision\":\"ask\",\"permissionDecisionReason\":\"\(reason)\"}}'"
        let settings: [String: Any] = [
            "hooks": [
                "PreToolUse": [
                    [
                        "matcher": "Write|Edit|MultiEdit",
                        "hooks": [
                            ["type": "command", "command": hookCommand]
                        ]
                    ]
                ]
            ]
        ]
        do {
            try FileManager.default.createDirectory(atPath: claudeDir, withIntermediateDirectories: true)
            let data = try JSONSerialization.data(withJSONObject: settings, options: [.prettyPrinted])
            try data.write(to: URL(fileURLWithPath: settingsPath))
            log("Installed templates approval hook at \(settingsPath)")
        } catch {
            log("Failed to install templates approval hook: \(error.localizedDescription)")
        }
    }

    private func openTemplatesManager() {
        showDetailTab(id: "templates", title: "Summary Templates", icon: "doc.badge.gearshape", view: TemplatesManagerView(viewModel: viewModel))
    }

    /// Open Claude Code in the embedded CLI pane, cd'd into the templates
    /// folder, to refine an existing template. Brings the main window forward
    /// so the pane is visible. No API keys — uses the user's Claude Code login.
    private func iterateTemplate(_ url: URL) {
        let dir = templatesDir()
        let file = url.lastPathComponent
        ensureTemplatesApprovalHook()
        showSyncWindow()
        // Template authoring stays on the raw interactive terminal: it writes
        // files with human-in-the-loop approval, which needs claude's
        // interactive permission prompts (the formatted chat is read-only).
        viewModel.cliPaneMode = .terminal
        viewModel.cliPaneVisible = true
        // Human-in-the-loop: the AI must PROPOSE and wait for approval before
        // writing — template files are user-curated, so we never let it edit
        // silently. (Claude Code's own edit-permission prompt is a second gate.)
        let cmd = "cd \"\(dir)\" && \(aiCliBinary) \"Read the summary template '\(file)' and propose improvements to its section structure and 'Extraction guidance' notes. Show me the proposed changes as a clear diff or summary and ask for my approval. Do NOT modify the file until I explicitly approve — then save only the changes I approved.\""
        viewModel.terminalController.runCommand(cmd)
    }

    /// Open Claude Code in the CLI pane to author a brand-new template in the
    /// templates folder.
    private func createTemplateWithClaude() {
        let dir = templatesDir()
        ensureTemplatesApprovalHook()
        showSyncWindow()
        // Template authoring stays on the raw interactive terminal (see
        // iterateTemplate): writing files needs claude's interactive approval.
        viewModel.cliPaneMode = .terminal
        viewModel.cliPaneVisible = true
        // Human-in-the-loop: propose the full file and wait for approval
        // before writing anything.
        let cmd = "cd \"\(dir)\" && \(aiCliBinary) \"Help me create a new summary template as a markdown file in this folder. Ask what kind of recording it's for, then show me the full proposed template content (section headings + 'Extraction guidance' notes matching the style of the existing .md templates here) and ask for my approval. Do NOT write any file until I explicitly approve, then save it.\""
        viewModel.terminalController.runCommand(cmd)
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
                // Read before waitUntilExit — read-after-wait deadlocks once
                // the status JSON exceeds the pipe buffer (see
                // refreshMeetingExtraStats for the canonical ordering).
                let data = pipe.fileHandleForReading.readDataToEndOfFile()
                process.waitUntilExit()
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
                                installed: info["installed"] as? Bool ?? false,
                                stage: info["stage"] as? String ?? "other",
                                stageLabel: info["stage_label"] as? String ?? "",
                                category: info["category"] as? String ?? "pipeline",
                                usedBy: info["used_by"] as? String ?? "",
                                dependsOn: info["depends_on"] as? String ?? "",
                                backendKey: info["backend_key"] as? String ?? key,
                                active: info["active"] as? Bool ?? false,
                                experimental: info["experimental"] as? Bool ?? false,
                                builtIn: info["built_in"] as? Bool ?? false,
                                nemoModel: info["nemo_model"] as? Bool ?? false
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

    /// Persist a new active backend selection. Calls
    /// `shared/models.py set-active <key>`, which updates
    /// pipeline_backends.json — the next refreshModelStatuses()
    /// picks it up and the UI flips the ACTIVE badge.
    private func setActiveModelByKey(_ key: String) {
        let scriptPath = modelsScriptPath()
        let pythonPath = modelsPythonPath()
        guard FileManager.default.fileExists(atPath: scriptPath) else { return }
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let process = Process()
            process.executableURL = URL(fileURLWithPath: pythonPath)
            process.arguments = [scriptPath, "set-active", key]
            process.standardOutput = Pipe()
            process.standardError = Pipe()
            do {
                try process.run()
                process.waitUntilExit()
                DispatchQueue.main.async {
                    self?.log("Set active model: \(key)")
                    self?.refreshModelStatuses()
                }
            } catch {
                self?.log("Failed to set active model \(key): \(error)")
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

    private func plaudExtractorArguments(_ command: String, device: HiDockPairedDevice, extra: [String] = []) -> [String] {
        [command, "--account-id", device.plaudAccountId ?? device.deviceId] + extra
    }

    /// If a Plaud extractor command refreshed the user token, persist the
    /// rotated tokens to the Keychain so the next sync uses a fresh token.
    /// Keyed off PLAUD_ACCOUNT_ID in the command's environment, so it runs
    /// for every Plaud command (status/download/download-new) and is a no-op
    /// for HiDock/volume commands. Must run on the main thread.
    private func persistRefreshedPlaudTokens(from outData: Data, environment: [String: String]) {
        guard let accountId = environment["PLAUD_ACCOUNT_ID"], !accountId.isEmpty,
              let existing = PlaudAuthStore.load(accountId: accountId),
              let updated = PlaudSession.applyingRefreshedTokens(outData, to: existing) else { return }
        do {
            try PlaudAuthStore.save(updated)
            log("Plaud: refreshed and persisted user token for \(updated.displayName)")
        } catch {
            log("Plaud: failed to persist refreshed token: \(error.localizedDescription)")
        }
    }

    private func plaudEnvironment(for device: HiDockPairedDevice) -> [String: String] {
        guard device.deviceType == .plaud else { return [:] }
        guard let accountId = device.plaudAccountId,
              let session = PlaudAuthStore.load(accountId: accountId) else {
            markPlaudSignedOut(device)
            return [:]
        }
        if syncDeviceLastError[device.deviceId]?.0 == plaudSignedOutMessage {
            syncDeviceLastError.removeValue(forKey: device.deviceId)
        }
        return [
            "PLAUD_ACCOUNT_ID": accountId,
            "PLAUD_ACCESS_TOKEN": session.accessToken,
            "PLAUD_REFRESH_TOKEN": session.refreshToken ?? "",
            "PLAUD_REGION": session.region
        ]
    }

    private func markPlaudSignedOut(_ device: HiDockPairedDevice) {
        guard device.deviceType == .plaud else { return }
        syncDeviceConnected[device.deviceId] = false
        syncDeviceLastError[device.deviceId] = (plaudSignedOutMessage, Date())
        log("\(device.cleanName): \(plaudSignedOutMessage)")
        syncViewModelState()
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

    /// Lightweight CONCURRENT extractor runner for launch cache-paint reads
    /// only (cached-status / plaud-cached-status). Pure catalog reads — no USB
    /// open, no token persistence, no backoff/cooldown — so several can run at
    /// once. Keeps the serial `runExtractor` for everything that touches the
    /// device or downloads.
    private func runCachedExtractor(arguments: [String], productId: Int? = nil, environment: [String: String] = [:], completion: @escaping (Result<Data, Error>) -> Void) {
        let fullArgs = extractorArguments(arguments, productId: productId)
        log("runCachedExtractor: \(fullArgs.joined(separator: " "))")
        cacheReadQueue.async {
            let process = Process()
            process.currentDirectoryURL = URL(fileURLWithPath: self.extractorRoot)
            process.executableURL = URL(fileURLWithPath: self.extractorPythonPath)
            process.arguments = [self.extractorScriptPath] + fullArgs
            if !environment.isEmpty {
                process.environment = ProcessInfo.processInfo.environment.merging(environment) { _, new in new }
            }
            let tempDir = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
            let outURL = tempDir.appendingPathComponent("hidock-cache-\(UUID().uuidString).out")
            let errURL = tempDir.appendingPathComponent("hidock-cache-\(UUID().uuidString).err")
            do {
                FileManager.default.createFile(atPath: outURL.path, contents: nil)
                FileManager.default.createFile(atPath: errURL.path, contents: nil)
                let outHandle = try FileHandle(forWritingTo: outURL)
                let errHandle = try FileHandle(forWritingTo: errURL)
                process.standardOutput = outHandle
                process.standardError = errHandle
                try process.run()
                let deadline = Date().addingTimeInterval(self.extractorProcessTimeout)
                while process.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.1) }
                if process.isRunning { process.terminate(); process.waitUntilExit() }
                try? outHandle.close()
                try? errHandle.close()
                let outData = (try? Data(contentsOf: outURL)) ?? Data()
                try? FileManager.default.removeItem(at: outURL)
                try? FileManager.default.removeItem(at: errURL)
                if process.terminationStatus == 0 {
                    DispatchQueue.main.async { completion(.success(outData)) }
                } else {
                    DispatchQueue.main.async {
                        completion(.failure(NSError(domain: "HiDockCache", code: Int(process.terminationStatus))))
                    }
                }
            } catch {
                DispatchQueue.main.async { completion(.failure(error)) }
            }
        }
    }

    private func runExtractor(arguments: [String], productId: Int? = nil, environment: [String: String] = [:], completion: @escaping (Result<Data, Error>) -> Void) {
        let fullArgs = extractorArguments(arguments, productId: productId)
        log("runExtractor: \(fullArgs.joined(separator: " "))")
        let deviceKeyForBackoff: String? = productId.map { "hidock:\($0)" }
        syncExtractorQueue.async {
            let process = Process()
            process.currentDirectoryURL = URL(fileURLWithPath: self.extractorRoot)
            process.executableURL = URL(fileURLWithPath: self.extractorPythonPath)
            process.arguments = [self.extractorScriptPath] + fullArgs
            if !environment.isEmpty {
                process.environment = ProcessInfo.processInfo.environment.merging(environment) { _, new in new }
            }
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
                    DispatchQueue.main.async {
                        self.persistRefreshedPlaudTokens(from: outData, environment: environment)
                        completion(.success(outData))
                    }
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

    private func runExtractorWithProgress(arguments: [String], productId: Int? = nil, environment: [String: String] = [:], onProgress: @escaping (Int, Int, Int) -> Void, onFile: ((String, Bool) -> Void)? = nil, completion: @escaping (Result<Data, Error>) -> Void) {
        let fullArgs = extractorArguments(arguments, productId: productId)
        syncExtractorQueue.async {
            let process = Process()
            process.currentDirectoryURL = URL(fileURLWithPath: self.extractorRoot)
            process.executableURL = URL(fileURLWithPath: self.extractorPythonPath)
            process.arguments = [self.extractorScriptPath] + fullArgs
            if !environment.isEmpty {
                process.environment = ProcessInfo.processInfo.environment.merging(environment) { _, new in new }
            }
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
                        } else if line.hasPrefix("FILE_START:") {
                            let name = String(line.dropFirst("FILE_START:".count))
                            DispatchQueue.main.async { onFile?(name, true) }
                        } else if line.hasPrefix("FILE_DONE:") {
                            let name = String(line.dropFirst("FILE_DONE:".count))
                            DispatchQueue.main.async { onFile?(name, false) }
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
                    DispatchQueue.main.async {
                        self.persistRefreshedPlaudTokens(from: finalOut, environment: environment)
                        completion(.success(finalOut))
                    }
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
        // Capture the previous connection state before we overwrite it —
        // auto-download trigger #3 fires on the disconnected→connected
        // transition. Treat "never seen" (nil) as not-connected so the
        // very first live probe after launch counts as a fresh connect.
        let wasConnected = syncDeviceConnected[device.deviceId] ?? false
        // A cached response (cached-status, or a live probe that timed out and
        // fell back to cache) carries connected:false because we didn't get an
        // authoritative live read — NOT because the device is gone. Treat it as
        // a catalog-only paint: update recordings/storage below, but leave the
        // connection baseline untouched so it can't clobber a real Connected
        // state and make the next live probe look "freshly connected" (which
        // spuriously re-fired the download-new catch-all — the flapping bug).
        let isCached = status.cached == true
        syncOutputFolder = status.outputDir
        UserDefaults.standard.set(status.outputDir, forKey: syncOutputFolderKey)
        if !isCached {
            // LED ticker: announce real connect/disconnect transitions.
            if status.connected != wasConnected {
                let verb = status.connected ? "CONNECTED" : "DISCONNECTED"
                viewModel.ledMatrix.notify(LEDEvent(kind: .deviceConnect, text: "\(device.shortName) \(verb)"))
            }
            syncDeviceConnected[device.deviceId] = status.connected
        }
        if status.connected {
            syncDeviceLastOK[device.deviceId] = Date()
            syncDeviceLastError.removeValue(forKey: device.deviceId)
            syncDeviceHungUntil.removeValue(forKey: device.deviceId)
        } else if device.deviceType == .plaud,
                  let err = status.error,
                  err.localizedCaseInsensitiveContains("not signed in") {
            syncDeviceLastError[device.deviceId] = (plaudSignedOutMessage, Date())
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
                    transcribedDate: prev?.transcribedDate,
                    speakersTagged: prev?.speakersTagged ?? false,
                    speakersAutoMatched: prev?.speakersAutoMatched ?? false,
                    summaryPath: prev?.summaryPath,
                    transcriptionSkipped: prev?.transcriptionSkipped ?? false
                ))
            }
        } else {
            log("renderSyncStatus[\(device.shortName)]: empty recordings (connected=\(status.connected)), preserving last-known \(syncEntries.filter { $0.deviceId == device.deviceId }.count) rows")
        }
        let importedAfter = syncEntries.filter { $0.deviceId == IMPORTED_DEVICE_ID }.count
        log("renderSyncStatus[\(device.shortName)]: \(status.recordings.count) device recs, imported before/after = \(importedBefore)/\(importedAfter), total syncEntries=\(syncEntries.count)")

        // Auto-download trigger #2: device file-count rose since we
        // last probed → at least one new recording appeared. Covers
        // the "recorded directly on the HiDock without using the USB
        // mic trigger" case, which previously left files marooned on
        // the device.
        //
        // The baseline-count must be set even for *cached-catalog*
        // responses (extractor returns the last-known catalog with
        // `connected:false` when a probe fails). Without that, on a
        // fresh app launch where the user reconnects a device that
        // has new recordings, the very first successful probe sees
        // `previousCount == nil` and skips the trigger entirely.
        // Storing the cached count establishes the baseline so the
        // first real connection's count rise actually fires the
        // download. We still only TRIGGER the download for
        // genuinely-connected probes — a cached catalog can't be
        // bigger than its previous self anyway, so the rise check
        // naturally rejects spurious cached growth.
        let currentCount = status.recordings.count
        if currentCount > 0 {  // empty catalog = "I don't know", don't reset baseline
            let previousCount = syncDeviceLastSeenCount[device.deviceId]
            syncDeviceLastSeenCount[device.deviceId] = currentCount
            if status.connected,
               let prev = previousCount,
               currentCount > prev,
               syncAutoDownload, !syncDownloading {
                // !syncBusy intentionally not gated here — renderSyncStatus
                // is normally called from inside an auto-connect / refresh
                // probe batch that holds syncBusy=true until the dispatch
                // group's notify block fires. The 2s timer inside
                // scheduleAutoDownloadNewRecordings re-checks !syncBusy
                // post-batch, which is the correct deferral point.
                log("Auto-download: \(device.shortName) recording count \(prev)→\(currentCount), triggering download-new")
                scheduleAutoDownloadNewRecordings()
            }
        }

        // Auto-download trigger #3: device just transitioned from
        // disconnected→connected. Covers the case the count-rise check
        // misses: the catalog hasn't grown since last session, but the
        // recordings on it were never downloaded — without this, those
        // files sit on the device forever because there's no count
        // rise for #2 to detect. Fires on:
        //   - first successful live probe after app launch
        //   - replug after disconnect
        //   - manual ↻ when the previous state was "not connected"
        // Skipped on cached-status (connected=false). `download-new` is
        // a no-op when everything's already downloaded, so the worst
        // case is one extra extractor call per fresh connect.
        if status.connected, !wasConnected, currentCount > 0,
           syncAutoDownload, !syncDownloading,
           syncDeviceCatchAllSweptCount[device.deviceId] != currentCount {
            // !syncBusy intentionally not gated — see comment on trigger #2.
            // Idempotency guard (syncDeviceCatchAllSweptCount) suppresses the
            // re-fire caused by connection flapping on an unchanged catalog.
            log("Auto-download: \(device.shortName) freshly connected with \(currentCount) recording(s) on device, triggering download-new")
            scheduleAutoDownloadNewRecordings()
        }
        // Apply the sync disk-based transcribed check now, before any
        // view update: this ensures rows paint at "Transcribed" rather
        // than flashing "Downloaded" until the async Python refresh
        // finishes. Cheap — one directory listing + O(n) set lookups.
        applyTranscribedFromDiskScan()
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
            // Use the preserved per-device state (anyConnected), not
            // status.connected — a cached response reports connected:false but
            // must not flip the menu to disconnected.
            updateMenuSyncStatus(connected: anyConnected)
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

    private func toggleAutoSummarise() {
        syncAutoSummarise.toggle()
        UserDefaults.standard.set(syncAutoSummarise, forKey: syncAutoSummariseKey)
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

    private func scheduleAutoDownloadNewRecordings(attempt: Int = 0) {
        // No !syncBusy guard at the entry: the 2s debounce timer is the
        // correct deferral point. Triggers #2 and #3 fire from inside a
        // probe batch where syncBusy is true; we still want to schedule,
        // and re-check when the timer fires after the batch settles.
        guard syncAutoDownload, syncPaired else { return }
        syncAutoDownloadTimer?.invalidate()
        syncAutoDownloadTimer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: false) { [weak self] _ in
            guard let self = self, self.syncAutoDownload, self.syncPaired else { return }
            // If the probe batch hasn't settled yet, DON'T drop the request —
            // a batch can easily run longer than the 2s debounce (status +
            // P1 + Plaud + transcription-state refresh). Re-arm and retry
            // until syncBusy clears, bounded so we can't spin forever.
            if self.syncBusy {
                if attempt < 15 {
                    self.scheduleAutoDownloadNewRecordings(attempt: attempt + 1)
                } else {
                    self.log("Auto-download: still busy after \(attempt) retries — giving up; next trigger will retry")
                }
                return
            }
            self.downloadNewSyncRecordings()
        }
    }

    /// Poll interval for the Plaud new-recording check. Defaults to 2 minutes;
    /// override with the `plaudPollIntervalSeconds` user default (0 disables).
    private var plaudPollInterval: TimeInterval {
        let v = UserDefaults.standard.double(forKey: "plaudPollIntervalSeconds")
        return v > 0 ? v : 120
    }

    /// Persist a new Plaud poll cadence (seconds; 0 = off) and restart the timer.
    private func setPlaudPollInterval(_ seconds: Double) {
        UserDefaults.standard.set(seconds, forKey: "plaudPollIntervalSeconds")
        viewModel.plaudPollIntervalSeconds = seconds
        startPlaudPollTimer()   // re-reads the interval; stops the timer if 0
        log("Plaud poll interval set to \(Int(seconds))s")
    }

    private func startPlaudPollTimer() {
        plaudPollTimer?.invalidate()
        let interval = plaudPollInterval
        guard interval > 0 else { log("Plaud poll disabled (interval 0)"); return }
        // Always-on timer; the tick itself no-ops when no Plaud account is
        // paired, so we don't need to start/stop it on pairing changes.
        let t = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            self?.pollPlaudForChanges()
        }
        t.tolerance = min(15, interval * 0.2)   // let the OS coalesce it — saves power
        plaudPollTimer = t
        log("Plaud poll timer started (every \(Int(interval))s)")
    }

    /// Lightweight steady-state check: hit the Plaud API for each paired Plaud
    /// account and, ONLY when its catalog actually changed vs what's displayed,
    /// route through the normal render path (which rebuilds the rows, fires
    /// auto-download, and refreshes transcription state). When nothing changed
    /// we just keep the connection dot fresh — no row rebuild, no library
    /// rescan, no extra subprocesses. Plaud never touches USB, so this can't
    /// interfere with the mic trigger / HiDock.
    private func pollPlaudForChanges() {
        guard !syncBusy else { return }   // don't stack on a live refresh
        let plauds = syncPairedDevices.filter { $0.deviceType == .plaud }
        guard !plauds.isEmpty else { return }
        guard ensureExtractorReady() else { return }

        for device in plauds {
            let args = plaudExtractorArguments("plaud-status", device: device)
            runExtractor(arguments: args, productId: nil, environment: plaudEnvironment(for: device)) { [weak self] result in
                guard let self = self else { return }
                guard case .success(let data) = result,
                      let payload = try? JSONDecoder().decode(HiDockSyncStatusResponse.self, from: data) else { return }
                DispatchQueue.main.async {
                    let polled = Set(payload.recordings.map { $0.name })
                    let current = Set(
                        self.syncEntries.filter { $0.deviceId == device.deviceId }.map { $0.recording.name }
                    )
                    // Non-empty + differs from what's shown → something changed.
                    // (Empty = signed-out / transient error — don't wipe rows.)
                    if !payload.recordings.isEmpty, polled != current {
                        self.log("Plaud poll: \(device.cleanName) catalog changed (\(current.count)→\(polled.count)) — refreshing")
                        self.renderSyncStatus(payload, device: device)   // rebuild + auto-download + disk scan
                        self.refreshTranscriptionState()
                        self.syncViewModelState()
                    } else {
                        // Unchanged — just keep the card's connection state fresh.
                        if payload.connected { self.syncDeviceLastOK[device.deviceId] = Date() }
                        self.syncDeviceConnected[device.deviceId] = payload.connected
                        self.syncViewModelState()
                    }
                    // Sweep any pending (undownloaded) Plaud recordings. The
                    // renderSyncStatus auto-download triggers only fire on a
                    // count-rise or a fresh connect, so a static backlog of
                    // "On device" cloud recordings never downloaded on its own.
                    // With auto-download on, the poll is the natural place to
                    // pull them.
                    self.autoDownloadPendingPlaud(device, payload: payload)
                }
            }
        }
    }

    /// When auto-download is on, pull any undownloaded Plaud cloud recordings.
    /// Plaud has no count-rise/fresh-connect event for an existing backlog, so
    /// this poll-driven sweep is what actually fetches "On device" items.
    /// Guarded on syncBusy/syncDownloading so it never stacks; the poll cadence
    /// (default 2 min) doubles as the retry interval for any that failed.
    private func autoDownloadPendingPlaud(_ device: HiDockPairedDevice, payload: HiDockSyncStatusResponse) {
        guard viewModel.syncAutoDownload, !syncBusy, !syncDownloading, payload.connected else { return }
        let hasPending = payload.recordings.contains { !$0.downloaded && !($0.removed ?? false) }
        guard hasPending else { return }
        log("Plaud poll: auto-downloading pending recordings for \(device.cleanName)")
        syncBusy = true
        syncViewModelState()
        downloadNewFromDevices([device], totalDownloaded: 0, freshDownloads: [])
    }

    private func refreshSyncStatus(manual: Bool = false) {
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
            if device.deviceType == .volume || device.deviceType == .plaud { return true }
            if !recording { return true }
            log("Refresh: skipping \(device.cleanName) — ffmpeg is currently recording, keeping last-known state")
            return false
        }
        if probeDevices.isEmpty {
            log("Refresh: no devices to probe (trigger active, all HiDocks skipped)")
            // Leave status/busy state untouched; nothing to do.
            return
        }
        // Only the explicit ↻ (manual) shows the global "Refreshing…" status
        // line. Auto/initial refreshes stay silent — the per-device cards
        // already show "Connecting…" chips, so a ticking global banner on load
        // was redundant and read as "stuck".
        performRefreshProbes(devices: probeDevices, restartTriggerAfter: false, showStatus: manual)
    }

    private func performRefreshProbes(devices: [HiDockPairedDevice], restartTriggerAfter: Bool, showStatus: Bool = true) {
        syncBusy = true
        if showStatus {
            viewModel.syncStatus = "Refreshing..."
            viewModel.syncStatusLevel = .secondary
            startSyncRefreshTimer()
        }
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
            case .plaud:
                args = plaudExtractorArguments("plaud-status", device: device)
                pid = nil
            }

            runExtractor(arguments: args, productId: pid, environment: plaudEnvironment(for: device)) { [weak self] result in
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
                // Mirror the auto-connect path: suppress the global
                // banner when every paired device is just unplugged.
                // Reserve it for genuinely unusual errors.
                let allJustMissing = deviceErrors.values.allSatisfy {
                    $0.localizedCaseInsensitiveContains("not found")
                        && !$0.contains("held by")
                        && !$0.localizedCaseInsensitiveContains("access denied")
                        && !$0.contains("Errno 13")
                }
                if !allJustMissing {
                    let bestError = deviceErrors.values.first(where: { $0.contains("held by") })
                        ?? deviceErrors.values.first ?? "unknown"
                    let message = syncErrorDescription(bestError)
                    self.viewModel.syncStatus = message
                    self.viewModel.syncStatusLevel = .error
                }
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
            let environment: [String: String]
            if let device = device, device.deviceType == .volume {
                args = ["mark-downloaded", "--volume-name", device.volumeName ?? ""] + filenames
                pid = nil
                environment = [:]
            } else if let device = device, device.deviceType == .plaud {
                args = ["mark-downloaded", "--plaud-account", device.plaudAccountId ?? ""] + filenames
                pid = nil
                environment = plaudEnvironment(for: device)
            } else {
                args = ["mark-downloaded"] + filenames
                pid = device?.productId
                environment = [:]
            }

            log("Skip-download[\(device?.shortName ?? deviceId)]: mark-downloaded \(filenames.count) file(s), pid=\(pid.map(String.init) ?? "nil")")
            group.enter()
            runExtractor(arguments: args, productId: pid, environment: environment) { [weak self] result in
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
            let environment: [String: String]
            if let device = device, device.deviceType == .volume {
                args = ["unmark-downloaded", "--volume-name", device.volumeName ?? ""] + filenames
                pid = nil
                environment = [:]
            } else if let device = device, device.deviceType == .plaud {
                args = ["unmark-downloaded", "--plaud-account", device.plaudAccountId ?? ""] + filenames
                pid = nil
                environment = plaudEnvironment(for: device)
            } else {
                args = ["unmark-downloaded"] + filenames
                pid = device?.productId
                environment = [:]
            }

            group.enter()
            runExtractor(arguments: args, productId: pid, environment: environment) { result in
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
        viewModel.ledMatrix.setStatus(nil)
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
        let environment: [String: String]
        if let device = pairedDevice(for: current), device.deviceType == .volume {
            args = volumeExtractorArguments("volume-import", device: device, extra: [current.recording.name])
            pid = nil
            environment = [:]
        } else if let device = pairedDevice(for: current), device.deviceType == .plaud {
            args = plaudExtractorArguments("plaud-download", device: device, extra: [current.recording.name])
            pid = nil
            environment = plaudEnvironment(for: device)
        } else {
            args = ["download", current.recording.name, "--length", "\(current.recording.length)"]
            pid = current.deviceProductId
            environment = [:]
        }

        runExtractorWithProgress(arguments: args, productId: pid, environment: environment, onProgress: { [weak self] received, total, pct in
            guard let self = self else { return }
            let receivedMB = String(format: "%.1f", Double(received) / 1_000_000)
            let totalMB = String(format: "%.1f", Double(total) / 1_000_000)
            self.viewModel.syncStatus = "Downloading \(current.recording.outputName) — \(pct)% (\(receivedMB)/\(totalMB) MB)"
            self.viewModel.syncDownloadProgress = "\(pct)% (\(receivedMB)/\(totalMB) MB)"
            self.viewModel.ledMatrix.setStatus("\(LEDFont.arrowDown) \(pct)%", color: .blue)
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
        // Stay visually quiet up front. Most auto-sweeps are no-ops (the
        // fresh-connect catch-all with nothing new), and flashing a
        // "Downloading new recordings…" banner + progress bar for those was
        // misleading. The download UI is revealed lazily — only once a file
        // actually starts downloading (beginDownloadProgressIfNeeded, called
        // from the per-file/progress callbacks). A genuine download still
        // shows full progress; a no-op sweep stays silent.
        //
        // Record the catalog size we're about to sweep per device so a
        // flapping connection can't re-trigger the catch-all for an unchanged
        // catalog. Done here (sweep actually running) rather than at schedule
        // time so a timer skipped on `syncBusy` can't permanently consume it.
        for d in devices {
            if let c = syncDeviceLastSeenCount[d.deviceId] {
                syncDeviceCatchAllSweptCount[d.deviceId] = c
            }
        }
        syncViewModelState()

        downloadNewFromDevices(devices, totalDownloaded: 0, freshDownloads: [])
    }

    /// Reveal the download progress UI (progress bar + banner + elapsed timer)
    /// the first time a file actually starts downloading during a download-new
    /// sweep. No-op if already shown. Keeps no-op sweeps silent (see
    /// downloadNewSyncRecordings).
    private func beginDownloadProgressIfNeeded() {
        guard !syncDownloading else { return }
        syncDownloading = true
        startDownloadTimer()
        viewModel.syncStatus = "Downloading new recordings..."
        viewModel.syncStatusLevel = .secondary
        syncViewModelState()
    }

    private func downloadNewFromDevices(_ remaining: [HiDockPairedDevice], totalDownloaded: Int, freshDownloads: [HiDockSyncDownloadResult]) {
        guard let device = remaining.first else {
            stopDownloadTimer()
            syncBusy = false
            syncDownloading = false
            currentlyDownloadingName = nil
            if totalDownloaded > 0 {
                let body = totalDownloaded == 1
                    ? "1 new recording was saved successfully."
                    : "\(totalDownloaded) new recordings were saved successfully."
                postSyncDownloadNotification(title: "✅ Downloads Complete", body: body)
                viewModel.syncStatus = "Downloaded \(totalDownloaded) new recordings"
                viewModel.syncStatusLevel = .success
                viewModel.ledMatrix.notify(LEDEvent(kind: .syncComplete, text: "\(LEDFont.check) SYNC \(totalDownloaded) NEW"))
            }
            // Flip the just-downloaded rows to "Downloaded" immediately, keyed by
            // filename, rather than waiting for the async status re-probe below
            // (which can lag ~15s and made rows linger on "On device" — and even
            // jump to "Transcribing" — before showing "Downloaded").
            if !freshDownloads.isEmpty {
                let byName = Dictionary(freshDownloads.map { ($0.filename, $0.outputPath) },
                                        uniquingKeysWith: { a, _ in a })
                for i in syncEntries.indices {
                    if let path = byName[syncEntries[i].recording.name],
                       !syncEntries[i].recording.localExists {
                        syncEntries[i].recording = syncEntries[i].recording.markedDownloaded(outputPath: path)
                    }
                }
                syncViewModelState()
            }
            // When nothing was downloaded (the common no-op auto-sweep), leave
            // the status line quiet — refreshSyncStatus restores the normal
            // connected/blank state rather than a misleading "Downloaded 0".
            refreshSyncStatus()
            // Auto-transcribe combines two sources so nothing is missed:
            //
            //   1. Fresh downloads (`freshDownloads`) — the extractor's
            //      own download-new response. We must use this because
            //      refreshSyncStatus above is async and hasn't populated
            //      `visibleEntries` with the just-downloaded files yet;
            //      filtering visibleEntries alone would skip every
            //      recording downloaded in this pass. (This is exactly
            //      how Rec59 got missed: auto-transcribe fired ~14s
            //      before the status probe returned with the new file.)
            //
            //   2. Backlog (`syncEntries`) — rows that WERE already in
            //      the table before this download run but remained
            //      untranscribed (e.g. user toggled auto-transcribe off
            //      previously, or a prior transcription failed). Read
            //      from syncEntries (ALL entries), not visibleEntries —
            //      an active device / heatmap-day / status filter would
            //      silently shrink the sweep to whatever the user
            //      happens to be looking at.
            //
            // Fresh paths come first so the newest recordings jump the
            // queue — matches the default newest-first table sort.
            // Dedupe by path so a file that somehow appears in both
            // lists isn't enqueued twice.
            if self.syncAutoTranscribe, ensureTranscriptionReady() {
                let freshPaths = freshDownloads.map(\.outputPath)
                let backlogPaths = self.syncEntries
                    .filter { $0.recording.localExists && !$0.transcribed && !$0.transcriptionSkipped }
                    .map(\.recording.outputPath)
                var seen = Set<String>()
                let combined = (freshPaths + backlogPaths).filter { seen.insert($0).inserted }
                if !combined.isEmpty {
                    self.log("Auto-transcribe: \(combined.count) recording(s) to process (\(freshPaths.count) fresh + \(backlogPaths.count) backlog, deduped)")
                    self.enqueueTranscriptions(combined)
                }
            }
            syncViewModelState()
            return
        }

        // Choose extractor command based on device type
        let args: [String]
        let pid: Int?
        let environment: [String: String]
        switch device.deviceType {
        case .hidock:
            args = ["download-new"]
            pid = device.productId
            environment = [:]
        case .volume:
            args = volumeExtractorArguments("volume-import-new", device: device)
            pid = nil
            environment = [:]
        case .plaud:
            args = plaudExtractorArguments("plaud-download-new", device: device)
            pid = nil
            environment = plaudEnvironment(for: device)
        }

        runExtractorWithProgress(arguments: args, productId: pid, environment: environment, onProgress: { [weak self] received, total, pct in
            guard let self = self else { return }
            self.beginDownloadProgressIfNeeded()
            let receivedMB = String(format: "%.1f", Double(received) / 1_000_000)
            let totalMB = String(format: "%.1f", Double(total) / 1_000_000)
            self.viewModel.syncStatus = "Downloading (\(device.cleanName)) — \(pct)% (\(receivedMB)/\(totalMB) MB)"
            self.viewModel.syncDownloadProgress = "\(pct)% (\(receivedMB)/\(totalMB) MB)"
            self.viewModel.ledMatrix.setStatus("\(LEDFont.arrowDown) \(pct)%", color: .blue)
        }, onFile: { [weak self] name, started in
            guard let self = self else { return }
            if started { self.beginDownloadProgressIfNeeded() }
            self.currentlyDownloadingName = started ? name : nil
            self.log(started ? "FILE_START: \(name)" : "FILE_DONE: \(name)")
            self.syncViewModelState()
        }) { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success(let data):
                var deviceDownloaded = 0
                var devicePayloads: [HiDockSyncDownloadResult] = []
                if let payload = try? JSONDecoder().decode(HiDockSyncDownloadNewResponse.self, from: data) {
                    if let error = payload.error {
                        self.log("download-new error for \(device.cleanName): \(error)")
                    }
                    deviceDownloaded = payload.downloaded.count
                    devicePayloads = payload.downloaded
                    // NOTE: deliberately do NOT change the device/status filters
                    // here. Filters only ever change in response to an explicit
                    // user action (filter button / device card), never as a
                    // side effect of downloading or transcribing.
                }
                self.downloadNewFromDevices(
                    Array(remaining.dropFirst()),
                    totalDownloaded: totalDownloaded + deviceDownloaded,
                    freshDownloads: freshDownloads + devicePayloads
                )
            case .failure(let error):
                if self.syncDownloadStopping {
                    self.syncDownloadStopping = false
                    self.stopDownloadTimer()
                    self.syncBusy = false
                    self.syncDownloading = false
                    self.currentlyDownloadingName = nil
                    self.syncViewModelState()
                    return
                }
                self.log("download-new failed for \(device.cleanName): \(error.localizedDescription)")
                self.downloadNewFromDevices(
                    Array(remaining.dropFirst()),
                    totalDownloaded: totalDownloaded,
                    freshDownloads: freshDownloads
                )
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

    /// Kick off the merge-rediarize subprocess for a merged file. Reuses
    /// `runTranscription` to drive the subprocess with the same progress
    /// + stage + timeout machinery — only the args and the post-completion
    /// status string differ from a normal full transcribe.
    private func runMergeRediarize(mergedPath: String, pieceEntries: [HiDockSyncRecordingEntry]) {
        var args = ["merge-rediarize", mergedPath, "--pieces"]
        args.append(contentsOf: pieceEntries.map(\.recording.outputPath))
        if diarizeEnabled || true {
            // Always summarize the merged file — a merged meeting
            // deserves its own coherent summary. The original per-piece
            // summaries are still on disk attached to each child.
            args.append("--summarize")
        }
        // Diarization on a 90-min merge takes minutes, not the hour-plus
        // a fresh Whisper pass would. Bound generously: 30 min + 1× the
        // estimated audio duration via file size.
        let mergedSize = (try? FileManager.default.attributesOfItem(atPath: mergedPath)[.size] as? Int) ?? 0
        let estAudioSec = Double(mergedSize) / 8000.0   // matches the rest of the codebase's rough estimate
        let timeout = max(estAudioSec + 1800, 600)

        let mergedName = (mergedPath as NSString).lastPathComponent
        viewModel.syncStatus = "Stitching transcripts + rediarizing \(mergedName)…"
        viewModel.syncStatusLevel = .secondary
        syncViewModelState()

        runTranscription(arguments: args, timeout: timeout) { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success:
                self.log("Merge-rediarize complete: \(mergedPath)")
                self.viewModel.syncStatus = "Merged transcript ready"
                self.viewModel.syncStatusLevel = .success
            case .failure(let err):
                self.log("Merge-rediarize failed: \(err.localizedDescription)")
                self.viewModel.syncStatus = "Merge transcript failed"
                self.viewModel.syncStatusLevel = .error
            }
            self.refreshTranscriptionState()
            self.syncViewModelState()
        }
    }

    private func runTranscription(arguments: [String], timeout: TimeInterval = 600, stdin: String? = nil, onProgress: ((Int) -> Void)? = nil, onStage: ((Int, Int, String) -> Void)? = nil, onLine: ((String) -> Void)? = nil, completion: @escaping (Result<Data, Error>) -> Void) {
        // Single chokepoint for the summarisation provider: when a run asks to
        // summarise and the user picked a specific engine, pass it through.
        // "auto" omits the flag so the pipeline keeps its config/auto default.
        var arguments = arguments
        if arguments.contains("--summarize"), summarizeEngine != "auto",
           !arguments.contains("--summarize-engine") {
            arguments.append(contentsOf: ["--summarize-engine", summarizeEngine])
        }
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

            // Optional stdin (e.g. the `ask` subcommand reads its prompt from
            // stdin to avoid arg-escaping issues with long/multiline prompts).
            let inPipe: Pipe? = stdin != nil ? Pipe() : nil
            if let inPipe = inPipe { process.standardInput = inPipe }

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
                        if let onLine = onLine {
                            DispatchQueue.main.async { onLine(line) }
                        }
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
                DispatchQueue.main.async { self.transcriptionSubprocess = process }
                NSLog("runTranscription: process started (pid %d)", process.processIdentifier)

                // Feed stdin then close so the child sees EOF and proceeds.
                if let inPipe = inPipe, let stdin = stdin {
                    let handle = inPipe.fileHandleForWriting
                    if let data = stdin.data(using: .utf8) { handle.write(data) }
                    try? handle.close()
                }

                let deadline = Date().addingTimeInterval(timeout)
                while process.isRunning && Date() < deadline {
                    Thread.sleep(forTimeInterval: 0.5)
                }
                // Track whether WE killed the process (timeout) — same
                // weKilledIt flag runExtractor uses. terminationReason
                // alone can't tell a timeout kill from a genuine signal
                // crash (SIGSEGV), and Python may trap SIGTERM and exit
                // "normally" with 143.
                var weKilledIt = false
                if process.isRunning {
                    NSLog("runTranscription: killing hung process (pid %d) after %ds", process.processIdentifier, Int(timeout))
                    weKilledIt = true
                    process.terminate()
                    Thread.sleep(forTimeInterval: 1)
                    if process.isRunning { process.interrupt() }
                    // Escalate to SIGKILL after a short grace period —
                    // mirrors cancelTranscription: the pipeline can be deep
                    // inside a non-interruptible native call (MPS matmul,
                    // torch model load) where SIGTERM/SIGINT never land,
                    // and without SIGKILL the waitUntilExit below would
                    // block this serial queue forever, wedging the whole
                    // transcription queue.
                    if process.isRunning {
                        Thread.sleep(forTimeInterval: 2)
                        if process.isRunning {
                            NSLog("runTranscription: SIGKILL pid %d (didn't respond to SIGTERM/SIGINT)", process.processIdentifier)
                            kill(process.processIdentifier, SIGKILL)
                        }
                    }
                    process.waitUntilExit()
                } else {
                    NSLog("runTranscription: process exited with status %d", process.terminationStatus)
                }
                DispatchQueue.main.async { self.transcriptionSubprocess = nil }

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
                } else if weKilledIt {
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
                    // Badge completions that finish while the user isn't looking
                    // at the main window; focusing it clears the count.
                    if self.syncWindow?.isKeyWindow != true {
                        self.viewModel.sessionTranscribedCount += 1
                        self.updateTranscribedBadge()
                    }
                    if self.syncAutoSummarise { self.pendingAutoSummariseNames.insert(filename) }
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

        let ready = all.filter { $0.recording.localExists }
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
        // Items that have previously failed, been cancelled, or completed
        // still live in `pendingTranscriptionQueue` so the Queue window can
        // show their final state. Without resetting their status here, a user
        // who re-selects those rows and hits Transcribe Selected sees
        // nothing happen: the existing item isn't `.queued`, so
        // processNextInQueue skips it, and the duplicate guard below
        // stops us appending a fresh entry. Retry by flipping the
        // terminal status back to `.queued` and clearing progress so
        // the retry shows a clean 0%. `.completed` is included so the
        // "Already Transcribed → Re-transcribe?" flow actually re-runs
        // in the same session instead of silently no-op'ing.
        transcriptionCancelled = false
        for path in paths {
            if let idx = pendingTranscriptionQueue.firstIndex(where: { $0.path == path }) {
                let st = pendingTranscriptionQueue[idx].status
                if st == .failed || st == .cancelled || st == .completed {
                    pendingTranscriptionQueue[idx].status = .queued
                    pendingTranscriptionQueue[idx].progress = 0
                    pendingTranscriptionQueue[idx].errorMessage = nil
                }
                continue
            }
            pendingTranscriptionQueue.append(TranscriptionQueueItem(path: path))
        }
        log("Enqueued \(paths.count) recording(s), queue size: \(pendingTranscriptionQueue.count)")
        syncViewModelState()
        processNextInQueue()
    }

    /// Ratcheting progress setter: the bar only ever moves forward.
    /// All three sources (synthetic timer, real PROGRESS: from Whisper,
    /// STAGE: stage floors) call this. Previously each source wrote
    /// directly to `transcriptionProgress`, so they overwrote each
    /// other — STAGE 1/5 set 20%, then real Whisper progress dropped
    /// back to 5%, then STAGE 4/5 jumped to 80%. The user saw the
    /// bar bouncing. Taking a max here (per item) keeps it monotonic.
    private func applyTranscriptionProgress(_ candidate: Int, itemPath: String, filename: String, position: Int, total: Int) {
        let clamped = max(0, min(candidate, 100))
        let next = max(transcriptionProgress, clamped)
        guard next != transcriptionProgress else { return }
        transcriptionProgress = next
        if let idx = pendingTranscriptionQueue.firstIndex(where: { $0.path == itemPath }) {
            pendingTranscriptionQueue[idx].progress = next
        }
        viewModel.syncStatus = "Transcribing \(filename) — \(next)% (\(position)/\(total))"
        syncViewModelState()
    }

    /// Map a STAGE: line from transcribe.py to a percent floor that
    /// roughly matches how long each stage takes on Whisper-MPS for a
    /// typical meeting. Equal-weighting (N/M*100) was misleading
    /// because "Loading model" is a couple of seconds while
    /// "Transcribing" is most of the run. Falls back to the raw N/M
    /// ratio for stages we don't know about, so future pipeline
    /// changes degrade gracefully.
    private func progressFloorForStage(label: String, current: Int, stageTotal: Int) -> Int {
        let normalised = label.lowercased()
        if normalised.contains("loading") { return 5 }
        if normalised.contains("transcribing") { return 10 }
        if normalised.contains("applying") || normalised.contains("correction") { return 80 }
        if normalised.contains("diariz") { return 85 }
        if normalised.contains("writing") { return 95 }
        return Int(Double(current) / Double(max(stageTotal, 1)) * 100)
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
            // The queue just drained; new transcripts on disk may
            // unlock new merge candidates (the detector requires
            // every piece in a chain to have a transcript). Cheap to
            // re-run, ~1s.
            scanMergeCandidates()
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

        // Start a Swift-side timer for synthetic progress (Python GIL blocks the progress thread).
        // The three progress sources (synthetic, real PROGRESS: lines,
        // STAGE: lines) all funnel through a ratcheting setter so the
        // bar is monotonic — never drops back. Previously they
        // overwrote each other, producing the "20% → 5% → 85%" bounce.
        transcriptionProgressTimer?.invalidate()
        transcriptionProgressTimer = Timer.scheduledTimer(withTimeInterval: 3.0, repeats: true) { [weak self] _ in
            guard let self = self, self.transcriptionBusy else { return }
            guard let startTime = self.transcriptionStartTime else { return }
            let elapsed = Date().timeIntervalSince(startTime)
            let frac = min(elapsed / self.transcriptionEstimatedDuration, 0.95)
            let syntheticPct = max(15, min(Int(15 + frac * 70), 84))
            self.applyTranscriptionProgress(syntheticPct, itemPath: item.path, filename: filename, position: position, total: total)
        }

        viewModel.ledMatrix.notify(LEDEvent(kind: .transcription, text: "TRANSCRIBING \(filename)"))
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
            self.applyTranscriptionProgress(pct, itemPath: item.path, filename: filename, position: position, total: total)
        }, onStage: { [weak self] current, stageTotal, label in
            guard let self = self else { return }
            // Map the stage to a time-realistic floor instead of
            // raw N/M*100. Stages aren't equal-duration: "Loading
            // model" is < 5% of total time but used to render as
            // 20% (1/5), making the bar leap forward and then stall.
            // The new mapping reflects roughly how long each stage
            // takes on Whisper-MPS for a typical 30-min meeting.
            let floor = self.progressFloorForStage(label: label, current: current, stageTotal: stageTotal)
            self.applyTranscriptionProgress(floor, itemPath: item.path, filename: filename, position: position, total: total)
            // Publish the stage to transcriptionStatus so the bar at
            // the top of the window shows "Transcribing N/M — p% ·
            // Diarizing speakers 4/5". Previously this was written
            // to syncStatus, which rendered in a separate dot row
            // below the device cards — now hidden during transcription
            // to avoid two live indicators.
            self.viewModel.transcriptionStatus = "\(label) \(current)/\(stageTotal)"
            self.syncViewModelState()
        }) { [weak self] result in
            guard let self = self else { return }
            self.transcriptionProgressTimer?.invalidate()
            self.transcriptionProgressTimer = nil
            self.transcriptionBusy = false
            self.transcriptionCurrentFile = nil
            self.transcriptionProgress = 0
            self.viewModel.transcriptionStatus = ""

            // Don't overwrite a user cancellation: cancelTranscription has
            // already marked the item .cancelled (with its own message)
            // before killing the subprocess — the failure completion for
            // the killed process would otherwise flip it to .failed with
            // a misleading error.
            if let idx = self.pendingTranscriptionQueue.firstIndex(where: { $0.path == item.path }),
               self.pendingTranscriptionQueue[idx].status != .cancelled {
                switch result {
                case .success:
                    self.pendingTranscriptionQueue[idx].status = .completed
                    self.pendingTranscriptionQueue[idx].errorMessage = nil
                    self.viewModel.ledMatrix.notify(LEDEvent(kind: .transcription, text: "\(LEDFont.check) \(filename)"))
                    // Flag for auto-summarise; refreshTranscriptionState
                    // (called just below) populates transcriptPath, then
                    // queues the typed summary for these names.
                    if self.syncAutoSummarise { self.pendingAutoSummariseNames.insert(filename) }
                case .failure(let err):
                    self.pendingTranscriptionQueue[idx].status = .failed
                    self.viewModel.ledMatrix.notify(LEDEvent(kind: .error, text: "\(LEDFont.cross) TRANSCRIBE FAILED"))
                    // The NSError carries the trimmed stderr text
                    // assembled by `runTranscription` — exactly what we
                    // want shown when the user clicks the red X icon.
                    self.pendingTranscriptionQueue[idx].errorMessage =
                        err.localizedDescription
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

        // Actually kill the subprocess. Without this, the UI flipped
        // to "cancelled" but whisper/diarize kept burning CPU and
        // producing a transcript the user didn't want.
        if let p = transcriptionSubprocess, p.isRunning {
            log("Cancel: terminating transcription subprocess pid \(p.processIdentifier)")
            p.terminate()
            // Escalate to SIGKILL after a short grace period in case
            // the Python pipeline is deep inside a non-interruptible
            // call (MPS matmul, torch model load, etc.).
            DispatchQueue.global().asyncAfter(deadline: .now() + 2.0) { [weak p] in
                guard let p = p, p.isRunning else { return }
                NSLog("Cancel: SIGKILL transcription pid %d (didn't respond to SIGTERM)", p.processIdentifier)
                kill(p.processIdentifier, SIGKILL)
            }
        }
        transcriptionSubprocess = nil

        // Stop the progress timer
        transcriptionProgressTimer?.invalidate()
        transcriptionProgressTimer = nil

        // Mark the currently transcribing item as cancelled
        for i in pendingTranscriptionQueue.indices {
            if pendingTranscriptionQueue[i].status == .transcribing {
                pendingTranscriptionQueue[i].status = .cancelled
                pendingTranscriptionQueue[i].errorMessage = "Cancelled mid-run by user."
            }
            if pendingTranscriptionQueue[i].status == .queued {
                pendingTranscriptionQueue[i].status = .cancelled
                pendingTranscriptionQueue[i].errorMessage = "Cancelled before this item ran."
            }
        }

        transcriptionBusy = false
        transcriptionCurrentFile = nil
        transcriptionProgress = 0
        viewModel.transcriptionStatus = ""
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
        showDetailTab(id: "queue", title: "Transcription Queue", icon: "list.bullet.rectangle", view: TranscriptionQueueView(viewModel: viewModel))
    }

    /// Synchronous, filesystem-only pass that marks entries as
    /// transcribed when a matching `<basename>.md` exists in the
    /// transcripts folder. Prevents the "Downloaded → Transcribed"
    /// flash on launch: `refreshTranscriptionState` spawns Python
    /// (`transcribe.py status`) which takes long enough that the user
    /// sees stale "Downloaded" rows until it returns. This pass is
    /// effectively free — one `contentsOfDirectory` + set lookups —
    /// and lands the table on the correct pipeline-end status (the
    /// cascade already prefers Transcribed over Downloaded). The
    /// async refresh still runs afterwards to fill in `speakersTagged`
    /// / `summaryPath` / canonical `transcriptPath`.
    /// The transcript file's modification time — used as the "transcribed on"
    /// date for the table column (transcripts are written once at transcription
    /// time, so mtime ≈ when it was transcribed). nil if no path / not on disk.
    private func transcriptModificationDate(_ path: String?) -> Date? {
        guard let path = path, !path.isEmpty,
              let attrs = try? FileManager.default.attributesOfItem(atPath: path) else { return nil }
        return attrs[.modificationDate] as? Date
    }

    private func applyTranscribedFromDiskScan() {
        let transcriptDir = syncTranscriptFolder ?? "\(NSHomeDirectory())/HiDock/Raw Transcripts"
        guard let contents = try? FileManager.default.contentsOfDirectory(atPath: transcriptDir) else { return }
        let mdBaseNames = Set(
            contents
                .filter { $0.hasSuffix(".md") }
                .map { ($0 as NSString).deletingPathExtension }
        )
        guard !mdBaseNames.isEmpty else { return }
        var flipped = 0
        for i in syncEntries.indices {
            let base = (syncEntries[i].recording.outputName as NSString).deletingPathExtension
            if mdBaseNames.contains(base) && !syncEntries[i].transcribed {
                syncEntries[i].transcribed = true
                if syncEntries[i].transcriptPath == nil {
                    syncEntries[i].transcriptPath = (transcriptDir as NSString).appendingPathComponent(base + ".md")
                }
                syncEntries[i].transcribedDate = transcriptModificationDate(syncEntries[i].transcriptPath)
                // Compute the tagging state here too. Without it the row shows a
                // transient "needs tagging" (orange) until the async status
                // refresh computes the real state — which flickered against the
                // auto-matched (blue ?) state on load.
                let review = speakerReviewState(transcriptPath: syncEntries[i].transcriptPath)
                syncEntries[i].speakersTagged = review.tagged
                syncEntries[i].speakersAutoMatched = review.autoMatched
                flipped += 1
            }
        }
        if flipped > 0 {
            log("applyTranscribedFromDiskScan: flipped \(flipped) row(s) to Transcribed from disk")
        }
    }

    /// Fetch per-transcript speaker / action-item counts (heatmap Tier-2
    /// tooltip) via `transcribe.py activity-stats`. Small JSON; refreshed
    /// alongside transcription state.
    private func refreshMeetingExtraStats() {
        guard FileManager.default.fileExists(atPath: transcriptionScriptPath),
              FileManager.default.isExecutableFile(atPath: transcriptionPythonPath) else { return }
        transcriptionDispatchQueue.async {
            let process = Process()
            process.currentDirectoryURL = URL(fileURLWithPath: self.transcriptionRoot)
            process.executableURL = URL(fileURLWithPath: self.transcriptionPythonPath)
            process.arguments = [self.transcriptionScriptPath, "activity-stats"]
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
            process.standardError = Pipe()
            do { try process.run() } catch {
                self.log("activity-stats: launch failed: \(error.localizedDescription)")
                return
            }
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: [String: Int]] else { return }
            var map: [String: (speakers: Int, actionItems: Int)] = [:]
            for (name, counts) in obj {
                map[name] = (speakers: counts["speakers"] ?? 0, actionItems: counts["action_items"] ?? 0)
            }
            DispatchQueue.main.async {
                self.viewModel.meetingExtraStats = map
                self.log("activity-stats: loaded speaker/action counts for \(map.count) transcript(s)")
            }
        }
    }

    private func refreshTranscriptionState() {
        guard FileManager.default.fileExists(atPath: transcriptionScriptPath),
              FileManager.default.isExecutableFile(atPath: transcriptionPythonPath) else {
            log("refreshTranscriptionState: skipping — script=\(FileManager.default.fileExists(atPath: transcriptionScriptPath)), python=\(FileManager.default.isExecutableFile(atPath: transcriptionPythonPath)) at \(transcriptionScriptPath)")
            return
        }
        // Refresh the heatmap Tier-2 stats alongside (own async, small payload).
        refreshMeetingExtraStats()

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

            // Drain stdout/stderr concurrently with the child. `transcribe.py
            // status` prints the full state.json (>100 KB once the backlog
            // grows) and the macOS pipe buffer is only ~16–64 KB, so a
            // read-after-waitUntilExit pattern deadlocks: child blocks on
            // write(), parent blocks on waitUntilExit. Because this runs on
            // the same serial `transcriptionDispatchQueue` as the actual
            // transcribe subprocess, that deadlock wedges the entire
            // transcription queue.
            var outData = Data()
            let outQueue = DispatchQueue(label: "hidock.refreshTranscriptionState.stdout")
            pipe.fileHandleForReading.readabilityHandler = { handle in
                let chunk = handle.availableData
                if !chunk.isEmpty { outQueue.sync { outData.append(chunk) } }
            }
            var errData = Data()
            let errQueue = DispatchQueue(label: "hidock.refreshTranscriptionState.stderr")
            errPipe.fileHandleForReading.readabilityHandler = { handle in
                let chunk = handle.availableData
                if !chunk.isEmpty { errQueue.sync { errData.append(chunk) } }
            }

            do {
                try process.run()
                process.waitUntilExit()
            } catch {
                pipe.fileHandleForReading.readabilityHandler = nil
                errPipe.fileHandleForReading.readabilityHandler = nil
                self.log("refreshTranscriptionState: failed to run process: \(error.localizedDescription)")
                return
            }

            pipe.fileHandleForReading.readabilityHandler = nil
            errPipe.fileHandleForReading.readabilityHandler = nil
            let tailOut = pipe.fileHandleForReading.readDataToEndOfFile()
            if !tailOut.isEmpty { outQueue.sync { outData.append(tailOut) } }
            let tailErr = errPipe.fileHandleForReading.readDataToEndOfFile()
            if !tailErr.isEmpty { errQueue.sync { errData.append(tailErr) } }

            let data = outQueue.sync { outData }
            if process.terminationStatus != 0 {
                let stderrBytes = errQueue.sync { errData }
                let errMsg = String(data: stderrBytes, encoding: .utf8) ?? "unknown error"
                self.log("refreshTranscriptionState: exit=\(process.terminationStatus), stderr=\(errMsg.prefix(400))")
                return
            }
            guard let lookup = try? JSONSerialization.jsonObject(with: data) as? [String: [String: Any]] else {
                self.log("refreshTranscriptionState: failed to decode JSON (\(data.count) bytes, first 200 chars=\(String(data: data, encoding: .utf8)?.prefix(200) ?? "?"))")
                return
            }

            DispatchQueue.main.async {
                self.log("refreshTranscriptionState: got \(lookup.count) entries from status")
                // Build the summary index ONCE (stem → path). Previously
                // findSummaryPath listed the entire ~1400-file Summaries dir per
                // entry — ~1700 full directory scans per refresh, on the main
                // thread → beachball. Now it's one listing + O(1) lookups.
                let summaryIndex = self.buildSummaryIndex()
                var matched = 0
                // recording name → the named people in that meeting (people filter).
                var meetingPeople: [String: Set<String>] = [:]
                for i in self.syncEntries.indices {
                    let mp3Name = self.syncEntries[i].recording.outputName
                    if let info = lookup[mp3Name] {
                        self.syncEntries[i].transcribed = info["transcribed"] as? Bool ?? false
                        self.syncEntries[i].transcriptPath = info["transcript_path"] as? String
                        self.syncEntries[i].transcribedDate = self.transcriptModificationDate(info["transcript_path"] as? String)
                        // Check speaker tagging state from diarized JSON
                        let review = self.speakerReviewState(transcriptPath: info["transcript_path"] as? String)
                        self.syncEntries[i].speakersTagged = review.tagged
                        self.syncEntries[i].speakersAutoMatched = review.autoMatched
                        if !review.people.isEmpty {
                            meetingPeople[self.syncEntries[i].recording.name] = Set(review.people)
                        }
                        // Check if summary exists (O(1) via the prebuilt index)
                        self.syncEntries[i].summaryPath = summaryIndex[(mp3Name as NSString).deletingPathExtension]
                        if self.syncEntries[i].transcribed { matched += 1 }
                    } else {
                        self.syncEntries[i].transcribed = false
                        self.syncEntries[i].transcriptPath = nil
                        self.syncEntries[i].transcribedDate = nil
                        self.syncEntries[i].speakersTagged = false
                        self.syncEntries[i].speakersAutoMatched = false
                        self.syncEntries[i].summaryPath = nil
                    }
                    // Apply the transcription-skip flag from the user's
                    // persisted opt-out list, regardless of transcription state.
                    self.syncEntries[i].transcriptionSkipped =
                        self.skippedTranscriptions.contains(mp3Name)
                }
                self.log("refreshTranscriptionState: matched \(matched) transcribed entries out of \(self.syncEntries.count)")

                // Populate merge-group transcript state from the same
                // lookup. Merge groups don't live in syncEntries, so
                // the loop above never sets their transcribed/tagged
                // flags — without this block, a successful merge-rediarize
                // would leave the merged row showing "no transcript"
                // forever and the "needs tagging" count would never
                // include it. We mirror exactly the per-row logic.
                var mergedTranscribed: Set<String> = []
                var mergedTagged: Set<String> = []
                var mergedAutoMatched: Set<String> = []
                var mergedPaths: [String: String] = [:]
                var mergedDates: [String: Date] = [:]
                for group in self.mergeGroups {
                    let mergedMp3Name = (group.outputPath as NSString).lastPathComponent
                    guard let info = lookup[mergedMp3Name],
                          (info["transcribed"] as? Bool) == true else { continue }
                    mergedTranscribed.insert(mergedMp3Name)
                    let path = info["transcript_path"] as? String
                    if let path = path { mergedPaths[mergedMp3Name] = path }
                    if let d = self.transcriptModificationDate(path) { mergedDates[mergedMp3Name] = d }
                    let review = self.speakerReviewState(transcriptPath: path)
                    if review.tagged { mergedTagged.insert(mergedMp3Name) }
                    if review.autoMatched { mergedAutoMatched.insert(mergedMp3Name) }
                    if !review.people.isEmpty { meetingPeople[mergedMp3Name] = Set(review.people) }
                }
                self.viewModel.mergedFileTranscribed = mergedTranscribed
                self.viewModel.mergedFileTagged = mergedTagged
                self.viewModel.mergedFileAutoMatched = mergedAutoMatched
                self.viewModel.mergedFileTranscriptPaths = mergedPaths
                self.viewModel.mergedFileTranscribedDates = mergedDates
                self.viewModel.meetingPeople = meetingPeople
                if !mergedTranscribed.isEmpty {
                    self.log("refreshTranscriptionState: \(mergedTranscribed.count) merged file(s) transcribed, \(mergedTranscribed.count - mergedTagged.count) need tagging")
                }

                // Auto-summarise: recordings that finished transcribing this
                // session (captured in pendingAutoSummariseNames) now have
                // their transcriptPath populated above — queue them for a
                // typed summary. Scoped to newly-transcribed files only; we
                // deliberately don't sweep the historical backlog (that could
                // fan out dozens of Claude Code runs). Use "Summarise
                // Selected" for backlog.
                if self.syncAutoSummarise, !self.pendingAutoSummariseNames.isEmpty {
                    let due = self.syncEntries.filter {
                        self.pendingAutoSummariseNames.contains($0.recording.outputName)
                            && $0.transcribed
                            && ($0.transcriptPath.map { !$0.isEmpty } ?? false)
                            && $0.summaryPath == nil
                            && !self.viewModel.summarisingNames.contains($0.recording.outputName)
                    }
                    self.pendingAutoSummariseNames.removeAll()
                    if !due.isEmpty {
                        self.log("Auto-summarise: \(due.count) newly-transcribed recording(s)")
                        self.enqueueSummaries(due)
                    }
                }

                self.syncViewModelState()
                self.runLaunchAutoTranscribeIfNeeded()
            }
        }
    }

    /// Fires once per session after the first `refreshTranscriptionState`
    /// completes, so any recording that was already downloaded but never
    /// transcribed gets picked up on launch — no need for the user to
    /// trigger a Download New to kick the backlog filter. Gated on
    /// `syncAutoTranscribe`: this is the opt-in contract the user made
    /// when they ticked that box. Idempotent via
    /// `didRunLaunchAutoTranscribe`, and `enqueueTranscriptions`'
    /// own duplicate-guard handles any overlap with later
    /// download-new triggers on the same session.
    private func runLaunchAutoTranscribeIfNeeded() {
        // Always scan for merge candidates on first refreshTranscriptionState
        // — this is the moment we know transcripts on disk are reflected
        // in syncEntries.transcribed, which the merge-candidates
        // detector relies on. Cheap (~1s) so safe to fire here too.
        if !didRunLaunchAutoTranscribe {
            scanMergeCandidates()
        }
        guard !didRunLaunchAutoTranscribe else { return }
        guard syncAutoTranscribe else {
            // Still flip the flag — if the user toggles auto-transcribe
            // on later in the session, we don't want a stale backlog
            // sweep to suddenly fire. They can hit Transcribe All.
            didRunLaunchAutoTranscribe = true
            return
        }
        guard ensureTranscriptionReady() else { return }
        didRunLaunchAutoTranscribe = true

        // Sweep ALL entries, not visibleEntries — active device / day /
        // status filters must not shrink the launch backlog.
        let untranscribed = syncEntries
            .filter { $0.recording.localExists && !$0.transcribed && !$0.transcriptionSkipped }
            .map(\.recording.outputPath)
        guard !untranscribed.isEmpty else { return }
        log("Auto-transcribe (launch backlog): \(untranscribed.count) untranscribed recording(s) found")
        enqueueTranscriptions(untranscribed)
    }

    // MARK: - Speaker Tagging & Summary Helpers

    /// Tri-state speaker-review outcome for a transcript (see
    /// PLAN-speaker-tagging-loop.md). Read from the `_diarized.json` sidecar's
    /// `speaker_names` + `speaker_meta`:
    ///   - `tagged`      = a multi-speaker meeting with ≥1 user-confirmed speaker
    ///                     (or a single-speaker meeting — nothing to disambiguate).
    ///   - `autoMatched` = the voice library matched ≥1 speaker but none is
    ///                     confirmed yet ("confirm me"). Never true when tagged.
    ///   - neither       = needs tagging (multi-speaker, nothing named/matched) —
    ///                     the only state that feeds the "N need tagging" nag.
    /// Legacy sidecars without `speaker_meta`: a non-generic name is inferred as
    /// an unverified auto-match, so previously auto-tagged meetings surface for
    /// verification rather than silently reading as confirmed.
    private func speakerReviewState(transcriptPath: String?) -> (tagged: Bool, autoMatched: Bool, people: [String]) {
        guard let mdPath = transcriptPath else { return (false, false, []) }
        let mdURL = URL(fileURLWithPath: mdPath)
        let baseName = mdURL.deletingPathExtension().lastPathComponent
        let dirURL = mdURL.deletingLastPathComponent()
        let diarizedPath = dirURL.appendingPathComponent(baseName + "_diarized.json").path

        guard FileManager.default.fileExists(atPath: diarizedPath),
              let data = FileManager.default.contents(atPath: diarizedPath),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let speakerNames = json["speaker_names"] as? [String: String],
              !speakerNames.isEmpty else {
            return (false, false, [])
        }

        let genericPattern = try? NSRegularExpression(pattern: "^Speaker \\d+$")
        func isGeneric(_ name: String) -> Bool {
            let range = NSRange(name.startIndex..., in: name)
            return genericPattern?.firstMatch(in: name, range: range) != nil
        }

        // Named (non-generic) people in this meeting — the people-filter source.
        let people = Array(Set(speakerNames.values.filter { !isGeneric($0) }))

        // Single-speaker meetings are never flagged — nothing to disambiguate.
        if speakerNames.count <= 1 { return (true, false, people) }

        let meta = json["speaker_meta"] as? [String: [String: Any]] ?? [:]
        var anyVerified = false
        var anyNamed = false   // a real (non-generic) name — auto or user
        for (id, name) in speakerNames {
            if (meta[id]?["verified"] as? Bool) == true { anyVerified = true }
            if !isGeneric(name) { anyNamed = true }
        }

        if anyVerified { return (true, false, people) }      // ≥1 confirmed → tagged
        if anyNamed { return (false, true, people) }         // matched but unconfirmed → confirm me
        return (false, false, people)                        // nothing → needs tagging
    }

    /// Build a `stem → summary-path` index by listing the Summaries directory
    /// ONCE. Summary files are named "<stem> - <Type> - <Area> - <Title>.md",
    /// so the key is the component before the first " - " (the same prefix the
    /// old per-entry `findSummaryPath` matched on — avoids a merged file's
    /// summary, which contains child stems, falsely matching children).
    /// Replaces ~1700 per-entry directory scans with one listing + O(1) lookups.
    private func buildSummaryIndex() -> [String: String] {
        let summariesDir = (NSHomeDirectory() as NSString).appendingPathComponent("HiDock/Summaries")
        guard let contents = try? FileManager.default.contentsOfDirectory(atPath: summariesDir) else { return [:] }
        var index: [String: String] = [:]
        for name in contents where name.hasSuffix(".md") {
            guard let sep = name.range(of: " - ") else { continue }
            let stem = String(name[..<sep.lowerBound])
            if index[stem] == nil {
                index[stem] = (summariesDir as NSString).appendingPathComponent(name)
            }
        }
        return index
    }

    // MARK: - Logging

    /// Serialises file writes from log(). log() is called from the main
    /// thread, syncExtractorQueue, and transcriptionDispatchQueue — each
    /// call used to open its own FileHandle, interleaving/corrupting
    /// lines and racing the rotation. One serial queue keeps appends and
    /// rotation atomic with respect to each other.
    private let logWriteQueue = DispatchQueue(label: "hidock.log-write")

    private func log(_ message: String) {
        let line = "[\(Date())] \(message)\n"
        NSLog("%@", message)   // immediate, thread-safe
        guard let data = line.data(using: .utf8) else { return }

        let logPath = self.logPath
        logWriteQueue.async {
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
        } catch {
            log("Build process failed to launch: \(error)")
            return false
        }

        // Drain stderr BEFORE waitUntilExit — swiftc can emit >64 KB of
        // errors, and read-after-wait deadlocks on a full pipe buffer
        // (see refreshMeetingExtraStats for the canonical ordering).
        let errData = errPipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()

        if p.terminationStatus != 0 {
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
