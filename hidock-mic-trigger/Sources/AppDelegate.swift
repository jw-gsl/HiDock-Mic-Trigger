import AppKit
import SwiftUI
import CoreAudio
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
    let viewModel = HiDockViewModel()

    private var syncOutputFolder: String?
    private var syncTranscriptFolder: String?
    private var syncEntries: [HiDockSyncRecordingEntry] = []
    private var syncCheckedRecordings: Set<String> = []
    private var syncHideDownloaded = false
    private var syncAutoDownload = false
    private var syncSortKey: String = "created"
    private var syncSortAscending: Bool = false
    private var syncFilterDeviceProductId: Int? = nil
    private var syncDeviceConnected: [Int: Bool] = [:]
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
    private let transcriptionQueue = DispatchQueue(label: "hidock.transcription", qos: .background)
    private var transcriptionBusy = false
    private var transcriptionCurrentFile: String? = nil
    private var transcriptionProgress: Int = 0
    private var transcriptionFileIndex: Int = 0
    private var transcriptionFileCount: Int = 0

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

    /// Repo root resolved from UserDefaults, falling back to the default home directory path.
    private var repoRoot: String {
        if let saved = UserDefaults.standard.string(forKey: repoRootKey), !saved.isEmpty {
            return saved
        }
        return "\(NSHomeDirectory())/_git/hidock-tools"
    }

    private var extractorRoot: String {
        "\(repoRoot)/usb-extractor"
    }

    private var extractorScriptPath: String {
        "\(extractorRoot)/extractor.py"
    }

    private var extractorPythonPath: String {
        "\(extractorRoot)/.venv/bin/python"
    }

    private var transcriptionRoot: String { "\(repoRoot)/transcription-pipeline" }
    private var transcriptionPythonPath: String { "\(transcriptionRoot)/.venv/bin/python" }
    private var transcriptionScriptPath: String { "\(transcriptionRoot)/transcribe.py" }

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
            return (try? JSONDecoder().decode([HiDockPairedDevice].self, from: data)) ?? []
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
        showSyncWindow()
        if autoStartOnLaunch {
            startTrigger()
        }
        autoConnectSyncIfPaired()
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
        if response.actionIdentifier == Self.micSwitchActionID {
            DispatchQueue.main.async { [weak self] in
                self?.showSyncWindow()
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
        viewModel.onFilterByDevice = { [weak self] pid in self?.filterSyncByDevice(pid) }
        viewModel.onToggleChecked = { [weak self] name in self?.toggleSyncRecordingCheckbox(name) }
        viewModel.onToggleHideDownloaded = { [weak self] in self?.toggleHideDownloaded() }
        viewModel.onToggleAutoDownload = { [weak self] in self?.toggleAutoDownload() }
        viewModel.onTranscribeSelected = { [weak self] in self?.transcribeSelectedRecordings() }
        viewModel.onTranscribeAll = { [weak self] in self?.transcribeAllRecordings() }
        viewModel.onRevealRecording = { path in
            NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
        }
        viewModel.onRevealTranscript = { path in
            NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
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
        viewModel.syncFilterDeviceProductId = syncFilterDeviceProductId
        viewModel.syncPairedDevices = syncPairedDevices
        viewModel.syncPaired = syncPaired
        viewModel.syncDeviceConnected = syncDeviceConnected
        viewModel.syncOutputFolder = syncOutputFolder
        viewModel.syncTranscriptFolder = syncTranscriptFolder
        viewModel.transcriptionBusy = transcriptionBusy
        viewModel.transcriptionCurrentFile = transcriptionCurrentFile
        viewModel.transcriptionProgress = transcriptionProgress
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
        postNotification(title: title, body: body)
    }

    // MARK: - CLI output monitoring

    private func handleCLIOutput(_ text: String) {
        for line in text.components(separatedBy: .newlines) {
            if line.contains("IN USE") && line.contains("holding HiDock") {
                log("Trigger: USB mic in use, HiDock recording started")
                postNotification(title: "HiDock Recording Started", body: "USB mic is in use — HiDock input held open.")
            } else if line.contains("NOT IN USE") && line.contains("releasing HiDock") {
                log("Trigger: USB mic idle, HiDock recording stopped")
                postNotification(title: "HiDock Recording Stopped", body: "USB mic went idle — HiDock input released.")
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

    private func registerNotificationCategories() {
        let openAction = UNNotificationAction(identifier: Self.micSwitchActionID, title: "Open Mic Settings", options: .foreground)
        let category = UNNotificationCategory(identifier: Self.micSwitchCategoryID, actions: [openAction], intentIdentifiers: [])
        UNUserNotificationCenter.current().setNotificationCategories([category])
    }

    private func postMicChangeNotification(title: String, body: String, micName: String) {
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
        let pairedDevices = syncPairedDevices
        if !pairedDevices.isEmpty {
            let deviceParts = pairedDevices.map { device -> String in
                let connected = syncDeviceConnected[device.productId] ?? false
                return "\(device.shortName) \(connected ? "✓" : "⚠")"
            }
            initialTitle += " · \(deviceParts.joined(separator: " · "))"
        }
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
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(NSMenuItem(title: "Quit HiDock Mic Trigger", action: #selector(quitApp), keyEquivalent: "q"))
        appMenuItem.submenu = appMenu

        NSApp.mainMenu = mainMenu
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
        let pairedDevices = syncPairedDevices
        if !pairedDevices.isEmpty {
            let deviceParts = pairedDevices.map { device -> String in
                let connected = syncDeviceConnected[device.productId] ?? false
                return "\(device.shortName) \(connected ? "✓" : "⚠")"
            }
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

    private func autoConnectSyncIfPaired() {
        guard !syncBusy else {
            log("autoConnectSyncIfPaired: skipping, already busy")
            return
        }
        guard ensureExtractorReady() else {
            log("autoConnectSyncIfPaired: extractor not ready, aborting")
            return
        }
        log("autoConnectSyncIfPaired: running list-devices")

        runExtractor(arguments: ["list-devices"]) { [weak self] result in
            guard let self = self else { return }
            if case .success(let data) = result,
               let response = try? JSONDecoder().decode(HiDockDeviceListResponse.self, from: data) {
                let alreadyPaired = Set(self.syncPairedDevices.map(\.productId))
                for device in response.devices where !alreadyPaired.contains(device.productId) {
                    let pairedDevice = HiDockPairedDevice(productId: device.productId, displayName: device.displayName)
                    var devices = self.syncPairedDevices
                    devices.append(pairedDevice)
                    self.syncPairedDevices = devices
                    self.log("Auto-paired \(device.displayName) (product ID: \(device.productId))")
                }
            }

            let devices = self.syncPairedDevices
            guard !devices.isEmpty else { return }
            self.log("Auto-connecting \(devices.count) paired HiDock device(s) on startup")
            self.syncBusy = true
            self.syncViewModelState()

            let group = DispatchGroup()
            var anyConnected = false
            var lastError: String?

            for device in devices {
                group.enter()
                self.runExtractor(arguments: ["status", "--timeout-ms", "2000"], productId: device.productId) { [weak self] result in
                    guard let self = self else { group.leave(); return }
                    switch result {
                    case .success(let data):
                        if let status = try? JSONDecoder().decode(HiDockSyncStatusResponse.self, from: data) {
                            self.renderSyncStatus(status, deviceProductId: device.productId, deviceName: device.cleanName)
                            if status.connected {
                                anyConnected = true
                                self.log("Auto-connect: \(device.cleanName) connected (\(status.recordings.count) recordings)")
                            } else {
                                let err = status.error ?? "unknown"
                                self.log("Auto-connect: \(device.cleanName) not connected: \(err)")
                                lastError = err
                            }
                        } else {
                            let preview = String(data: data.prefix(200), encoding: .utf8) ?? "(binary)"
                            self.log("Auto-connect: \(device.cleanName) decode failed: \(preview)")
                            lastError = "Failed to decode status response"
                        }
                    case .failure(let error):
                        let desc = error.localizedDescription
                        let shortDesc = desc.components(separatedBy: "\n").last(where: { !$0.isEmpty }) ?? desc
                        self.log("Auto-connect: \(device.cleanName) failed: \(shortDesc)")
                        lastError = shortDesc
                    }
                    group.leave()
                }
            }

            group.notify(queue: .main) { [weak self] in
                guard let self = self else { return }
                self.syncBusy = false
                if !anyConnected, let err = lastError {
                    let message = syncErrorDescription(err)
                    self.viewModel.syncStatus = message
                    self.viewModel.syncStatusLevel = .warning
                }
                self.updateMenuSyncStatus(connected: anyConnected)
                self.syncViewModelState()
            }
        }
    }

    private func updateMenuSyncStatus(connected: Bool) {
        if !syncPaired {
            syncWindowItem?.title = "Show Window..."
            return
        }
        let devices = syncPairedDevices
        var parts: [String] = []
        for device in devices {
            let isConnected = syncDeviceConnected[device.productId] ?? connected
            parts.append("\(device.shortName) \(isConnected ? "✓" : "⚠")")
        }
        syncWindowItem?.title = "Sync: \(parts.joined(separator: " · "))"
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

    @objc private func showAbout() {
        let alert = NSAlert()
        alert.alertStyle = .informational
        alert.messageText = "HiDock Mic Trigger"
        alert.informativeText = "Menu bar app for controlling the HiDock mic trigger CLI.\nVersion 1.0.0"
        alert.runModal()
    }

    @objc private func showSyncWindow() {
        if syncWindow == nil {
            let rect = NSRect(x: 0, y: 0, width: 1120, height: 590)
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
        refreshSyncStatus()
    }

    // MARK: - Extractor

    private func extractorArguments(_ arguments: [String], productId: Int? = nil) -> [String] {
        if let pid = productId {
            return ["--product-id", "\(pid)"] + arguments
        }
        return arguments
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

    private let extractorProcessTimeout: TimeInterval = 30

    private func runExtractor(arguments: [String], productId: Int? = nil, completion: @escaping (Result<Data, Error>) -> Void) {
        let fullArgs = extractorArguments(arguments, productId: productId)
        log("runExtractor: \(fullArgs.joined(separator: " "))")
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
                if process.isRunning {
                    NSLog("runExtractor: killing hung process (pid %d) after %ds", process.processIdentifier, Int(self.extractorProcessTimeout))
                    process.terminate()
                    Thread.sleep(forTimeInterval: 1)
                    if process.isRunning { process.interrupt() }
                    process.waitUntilExit()
                } else {
                    NSLog("runExtractor: process exited with status %d", process.terminationStatus)
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
                } else if process.terminationReason == .uncaughtSignal {
                    let error = NSError(domain: "HiDockSync", code: -1, userInfo: [
                        NSLocalizedDescriptionKey: "Device query timed out — device may be busy recording"
                    ])
                    DispatchQueue.main.async { completion(.failure(error)) }
                } else {
                    let message = String(data: errData.isEmpty ? outData : errData, encoding: .utf8) ?? "Extractor failed"
                    let error = NSError(domain: "HiDockSync", code: Int(process.terminationStatus), userInfo: [
                        NSLocalizedDescriptionKey: message.trimmingCharacters(in: .whitespacesAndNewlines)
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
                errQueue.sync { stderrData.append(chunk) }
                if let text = String(data: chunk, encoding: .utf8) {
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

    private func renderSyncStatus(_ status: HiDockSyncStatusResponse, deviceProductId: Int, deviceName: String) {
        syncOutputFolder = status.outputDir
        UserDefaults.standard.set(status.outputDir, forKey: syncOutputFolderKey)
        syncDeviceConnected[deviceProductId] = status.connected
        syncEntries.removeAll { $0.deviceProductId == deviceProductId }
        for recording in status.recordings {
            syncEntries.append(HiDockSyncRecordingEntry(recording: recording, deviceProductId: deviceProductId, deviceName: deviceName))
        }
        let validNames = Set(syncEntries.map(\.recording.name))
        syncCheckedRecordings = syncCheckedRecordings.intersection(validNames)

        if !syncBusy {
            let devices = syncPairedDevices
            var parts: [String] = []
            for device in devices {
                let connected = syncDeviceConnected[device.productId] ?? false
                parts.append("\(device.shortName) \(connected ? "✓" : "⚠")")
            }
            if parts.isEmpty {
                viewModel.syncStatus = "Not connected"
                viewModel.syncStatusLevel = .secondary
            } else {
                viewModel.syncStatus = parts.joined(separator: " · ")
                viewModel.syncStatusLevel = syncDeviceConnected.values.contains(true) ? .success : .warning
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

    private func toggleSyncRecordingCheckbox(_ name: String) {
        if syncCheckedRecordings.contains(name) {
            syncCheckedRecordings.remove(name)
        } else {
            syncCheckedRecordings.insert(name)
        }
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

    private func filterSyncByDevice(_ productId: Int?) {
        syncFilterDeviceProductId = productId
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

        syncBusy = true
        viewModel.syncStatus = "Refreshing..."
        viewModel.syncStatusLevel = .secondary
        startSyncRefreshTimer()
        syncViewModelState()

        let group = DispatchGroup()
        var anyConnected = false
        var lastError: String?

        for device in devices {
            group.enter()
            runExtractor(arguments: ["status", "--timeout-ms", "5000"], productId: device.productId) { [weak self] result in
                guard let self = self else { group.leave(); return }
                switch result {
                case .success(let data):
                    do {
                        let payload = try JSONDecoder().decode(HiDockSyncStatusResponse.self, from: data)
                        self.renderSyncStatus(payload, deviceProductId: device.productId, deviceName: device.cleanName)
                        if payload.connected {
                            anyConnected = true
                        } else if let err = payload.error {
                            self.log("HiDock sync: \(device.cleanName) not connected: \(err)")
                            lastError = err
                        }
                    } catch {
                        self.log("HiDock sync decode failure for \(device.cleanName): \(error.localizedDescription)")
                        lastError = error.localizedDescription
                    }
                case .failure(let error):
                    let desc = error.localizedDescription
                    let shortDesc = desc.components(separatedBy: "\n").last(where: { !$0.isEmpty }) ?? desc
                    self.log("HiDock sync status error for \(device.cleanName): \(shortDesc)")
                    lastError = shortDesc
                }
                group.leave()
            }
        }

        group.notify(queue: .main) { [weak self] in
            guard let self = self else { return }
            self.syncBusy = false
            self.stopSyncRefreshTimer()
            if anyConnected {
                self.viewModel.syncStatus = "Paired and connected (\(devices.count) device\(devices.count == 1 ? "" : "s"))"
                self.viewModel.syncStatusLevel = .success
            } else if let err = lastError {
                let message = syncErrorDescription(err)
                self.viewModel.syncStatus = message
                self.viewModel.syncStatusLevel = .error
            }
            self.updateMenuSyncStatus(connected: anyConnected)
            self.refreshTranscriptionState()
            self.syncViewModelState()
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
                let alreadyPaired = Set(self.syncPairedDevices.map(\.productId))
                let unpaired = devices.filter { !alreadyPaired.contains($0.productId) }
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
            syncPairedDevices.contains(where: { $0.productId == entry.deviceProductId })
        }
        syncCheckedRecordings = syncCheckedRecordings.intersection(Set(syncEntries.map(\.recording.name)))
        if syncPairedDevices.isEmpty {
            viewModel.syncStatus = "Unpaired"
            viewModel.syncStatusLevel = .secondary
        }
        updateMenuSyncStatus(connected: false)
        syncViewModelState()
    }

    private func markSyncRecordingsAsDownloaded() {
        let entries = selectedSyncEntries()
        let notDownloaded = entries.filter { !$0.recording.downloaded }
        guard !notDownloaded.isEmpty else { return }

        let byDevice = Dictionary(grouping: notDownloaded, by: \.deviceProductId)
        let group = DispatchGroup()
        var anyError: String?

        for (productId, deviceEntries) in byDevice {
            let filenames = deviceEntries.map(\.recording.name)
            group.enter()
            runExtractor(arguments: ["mark-downloaded"] + filenames, productId: productId) { result in
                if case .failure(let error) = result {
                    anyError = error.localizedDescription
                }
                group.leave()
            }
        }

        group.notify(queue: .main) { [weak self] in
            guard let self = self else { return }
            if let error = anyError {
                self.showError("Failed to mark recordings:\n\(error)")
            }
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
                    self.postSyncDownloadNotification(title: "✅ HiDock Download Complete", body: body)
                } else {
                    let body = entries.count == 1
                        ? "\(entries[0].recording.outputName) saved successfully."
                        : "\(entries.count) recordings were saved successfully."
                    self.postSyncDownloadNotification(title: "✅ HiDock Download Complete", body: body)
                }
                self.syncCheckedRecordings.subtract(entries.map(\.recording.name))
                self.refreshSyncStatus()
                let paths = downloaded.map { d in
                    entries.first(where: { $0.recording.outputName == d.filename.replacingOccurrences(of: ".hda", with: ".mp3") })?.recording.outputPath
                        ?? "\(self.syncOutputFolder ?? "")/\(d.filename.replacingOccurrences(of: ".hda", with: ".mp3"))"
                }
                if !paths.isEmpty, self.ensureTranscriptionReady() {
                    self.transcriptionBusy = true
                    self.syncViewModelState()
                    self.transcribeSequentially(paths, index: 0)
                }
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

    private func downloadSyncRecordings(
        _ remaining: [HiDockSyncRecordingEntry],
        completed: [HiDockSyncDownloadResult],
        completion: @escaping (Result<[HiDockSyncDownloadResult], Error>) -> Void
    ) {
        guard let current = remaining.first else {
            completion(.success(completed))
            return
        }

        runExtractorWithProgress(arguments: ["download", current.recording.name, "--length", "\(current.recording.length)"], productId: current.deviceProductId, onProgress: { [weak self] received, total, pct in
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
                postSyncDownloadNotification(title: "✅ HiDock Downloads Complete", body: body)
            }
            viewModel.syncStatus = "Downloaded \(totalDownloaded) new recordings"
            viewModel.syncStatusLevel = .success
            refreshSyncStatus()
            if totalDownloaded > 0, ensureTranscriptionReady(), !transcriptionBusy {
                transcriptionBusy = true
                syncViewModelState()
                runTranscription(arguments: ["transcribe-batch"]) { [weak self] result in
                    guard let self = self else { return }
                    self.transcriptionBusy = false
                    if case .success(let data) = result,
                       let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                       let succeeded = json["succeeded"] as? Int, succeeded > 0 {
                        self.postNotification(title: "📝 Transcription Complete", body: "\(succeeded) new recording\(succeeded == 1 ? "" : "s") transcribed.")
                    }
                    self.refreshTranscriptionState()
                    self.syncViewModelState()
                }
            }
            syncViewModelState()
            return
        }

        runExtractorWithProgress(arguments: ["download-new"], productId: device.productId, onProgress: { [weak self] received, total, pct in
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

    private func runTranscription(arguments: [String], timeout: TimeInterval = 600, onProgress: ((Int) -> Void)? = nil, completion: @escaping (Result<Data, Error>) -> Void) {
        log("runTranscription: \(arguments.joined(separator: " "))")
        transcriptionQueue.async {
            let process = Process()
            process.currentDirectoryURL = URL(fileURLWithPath: self.transcriptionRoot)
            process.executableURL = URL(fileURLWithPath: self.transcriptionPythonPath)
            process.arguments = [self.transcriptionScriptPath] + arguments

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
                        if line.hasPrefix("PROGRESS:") {
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
        guard !transcriptionBusy else {
            log("transcribeFile: skipping, transcription already in progress")
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

        runTranscription(arguments: ["transcribe", mp3Path], onProgress: { [weak self] pct in
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
                    self.postNotification(title: "📝 Transcription Complete", body: "\(filename) transcribed in \(Int(duration))s")
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
        let entries = selectedSyncEntries().filter { $0.recording.downloaded && $0.recording.localExists && !$0.transcribed }
        guard !entries.isEmpty else { return }

        if entries.count == 1 {
            transcribeFile(mp3Path: entries[0].recording.outputPath)
        } else {
            guard !transcriptionBusy else { return }
            transcriptionBusy = true
            viewModel.syncStatus = "Transcribing \(entries.count) recordings..."
            viewModel.syncStatusLevel = .secondary
            syncViewModelState()

            let paths = entries.map(\.recording.outputPath)
            transcribeSequentially(paths, index: 0)
        }
    }

    private func transcribeSequentially(_ paths: [String], index: Int) {
        guard index < paths.count else {
            transcriptionBusy = false
            transcriptionCurrentFile = nil
            transcriptionProgress = 0
            let body = "\(paths.count) recordings transcribed."
            postNotification(title: "📝 Transcription Complete", body: body)
            viewModel.syncStatus = "Batch transcription complete"
            viewModel.syncStatusLevel = .success
            refreshTranscriptionState()
            syncViewModelState()
            return
        }

        let filename = (paths[index] as NSString).lastPathComponent
        transcriptionCurrentFile = filename
        transcriptionProgress = 0
        transcriptionFileIndex = index
        transcriptionFileCount = paths.count
        viewModel.syncStatus = "Transcribing \(filename) (\(index + 1)/\(paths.count))..."
        viewModel.syncStatusLevel = .secondary
        syncViewModelState()

        runTranscription(arguments: ["transcribe", paths[index]], onProgress: { [weak self] pct in
            guard let self = self else { return }
            self.transcriptionProgress = pct
            self.viewModel.syncStatus = "Transcribing \(filename) — \(pct)% (\(index + 1)/\(paths.count))"
            self.syncViewModelState()
        }) { [weak self] _ in
            self?.refreshTranscriptionState()
            self?.transcribeSequentially(paths, index: index + 1)
        }
    }

    private func transcribeAllRecordings() {
        guard ensureTranscriptionReady() else { return }
        guard !transcriptionBusy else { return }

        let paths = viewModel.visibleEntries
            .filter { $0.recording.downloaded && $0.recording.localExists && !$0.transcribed }
            .map(\.recording.outputPath)

        guard !paths.isEmpty else {
            viewModel.syncStatus = "All recordings already transcribed"
            viewModel.syncStatusLevel = .success
            return
        }

        transcriptionBusy = true
        syncViewModelState()
        transcribeSequentially(paths, index: 0)
    }

    private func refreshTranscriptionState() {
        guard FileManager.default.fileExists(atPath: transcriptionScriptPath),
              FileManager.default.isExecutableFile(atPath: transcriptionPythonPath) else {
            return
        }

        transcriptionQueue.async {
            let process = Process()
            process.currentDirectoryURL = URL(fileURLWithPath: self.transcriptionRoot)
            process.executableURL = URL(fileURLWithPath: self.transcriptionPythonPath)
            process.arguments = [self.transcriptionScriptPath, "status"]

            let pipe = Pipe()
            process.standardOutput = pipe
            process.standardError = Pipe()

            do {
                try process.run()
                process.waitUntilExit()
            } catch {
                return
            }

            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            guard process.terminationStatus == 0,
                  let lookup = try? JSONSerialization.jsonObject(with: data) as? [String: [String: Any]] else {
                return
            }

            DispatchQueue.main.async {
                for i in self.syncEntries.indices {
                    let mp3Name = self.syncEntries[i].recording.outputName
                    if let info = lookup[mp3Name] {
                        self.syncEntries[i].transcribed = info["transcribed"] as? Bool ?? false
                        self.syncEntries[i].transcriptPath = info["transcript_path"] as? String
                    } else {
                        self.syncEntries[i].transcribed = false
                        self.syncEntries[i].transcriptPath = nil
                    }
                }
                self.syncViewModelState()
            }
        }
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
