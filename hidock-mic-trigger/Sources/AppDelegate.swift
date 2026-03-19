import AppKit
import CoreAudio
import UserNotifications

private struct HiDockSyncRecording: Codable {
    let name: String
    let createDate: String
    let createTime: String
    let length: Int
    let duration: Double
    let version: Int
    let mode: String
    let signature: String
    let outputPath: String
    let outputName: String
    let downloaded: Bool
    let localExists: Bool
    let downloadedAt: String?
    let lastError: String?
    let status: String
    let humanLength: String
}

private struct HiDockSyncStatusResponse: Codable {
    let connected: Bool
    let outputDir: String
    let statePath: String
    let configPath: String
    let recordings: [HiDockSyncRecording]
    let error: String?
}

private struct HiDockSyncDownloadResult: Codable {
    let filename: String
    let written: Int
    let expectedLength: Int
    let outputPath: String
    let downloaded: Bool
}

private struct HiDockSyncDownloadNewResponse: Codable {
    struct Skipped: Codable {
        let filename: String
        let reason: String
    }

    let connected: Bool
    let outputDir: String
    let downloaded: [HiDockSyncDownloadResult]
    let skipped: [Skipped]
    let error: String?
}

private struct HiDockDevice: Codable {
    let vendorId: Int
    let productId: Int
    let productName: String?
    let serialNumber: String?
    let bus: Int?
    let address: Int?

    var displayName: String {
        sanitizeDeviceName(productName ?? "HiDock")
    }

    var shortName: String {
        let name = displayName
        if name.hasPrefix("HiDock ") {
            return String(name.dropFirst("HiDock ".count))
        }
        return name
    }
}

private struct HiDockDeviceListResponse: Codable {
    let devices: [HiDockDevice]
}

private struct HiDockPairedDevice: Codable, Equatable {
    let productId: Int
    let displayName: String

    /// Sanitized name: removes serial in brackets, replaces underscores
    var cleanName: String {
        sanitizeDeviceName(displayName)
    }

    var shortName: String {
        let name = cleanName
        if name.hasPrefix("HiDock ") {
            return String(name.dropFirst("HiDock ".count))
        }
        return name
    }

    static func == (lhs: HiDockPairedDevice, rhs: HiDockPairedDevice) -> Bool {
        lhs.productId == rhs.productId
    }
}

private struct HiDockSyncRecordingEntry {
    let recording: HiDockSyncRecording
    let deviceProductId: Int
    let deviceName: String
    var transcribed: Bool = false
    var transcriptPath: String? = nil
}

// formatRecordingDuration is now in Helpers.swift

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate, NSMenuDelegate, UNUserNotificationCenterDelegate, NSTableViewDataSource, NSTableViewDelegate {
    private var statusItem: NSStatusItem!
    private let menu = NSMenu()
    private var startItem: NSMenuItem!
    private var stopItem: NSMenuItem!
    private var autoStartItem: NSMenuItem!
    private var syncWindowItem: NSMenuItem!
    private var micMenuItem: NSMenuItem!
    private var micSubmenu: NSMenu!
    private var process: Process?

    private var windowStartButton: NSButton?
    private var windowStopButton: NSButton?
    private var windowStatusLabel: NSTextField?
    private var windowUptimeLabel: NSTextField?
    private var windowAutoStartCheckbox: NSButton?
    private var windowMicPopup: NSPopUpButton?
    private var syncWindow: NSWindow?
    private var syncStatusLabel: NSTextField?
    private var syncFolderLabel: NSTextField?
    private var syncTableView: NSTableView?
    private var syncRefreshButton: NSButton?
    private var syncDownloadSelectedButton: NSButton?
    private var syncDownloadNewButton: NSButton?
    private var syncPairButton: NSButton?
    private var syncUnpairButton: NSButton?
    private var syncSummaryLabel: NSTextField?
    private var syncHideDownloadedCheckbox: NSButton?
    private var syncAutoDownloadCheckbox: NSButton?
    private var syncOutputFolder: String?
    private var syncTranscriptFolderLabel: NSTextField?
    private var syncTranscriptFolder: String?
    private var syncEntries: [HiDockSyncRecordingEntry] = []
    private var syncCheckedRecordings: Set<String> = []
    private var syncHideDownloaded = false
    private var syncAutoDownload = false
    private var syncSortKey: String = "created"
    private var syncSortAscending: Bool = false
    private var syncFilterDeviceProductId: Int? = nil
    private var syncDeviceFilterButtons: [NSButton] = []
    private var syncDeviceConnected: [Int: Bool] = [:]
    private var syncLastCheckedRow: Int?
    private var syncBusy = false
    private var syncRefreshStartDate: Date?
    private var syncRefreshTimer: Timer?
    private var syncAutoDownloadTimer: Timer?
    private var syncExtractorProcess: Process?
    private let syncExtractorQueue = DispatchQueue(label: "hidock.extractor", qos: .userInitiated)
    private var syncStopButton: NSButton?
    private var syncDownloadStartDate: Date?
    private var syncDownloadTimer: Timer?
    private var syncDownloadStopping = false
    private var syncDownloading = false

    // Transcription
    private var syncTranscribeSelectedButton: NSButton?
    private var syncTranscribeAllButton: NSButton?
    private let transcriptionQueue = DispatchQueue(label: "hidock.transcription", qos: .background)
    private var transcriptionBusy = false
    private var transcriptionCurrentFile: String? = nil  // outputName of file being transcribed
    private var transcriptionProgress: Int = 0           // 0-100
    private var transcriptionFileIndex: Int = 0          // current index in batch
    private var transcriptionFileCount: Int = 0          // total files in batch

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
                // Refresh sync after a delay to let HiDock finalize the recording
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
        // Debounce: CoreAudio fires multiple rapid events when docking/undocking.
        // Wait 1.5s for devices to settle before checking what changed.
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

        // Refresh HiDock sync status when any device changes (dock connect/disconnect)
        if !appeared.isEmpty || !disappeared.isEmpty {
            if syncPaired && !syncBusy {
                log("USB device change detected, refreshing sync status")
                autoConnectSyncIfPaired()
            }
        }

        let preferred = preferredMicName
        let selected = selectedMicName

        // Case 1: Preferred mic just appeared and isn't already selected
        if let preferred = preferred, !preferred.isEmpty,
           appeared.contains(preferred), selected != preferred {
            log("Preferred mic '\(preferred)' just connected, auto-switching")
            let oldMic = selectedMicName
            selectedMicName = preferred
            refreshWindowMicPopup()
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

        // Case 2: Current mic disappeared — fall back
        if let selected = selected, !selected.isEmpty,
           disappeared.contains(selected) {
            log("Current mic '\(selected)' disconnected")
            let fallback = resolveFallbackMic(from: currentDevices)
            if let fallback = fallback {
                selectedMicName = fallback
                refreshWindowMicPopup()
                updateMenuState()
                if process != nil { restartTrigger() }
                postMicChangeNotification(
                    title: "Mic Disconnected",
                    body: "\(shortenMicName(selected)) was unplugged. Fell back to \(shortenMicName(fallback)).",
                    micName: fallback
                )
            } else {
                selectedMicName = nil
                updateMenuState()
                if process != nil { restartTrigger() }
                postNotification(title: "Mic Disconnected", body: "\(shortenMicName(selected)) was unplugged. No mics available.")
            }
            return
        }
    }

    private func resolveFallbackMic(from devices: Set<String>) -> String? {
        // Use explicit fallback if set and available
        if let fb = fallbackMicName, !fb.isEmpty, devices.contains(fb) {
            return fb
        }
        // Otherwise pick first device with "MacBook" in the name
        if let macbook = devices.first(where: { $0.contains("MacBook") }) {
            return macbook
        }
        // Last resort: first available device
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
        var initialTitle = "HiDock"
        // Don't show devices on initial launch — they'll appear once connection is confirmed
        if let mic = selectedMicName, !mic.isEmpty {
            let isFallback = preferredMicName != nil && !preferredMicName!.isEmpty && mic != preferredMicName
            let suffix = isFallback ? " (fallback)" : ""
            initialTitle += " · \(shortenMicName(mic))\(suffix)"
        } else {
            initialTitle += " · No Mic"
        }
        statusItem.button?.title = initialTitle

        startItem = NSMenuItem(title: "Start", action: #selector(startTrigger), keyEquivalent: "s")
        stopItem = NSMenuItem(title: "Stop", action: #selector(stopTrigger), keyEquivalent: "t")
        autoStartItem = NSMenuItem(title: "Auto-start on launch", action: #selector(toggleAutoStart), keyEquivalent: "")
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

        // --- Device list ---
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

        // --- Actions ---
        micSubmenu.addItem(NSMenuItem.separator())

        // Set / clear default (preferred) mic
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

        // Set / clear fallback mic
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

        // --- Status info ---
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
        let oldMic = selectedMicName
        selectedMicName = micName
        log("Selected trigger mic: \(micName)")
        refreshWindowMicPopup()
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
        var title = "HiDock"
        // Only show connected devices in the menu bar
        let connectedDevices = syncPairedDevices.filter { syncDeviceConnected[$0.productId] == true }
        if !connectedDevices.isEmpty {
            let deviceParts = connectedDevices.map { "\($0.shortName) ✓" }
            title += " · \(deviceParts.joined(separator: " · "))"
        }
        // Mic name on the far right
        if let mic = selectedMicName, !mic.isEmpty {
            let isFallback = preferredMicName != nil && !preferredMicName!.isEmpty && mic != preferredMicName
            let suffix = isFallback ? " (fallback)" : ""
            title += " · \(shortenMicName(mic))\(suffix)"
        } else {
            title += " · No Mic"
        }
        statusItem.button?.title = title
        updateWindowState()
    }

    // shortenMicName is now in Helpers.swift

    private func updateWindowState() {
        let running = (process != nil)
        windowStartButton?.isEnabled = !running
        windowStopButton?.isEnabled = running
        windowAutoStartCheckbox?.state = autoStartOnLaunch ? .on : .off
        if running {
            let pid = process.map { "\($0.processIdentifier)" } ?? ""
            windowStatusLabel?.stringValue = "Running (pid \(pid))"
            windowStatusLabel?.textColor = .systemGreen
            updateUptimeLabel()
            startUptimeTimer()
        } else {
            windowStatusLabel?.stringValue = "Stopped"
            windowStatusLabel?.textColor = .secondaryLabelColor
            windowUptimeLabel?.stringValue = ""
            stopUptimeTimer()
        }
    }

    private func updateUptimeLabel() {
        if let uptime = formatUptime() {
            windowUptimeLabel?.stringValue = "Uptime: \(uptime)"
        } else {
            windowUptimeLabel?.stringValue = ""
        }
    }

    private func updateMenuUptime() {
        guard process != nil, let uptime = formatUptime() else { return }
        startItem.title = "Running · \(uptime)"
        startItem.isEnabled = false
    }

    private func startUptimeTimer() {
        guard uptimeTimer == nil else { return }
        uptimeTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            self?.updateUptimeLabel()
            self?.updateMenuUptime()
        }
    }

    private func stopUptimeTimer() {
        uptimeTimer?.invalidate()
        uptimeTimer = nil
    }

    // MARK: - Process management

    @objc private func startTrigger() {
        guard process == nil else { return }

        if !FileManager.default.isExecutableFile(atPath: binaryPath) {
            log("Binary not found at \(binaryPath), attempting build...")
            startItem.isEnabled = false
            windowStartButton?.isEnabled = false
            buildTriggerBinaryAsync { [weak self] success in
                guard let self = self else { return }
                if success {
                    self.log("Build succeeded, starting trigger")
                    self.launchProcess()
                } else {
                    self.startItem.isEnabled = true
                    self.windowStartButton?.isEnabled = true
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

        // Monitor CLI stdout for trigger events
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

                // Clean up stdout handler
                outPipe.fileHandleForReading.readabilityHandler = nil

                self.process = nil
                self.processStartDate = nil
                self.updateMenuState()

                // Auto-restart on unexpected crash
                if !self.stoppingIntentionally && status != 0 {
                    self.handleCrash(exitStatus: status)
                } else if self.stoppingIntentionally {
                    // Reset crash count on clean stop
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

    @objc private func stopTrigger() {
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

    @objc private func toggleAutoStart() {
        autoStartOnLaunch.toggle()
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

        // First, discover all connected USB HiDock devices and auto-pair any new ones
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

            // Now connect to all paired devices
            let devices = self.syncPairedDevices
            guard !devices.isEmpty else { return }
            self.log("Auto-connecting \(devices.count) paired HiDock device(s) on startup")
            self.syncBusy = true

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
                    self.syncStatusLabel?.stringValue = "Status: \(message)"
                    self.syncStatusLabel?.textColor = .systemOrange
                }
                self.updateMenuSyncStatus(connected: anyConnected)
                self.updateSyncWindowState()
            }
        }
    }

    private func updateMenuSyncStatus(connected: Bool) {
        if !syncPaired {
            syncWindowItem?.title = "Show Window..."
            return
        }
        let connectedDevices = syncPairedDevices.filter { syncDeviceConnected[$0.productId] == true }
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

    @objc private func showAbout() {
        let alert = NSAlert()
        alert.alertStyle = .informational
        alert.messageText = "HiDock Mic Trigger"
        alert.informativeText = "Menu bar app for controlling the HiDock mic trigger CLI.\nVersion 1.0.0"
        alert.runModal()
    }

    @objc private func windowMicChanged(_ sender: NSPopUpButton) {
        let oldMic = selectedMicName
        selectedMicName = sender.titleOfSelectedItem
        log("Selected trigger mic: \(sender.titleOfSelectedItem ?? "none")")
        if process != nil && sender.titleOfSelectedItem != oldMic {
            restartTrigger()
        }
    }

    private func refreshWindowMicPopup() {
        guard let popup = windowMicPopup else { return }
        let devices = getInputDeviceNames()
        popup.removeAllItems()
        popup.addItems(withTitles: devices)
        if let current = selectedMicName, devices.contains(current) {
            popup.selectItem(withTitle: current)
        } else if !devices.isEmpty {
            popup.selectItem(at: 0)
            selectedMicName = popup.titleOfSelectedItem
        }
    }

    private func showWindow() {
        showSyncWindow()
    }

    @objc private func showSyncWindow() {
        if syncWindow == nil {
            let rect = NSRect(x: 0, y: 0, width: 1120, height: 590)
            let style: NSWindow.StyleMask = [.titled, .closable, .miniaturizable, .resizable]
            let win = NSWindow(contentRect: rect, styleMask: style, backing: .buffered, defer: false)
            win.center()
            win.title = "HiDock"
            win.isReleasedWhenClosed = false
            win.delegate = self
            win.minSize = NSSize(width: 980, height: 510)

            let contentView = win.contentView!

            // ── Mic Trigger section (top strip) ──
            let micTriggerLabel = NSTextField(labelWithString: "Mic Trigger")
            micTriggerLabel.font = .systemFont(ofSize: 14, weight: .semibold)
            micTriggerLabel.frame = NSRect(x: 20, y: 560, width: 100, height: 20)
            micTriggerLabel.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(micTriggerLabel)

            let triggerStatus = NSTextField(labelWithString: "Stopped")
            triggerStatus.font = .systemFont(ofSize: 13)
            triggerStatus.textColor = .secondaryLabelColor
            triggerStatus.frame = NSRect(x: 126, y: 560, width: 160, height: 20)
            triggerStatus.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(triggerStatus)
            windowStatusLabel = triggerStatus

            let triggerUptime = NSTextField(labelWithString: "")
            triggerUptime.font = .systemFont(ofSize: 12)
            triggerUptime.textColor = .secondaryLabelColor
            triggerUptime.frame = NSRect(x: 290, y: 560, width: 180, height: 20)
            triggerUptime.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(triggerUptime)
            windowUptimeLabel = triggerUptime

            let startBtn = NSButton(title: "Start", target: self, action: #selector(startTrigger))
            startBtn.bezelStyle = .rounded
            startBtn.controlSize = .small
            startBtn.frame = NSRect(x: 20, y: 534, width: 60, height: 24)
            startBtn.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(startBtn)
            windowStartButton = startBtn

            let stopBtn2 = NSButton(title: "Stop", target: self, action: #selector(stopTrigger))
            stopBtn2.bezelStyle = .rounded
            stopBtn2.controlSize = .small
            stopBtn2.frame = NSRect(x: 88, y: 534, width: 60, height: 24)
            stopBtn2.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(stopBtn2)
            windowStopButton = stopBtn2

            let micLabel = NSTextField(labelWithString: "Trigger Mic:")
            micLabel.font = .systemFont(ofSize: 12)
            micLabel.frame = NSRect(x: 170, y: 536, width: 80, height: 18)
            micLabel.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(micLabel)

            let micPopup = NSPopUpButton(frame: NSRect(x: 252, y: 532, width: 240, height: 26), pullsDown: false)
            micPopup.controlSize = .small
            micPopup.target = self
            micPopup.action = #selector(windowMicChanged(_:))
            micPopup.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(micPopup)
            windowMicPopup = micPopup

            let autoStart = NSButton(checkboxWithTitle: "Auto-start on launch", target: self, action: #selector(toggleAutoStart))
            autoStart.controlSize = .small
            autoStart.font = .systemFont(ofSize: 12)
            autoStart.frame = NSRect(x: 510, y: 536, width: 170, height: 18)
            autoStart.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(autoStart)
            windowAutoStartCheckbox = autoStart

            let micSeparator = NSBox()
            micSeparator.boxType = .separator
            micSeparator.frame = NSRect(x: 20, y: 524, width: 1080, height: 1)
            micSeparator.autoresizingMask = [.width, .minYMargin]
            contentView.addSubview(micSeparator)

            // ── Sync section ──
            let status = NSTextField(labelWithString: "Status: Not loaded")
            status.font = .systemFont(ofSize: 13)
            status.frame = NSRect(x: 20, y: 498, width: 760, height: 18)
            status.autoresizingMask = [.width, .minYMargin]
            contentView.addSubview(status)
            syncStatusLabel = status

            let folder = NSTextField(labelWithString: "Recordings folder: Not set")
            folder.font = .systemFont(ofSize: 12)
            folder.textColor = .secondaryLabelColor
            folder.frame = NSRect(x: 20, y: 478, width: 1080, height: 18)
            folder.autoresizingMask = [.width, .minYMargin]
            contentView.addSubview(folder)
            syncFolderLabel = folder
            if let savedFolder = UserDefaults.standard.string(forKey: syncOutputFolderKey), !savedFolder.isEmpty {
                syncOutputFolder = savedFolder
                syncFolderLabel?.stringValue = "Recordings folder: \(savedFolder)"
            }

            let transcriptFolder = NSTextField(labelWithString: "Transcript folder: ~/HiDock/Raw Transcripts")
            transcriptFolder.font = .systemFont(ofSize: 12)
            transcriptFolder.textColor = .secondaryLabelColor
            transcriptFolder.frame = NSRect(x: 20, y: 460, width: 1080, height: 18)
            transcriptFolder.autoresizingMask = [.width, .minYMargin]
            contentView.addSubview(transcriptFolder)
            syncTranscriptFolderLabel = transcriptFolder
            if let savedTranscriptFolder = UserDefaults.standard.string(forKey: syncTranscriptFolderKey), !savedTranscriptFolder.isEmpty {
                syncTranscriptFolder = savedTranscriptFolder
                syncTranscriptFolderLabel?.stringValue = "Transcript folder: \(savedTranscriptFolder)"
            } else {
                syncTranscriptFolder = "\(NSHomeDirectory())/HiDock/Raw Transcripts"
            }

            let summary = NSTextField(labelWithString: "No recordings loaded")
            summary.font = .systemFont(ofSize: 12)
            summary.textColor = .secondaryLabelColor
            summary.alignment = .right
            summary.frame = NSRect(x: 500, y: 498, width: 600, height: 18)
            summary.autoresizingMask = [.width, .minYMargin]
            contentView.addSubview(summary)
            syncSummaryLabel = summary

            // Row 1 (top toolbar): Pair, Unpair, Recordings Folder, Transcript Folder, Refresh
            let pairBtn = NSButton(title: "Pair Dock", target: self, action: #selector(pairSyncDock))
            pairBtn.bezelStyle = .rounded
            pairBtn.frame = NSRect(x: 20, y: 432, width: 100, height: 28)
            pairBtn.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(pairBtn)
            syncPairButton = pairBtn

            let unpairBtn = NSButton(title: "Unpair", target: self, action: #selector(unpairSyncDock))
            unpairBtn.bezelStyle = .rounded
            unpairBtn.frame = NSRect(x: 128, y: 432, width: 90, height: 28)
            unpairBtn.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(unpairBtn)
            syncUnpairButton = unpairBtn

            let chooseBtn = NSButton(title: "Recordings Folder", target: self, action: #selector(chooseSyncOutputFolder))
            chooseBtn.bezelStyle = .rounded
            chooseBtn.frame = NSRect(x: 226, y: 432, width: 140, height: 28)
            chooseBtn.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(chooseBtn)

            let chooseTranscriptBtn = NSButton(title: "Transcript Folder", target: self, action: #selector(chooseTranscriptOutputFolder))
            chooseTranscriptBtn.bezelStyle = .rounded
            chooseTranscriptBtn.frame = NSRect(x: 374, y: 432, width: 140, height: 28)
            chooseTranscriptBtn.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(chooseTranscriptBtn)

            let refreshBtn = NSButton(title: "Refresh", target: self, action: #selector(refreshSyncStatus))
            refreshBtn.bezelStyle = .rounded
            refreshBtn.frame = NSRect(x: 522, y: 432, width: 90, height: 28)
            refreshBtn.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(refreshBtn)
            syncRefreshButton = refreshBtn

            // Row 2: Download Selected, Download New, Mark Downloaded, Stop, Transcribe Selected, Transcribe All
            let downloadSelectedBtn = NSButton(title: "Download Selected", target: self, action: #selector(downloadSelectedSyncRecording))
            downloadSelectedBtn.bezelStyle = .rounded
            downloadSelectedBtn.frame = NSRect(x: 20, y: 404, width: 140, height: 28)
            downloadSelectedBtn.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(downloadSelectedBtn)
            syncDownloadSelectedButton = downloadSelectedBtn

            let downloadNewBtn = NSButton(title: "Download New", target: self, action: #selector(downloadNewSyncRecordings))
            downloadNewBtn.bezelStyle = .rounded
            downloadNewBtn.frame = NSRect(x: 168, y: 404, width: 120, height: 28)
            downloadNewBtn.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(downloadNewBtn)
            syncDownloadNewButton = downloadNewBtn

            let markBtn = NSButton(title: "Mark Downloaded", target: self, action: #selector(markSyncRecordingsAsDownloaded))
            markBtn.bezelStyle = .rounded
            markBtn.frame = NSRect(x: 296, y: 404, width: 140, height: 28)
            markBtn.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(markBtn)

            let stopBtn = NSButton(title: "Stop Download", target: self, action: #selector(stopSyncDownload))
            stopBtn.bezelStyle = .rounded
            stopBtn.frame = NSRect(x: 20, y: 404, width: 416, height: 28)
            stopBtn.autoresizingMask = [.maxXMargin, .minYMargin]
            stopBtn.contentTintColor = .systemRed
            stopBtn.isHidden = true
            contentView.addSubview(stopBtn)
            syncStopButton = stopBtn

            let transcribeSelectedBtn = NSButton(title: "Transcribe Selected", target: self, action: #selector(transcribeSelectedRecordings))
            transcribeSelectedBtn.bezelStyle = .rounded
            transcribeSelectedBtn.frame = NSRect(x: 444, y: 404, width: 150, height: 28)
            transcribeSelectedBtn.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(transcribeSelectedBtn)
            syncTranscribeSelectedButton = transcribeSelectedBtn

            let transcribeAllBtn = NSButton(title: "Transcribe All", target: self, action: #selector(transcribeAllRecordings))
            transcribeAllBtn.bezelStyle = .rounded
            transcribeAllBtn.frame = NSRect(x: 602, y: 404, width: 120, height: 28)
            transcribeAllBtn.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(transcribeAllBtn)
            syncTranscribeAllButton = transcribeAllBtn

            // Row 3: Select buttons, filter, checkboxes
            let selectAllBtn = NSButton(title: "Select All", target: self, action: #selector(selectAllSyncRecordings))
            selectAllBtn.bezelStyle = .rounded
            selectAllBtn.controlSize = .small
            selectAllBtn.font = .systemFont(ofSize: 11)
            selectAllBtn.frame = NSRect(x: 20, y: 376, width: 80, height: 22)
            selectAllBtn.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(selectAllBtn)

            let selectNoneBtn = NSButton(title: "Select None", target: self, action: #selector(selectNoneSyncRecordings))
            selectNoneBtn.bezelStyle = .rounded
            selectNoneBtn.controlSize = .small
            selectNoneBtn.font = .systemFont(ofSize: 11)
            selectNoneBtn.frame = NSRect(x: 108, y: 376, width: 86, height: 22)
            selectNoneBtn.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(selectNoneBtn)

            let selectNewBtn = NSButton(title: "Select Not Downloaded", target: self, action: #selector(selectNotDownloadedSyncRecordings))
            selectNewBtn.bezelStyle = .rounded
            selectNewBtn.controlSize = .small
            selectNewBtn.font = .systemFont(ofSize: 11)
            selectNewBtn.frame = NSRect(x: 202, y: 376, width: 150, height: 22)
            selectNewBtn.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(selectNewBtn)

            // Device filter buttons
            let filterLabel = NSTextField(labelWithString: "Filter:")
            filterLabel.font = .systemFont(ofSize: 11, weight: .medium)
            filterLabel.frame = NSRect(x: 380, y: 376, width: 40, height: 22)
            filterLabel.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(filterLabel)

            // Checkboxes on the right side of Row 3
            let hideDownloadedCheckbox = NSButton(checkboxWithTitle: "Hide Downloaded", target: self, action: #selector(toggleHideDownloaded(_:)))
            hideDownloadedCheckbox.frame = NSRect(x: 820, y: 378, width: 150, height: 22)
            hideDownloadedCheckbox.state = UserDefaults.standard.bool(forKey: syncHideDownloadedKey) ? .on : .off
            hideDownloadedCheckbox.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(hideDownloadedCheckbox)
            syncHideDownloadedCheckbox = hideDownloadedCheckbox
            syncHideDownloaded = hideDownloadedCheckbox.state == .on

            let autoDownloadCheckbox = NSButton(checkboxWithTitle: "Auto-download New", target: self, action: #selector(toggleAutoDownload(_:)))
            autoDownloadCheckbox.frame = NSRect(x: 970, y: 378, width: 170, height: 22)
            autoDownloadCheckbox.state = UserDefaults.standard.bool(forKey: syncAutoDownloadKey) ? .on : .off
            autoDownloadCheckbox.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(autoDownloadCheckbox)
            syncAutoDownloadCheckbox = autoDownloadCheckbox
            syncAutoDownload = autoDownloadCheckbox.state == .on

            let allDevicesBtn = NSButton(title: "All", target: self, action: #selector(filterSyncByDevice(_:)))
            allDevicesBtn.bezelStyle = .rounded
            allDevicesBtn.controlSize = .small
            allDevicesBtn.font = .systemFont(ofSize: 11, weight: .semibold)
            allDevicesBtn.tag = 0
            allDevicesBtn.frame = NSRect(x: 422, y: 376, width: 40, height: 22)
            allDevicesBtn.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(allDevicesBtn)
            syncDeviceFilterButtons = [allDevicesBtn]

            var filterX: CGFloat = 470
            for device in syncPairedDevices {
                let btn = NSButton(title: device.shortName, target: self, action: #selector(filterSyncByDevice(_:)))
                btn.bezelStyle = .rounded
                btn.controlSize = .small
                btn.font = .systemFont(ofSize: 11)
                btn.tag = device.productId
                let btnWidth = max(CGFloat(device.shortName.count) * 8 + 16, 50)
                btn.frame = NSRect(x: filterX, y: 376, width: btnWidth, height: 22)
                btn.autoresizingMask = [.maxXMargin, .minYMargin]
                contentView.addSubview(btn)
                syncDeviceFilterButtons.append(btn)
                filterX += btnWidth + 8
            }

            let scrollView = NSScrollView(frame: NSRect(x: 20, y: 20, width: 1080, height: 346))
            scrollView.hasVerticalScroller = true
            scrollView.hasHorizontalScroller = true
            scrollView.borderType = .bezelBorder
            scrollView.autoresizingMask = [.width, .height]

            let tableView = NSTableView(frame: scrollView.bounds)
            tableView.delegate = self
            tableView.dataSource = self
            tableView.usesAlternatingRowBackgroundColors = true
            tableView.allowsEmptySelection = true
            tableView.rowSizeStyle = .medium
            tableView.columnAutoresizingStyle = .noColumnAutoresizing

            let columns: [(String, String, CGFloat)] = [
                ("selected", "", 36),
                ("device", "Device", 130),
                ("status", "Status", 110),
                ("transcribed", "Transcribed", 90),
                ("name", "Recording", 250),
                ("created", "Created", 170),
                ("duration", "Length", 90),
                ("size", "Size", 90),
                ("path", "Output", 300),
                ("reveal", "MP3", 60),
            ]
            var totalColumnWidth: CGFloat = 0
            for (identifier, title, width) in columns {
                let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier(rawValue: identifier))
                column.title = title
                column.width = width
                if identifier != "selected" {
                    column.sortDescriptorPrototype = NSSortDescriptor(key: identifier, ascending: true)
                }
                tableView.addTableColumn(column)
                totalColumnWidth += width
            }
            tableView.frame = NSRect(x: 0, y: 0, width: totalColumnWidth, height: scrollView.bounds.height)

            let tableMenu = NSMenu()
            tableMenu.addItem(NSMenuItem(title: "Mark as Downloaded", action: #selector(markSyncRecordingsAsDownloaded), keyEquivalent: ""))
            tableMenu.addItem(NSMenuItem(title: "Show in Finder", action: #selector(revealSelectedSyncRecordingInFinder), keyEquivalent: ""))
            tableView.menu = tableMenu

            scrollView.documentView = tableView
            contentView.addSubview(scrollView)
            syncTableView = tableView

            syncWindow = win
            updateWindowState()
            updateSyncWindowState()
        }
        refreshWindowMicPopup()
        NSApp.setActivationPolicy(.regular)
        syncWindow?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        refreshSyncStatus()
    }

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

    /// Maximum time (seconds) an extractor process may run before being killed.
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

                // Wait with a timeout — kill the process if it hangs
                let deadline = Date().addingTimeInterval(self.extractorProcessTimeout)
                while process.isRunning && Date() < deadline {
                    Thread.sleep(forTimeInterval: 0.2)
                }
                if process.isRunning {
                    NSLog("runExtractor: killing hung process (pid %d) after %ds", process.processIdentifier, Int(self.extractorProcessTimeout))
                    process.terminate()
                    // Give it a moment to die, then force kill
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
                    DispatchQueue.main.async {
                        completion(.success(outData))
                    }
                } else if process.terminationReason == .uncaughtSignal {
                    let error = NSError(domain: "HiDockSync", code: -1, userInfo: [
                        NSLocalizedDescriptionKey: "Device query timed out — device may be busy recording"
                    ])
                    DispatchQueue.main.async {
                        completion(.failure(error))
                    }
                } else {
                    let message = String(data: errData.isEmpty ? outData : errData, encoding: .utf8) ?? "Extractor failed"
                    let error = NSError(domain: "HiDockSync", code: Int(process.terminationStatus), userInfo: [
                        NSLocalizedDescriptionKey: message.trimmingCharacters(in: .whitespacesAndNewlines)
                    ])
                    DispatchQueue.main.async {
                        completion(.failure(error))
                    }
                }
            } catch {
                DispatchQueue.main.async {
                    completion(.failure(error))
                }
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

            // Collect stdout in background to avoid pipe buffer deadlock
            var outData = Data()
            let outQueue = DispatchQueue(label: "hidock.stdout")
            outPipe.fileHandleForReading.readabilityHandler = { handle in
                let chunk = handle.availableData
                if !chunk.isEmpty {
                    outQueue.sync { outData.append(chunk) }
                }
            }

            // Read stderr in real-time for PROGRESS lines
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
                // Allow handlers to drain
                outPipe.fileHandleForReading.readabilityHandler = nil
                errPipe.fileHandleForReading.readabilityHandler = nil
                // Read any remaining data
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

    private func updateSyncWindowState() {
        let selectedRow = syncTableView?.selectedRow ?? -1
        let entries = visibleSyncEntries
        let hasSelection = !syncCheckedRecordings.isEmpty || (selectedRow >= 0 && selectedRow < entries.count)

        syncRefreshButton?.isEnabled = !syncBusy
        syncDownloadNewButton?.isEnabled = !syncBusy && syncPaired
        syncDownloadNewButton?.isHidden = syncDownloading
        syncDownloadSelectedButton?.isEnabled = !syncBusy && syncPaired && hasSelection
        syncDownloadSelectedButton?.isHidden = syncDownloading
        syncStopButton?.isHidden = !syncDownloading
        syncPairButton?.isEnabled = !syncBusy
        syncUnpairButton?.isEnabled = !syncBusy && syncPaired
        syncTranscribeSelectedButton?.isEnabled = !transcriptionBusy && hasSelection
        syncTranscribeAllButton?.isEnabled = !transcriptionBusy
    }

    private func renderSyncStatus(_ status: HiDockSyncStatusResponse, deviceProductId: Int, deviceName: String) {
        syncOutputFolder = status.outputDir
        UserDefaults.standard.set(status.outputDir, forKey: syncOutputFolderKey)
        syncDeviceConnected[deviceProductId] = status.connected
        // Remove old entries for this device, then add new ones
        syncEntries.removeAll { $0.deviceProductId == deviceProductId }
        for recording in status.recordings {
            syncEntries.append(HiDockSyncRecordingEntry(recording: recording, deviceProductId: deviceProductId, deviceName: deviceName))
        }
        let validNames = Set(syncEntries.map(\.recording.name))
        syncCheckedRecordings = syncCheckedRecordings.intersection(validNames)
        syncTableView?.reloadData()

        syncFolderLabel?.stringValue = "Output folder: \(status.outputDir)"
        updateSyncSummary()
        // Only update status label and menu if we're not mid-refresh (group.notify handles that)
        if !syncBusy {
            let connectedNames = syncPairedDevices
                .filter { syncDeviceConnected[$0.productId] == true }
                .map { "\(hidockDeviceEmoji($0.shortName)) \($0.shortName)" }
            if connectedNames.isEmpty {
                syncStatusLabel?.stringValue = "Status: Not connected"
                syncStatusLabel?.textColor = .secondaryLabelColor
            } else {
                syncStatusLabel?.stringValue = "Status: Connected — \(connectedNames.joined(separator: " · "))"
                syncStatusLabel?.textColor = .systemGreen
            }
            updateSyncWindowState()
            updateMenuSyncStatus(connected: status.connected)
        }
    }

    // syncErrorDescription is now a free function in Helpers.swift

    private func startSyncRefreshTimer() {
        syncRefreshStartDate = Date()
        syncRefreshTimer?.invalidate()
        syncRefreshTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            guard let self = self, let start = self.syncRefreshStartDate else { return }
            let elapsed = Int(Date().timeIntervalSince(start))
            self.syncStatusLabel?.stringValue = "Status: Refreshing... \(elapsed)s"
        }
    }

    private func stopSyncRefreshTimer() {
        syncRefreshTimer?.invalidate()
        syncRefreshTimer = nil
        syncRefreshStartDate = nil
    }

    private var visibleSyncEntries: [HiDockSyncRecordingEntry] {
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
                let aStatus = ar.downloaded ? "Downloaded" : (ar.lastError == nil ? "On device" : "Failed")
                let bStatus = br.downloaded ? "Downloaded" : (br.lastError == nil ? "On device" : "Failed")
                result = aStatus.localizedCaseInsensitiveCompare(bStatus) == .orderedAscending
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

    private func selectedSyncEntry() -> HiDockSyncRecordingEntry? {
        let row = syncTableView?.selectedRow ?? -1
        let entries = visibleSyncEntries
        guard row >= 0, row < entries.count else { return nil }
        return entries[row]
    }

    private func selectedSyncEntries() -> [HiDockSyncRecordingEntry] {
        let entries = visibleSyncEntries
        if !syncCheckedRecordings.isEmpty {
            return entries.filter { syncCheckedRecordings.contains($0.recording.name) }
        }
        if let single = selectedSyncEntry() {
            return [single]
        }
        return []
    }

    @objc private func toggleHideDownloaded(_ sender: NSButton) {
        syncHideDownloaded = sender.state == .on
        UserDefaults.standard.set(syncHideDownloaded, forKey: syncHideDownloadedKey)
        syncTableView?.reloadData()
        updateSyncSummary()
        updateSyncWindowState()
    }

    @objc private func toggleAutoDownload(_ sender: NSButton) {
        syncAutoDownload = sender.state == .on
        UserDefaults.standard.set(syncAutoDownload, forKey: syncAutoDownloadKey)
    }

    @objc private func toggleSyncRecordingCheckbox(_ sender: NSButton) {
        let entries = visibleSyncEntries
        let clickedRow = sender.tag
        guard clickedRow >= 0, clickedRow < entries.count else { return }

        let event = NSApp.currentEvent
        let shiftHeld = event?.modifierFlags.contains(.shift) == true
        let cmdHeld = event?.modifierFlags.contains(.command) == true

        if shiftHeld, let lastRow = syncLastCheckedRow {
            // Shift-click: toggle range between last clicked and current
            let rangeStart = min(lastRow, clickedRow)
            let rangeEnd = max(lastRow, clickedRow)
            let newState = sender.state == .on
            for i in rangeStart...rangeEnd where i < entries.count {
                if newState {
                    syncCheckedRecordings.insert(entries[i].recording.name)
                } else {
                    syncCheckedRecordings.remove(entries[i].recording.name)
                }
            }
            syncTableView?.reloadData()
        } else {
            // Normal or Cmd-click: toggle single checkbox
            let entry = entries[clickedRow]
            if sender.state == .on {
                syncCheckedRecordings.insert(entry.recording.name)
            } else {
                syncCheckedRecordings.remove(entry.recording.name)
            }
        }

        syncLastCheckedRow = clickedRow
        updateSyncWindowState()
        updateSyncSummary()
    }

    @objc private func selectAllSyncRecordings() {
        for entry in visibleSyncEntries {
            syncCheckedRecordings.insert(entry.recording.name)
        }
        syncTableView?.reloadData()
        updateSyncWindowState()
        updateSyncSummary()
    }

    @objc private func selectNoneSyncRecordings() {
        syncCheckedRecordings.removeAll()
        syncTableView?.reloadData()
        updateSyncWindowState()
        updateSyncSummary()
    }

    @objc private func selectNotDownloadedSyncRecordings() {
        syncCheckedRecordings.removeAll()
        for entry in visibleSyncEntries where !entry.recording.downloaded {
            syncCheckedRecordings.insert(entry.recording.name)
        }
        syncTableView?.reloadData()
        updateSyncWindowState()
        updateSyncSummary()
    }

    @objc private func filterSyncByDevice(_ sender: NSButton) {
        let productId = sender.tag
        syncFilterDeviceProductId = productId == 0 ? nil : productId
        // Update button styling
        for btn in syncDeviceFilterButtons {
            let isActive = btn.tag == productId
            btn.font = .systemFont(ofSize: 11, weight: isActive ? .semibold : .regular)
        }
        syncTableView?.reloadData()
        updateSyncWindowState()
        updateSyncSummary()
    }

    private func updateSyncSummary() {
        let visible = visibleSyncEntries
        let downloadedCount = syncEntries.filter(\.recording.downloaded).count
        let selectedCount = syncCheckedRecordings.count
        var parts = ["\(visible.count) shown", "\(syncEntries.count) total", "\(downloadedCount) downloaded"]
        if selectedCount > 0 {
            parts.append("\(selectedCount) selected")
        }
        syncSummaryLabel?.stringValue = parts.joined(separator: " · ")

    }

    private func scheduleAutoDownloadNewRecordings() {
        guard syncAutoDownload, syncPaired, !syncBusy else { return }
        syncAutoDownloadTimer?.invalidate()
        syncAutoDownloadTimer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: false) { [weak self] _ in
            guard let self = self, self.syncAutoDownload, self.syncPaired, !self.syncBusy else { return }
            self.downloadNewSyncRecordings()
        }
    }

    @objc private func refreshSyncStatus() {
        guard !syncBusy else {
            log("refreshSyncStatus: skipping, already busy")
            return
        }
        guard ensureExtractorReady() else { return }
        let devices = syncPairedDevices
        guard !devices.isEmpty else {
            syncStatusLabel?.stringValue = "Status: Not paired"
            syncStatusLabel?.textColor = .secondaryLabelColor
            updateSyncWindowState()
            return
        }

        syncBusy = true
        syncStatusLabel?.stringValue = "Status: Refreshing..."
        syncStatusLabel?.textColor = .secondaryLabelColor
        startSyncRefreshTimer()
        updateSyncWindowState()

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
                let connectedNames = self.syncPairedDevices
                    .filter { self.syncDeviceConnected[$0.productId] == true }
                    .map { "\(hidockDeviceEmoji($0.shortName)) \($0.shortName)" }
                let deviceList = connectedNames.joined(separator: " · ")
                self.syncStatusLabel?.stringValue = "Status: Connected — \(deviceList)"
                self.syncStatusLabel?.textColor = .systemGreen
            } else if let err = lastError {
                let message = syncErrorDescription(err)
                self.syncStatusLabel?.stringValue = "Status: \(message)"
                self.syncStatusLabel?.textColor = .systemRed
            }
            self.updateSyncSummary()
            self.updateSyncWindowState()
            self.updateMenuSyncStatus(connected: anyConnected)
            self.refreshTranscriptionState()
        }
    }

    @objc private func chooseSyncOutputFolder() {
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
                    self.syncFolderLabel?.stringValue = "Recordings folder: \(url.path)"
                    UserDefaults.standard.set(url.path, forKey: self.syncOutputFolderKey)
                    self.refreshSyncStatus()
                case .failure(let error):
                    self.log("HiDock sync set-output error: \(error.localizedDescription)")
                    self.showError("Failed to set recordings folder:\n\(error.localizedDescription)")
                }
            }
        }
    }

    @objc private func chooseTranscriptOutputFolder() {
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
            syncTranscriptFolderLabel?.stringValue = "Transcript folder: \(url.path)"
            UserDefaults.standard.set(url.path, forKey: syncTranscriptFolderKey)
        }
    }

    @objc private func pairSyncDock() {
        guard ensureExtractorReady() else { return }
        syncStatusLabel?.stringValue = "Status: Searching for devices..."
        syncStatusLabel?.textColor = .secondaryLabelColor

        runExtractor(arguments: ["list-devices"]) { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .success(let data):
                guard let response = try? JSONDecoder().decode(HiDockDeviceListResponse.self, from: data) else {
                    self.showError("Failed to parse device list")
                    return
                }
                let devices = response.devices
                // Filter out already-paired devices
                let alreadyPaired = Set(self.syncPairedDevices.map(\.productId))
                let unpaired = devices.filter { !alreadyPaired.contains($0.productId) }
                if unpaired.isEmpty && devices.isEmpty {
                    self.syncStatusLabel?.stringValue = "Status: No HiDock devices found"
                    self.syncStatusLabel?.textColor = .systemRed
                    self.showError("No HiDock devices found.\nConnect a HiDock via USB and try again.")
                    return
                }
                if unpaired.isEmpty {
                    self.syncStatusLabel?.stringValue = "Status: All connected devices already paired"
                    self.syncStatusLabel?.textColor = .secondaryLabelColor
                    return
                }
                if unpaired.count == 1, let device = unpaired.first {
                    self.completePairing(device)
                    return
                }
                self.showDevicePicker(unpaired)
            case .failure(let error):
                self.syncStatusLabel?.stringValue = "Status: Device search failed"
                self.syncStatusLabel?.textColor = .systemRed
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
        refreshSyncStatus()
    }

    @objc private func unpairSyncDock() {
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
                // Unpair all
                syncPairedDevices = []
            } else {
                let buttonIndex = response.rawValue - NSApplication.ModalResponse.alertFirstButtonReturn.rawValue
                if buttonIndex >= 1 && buttonIndex <= devices.count {
                    var updated = devices
                    updated.remove(at: buttonIndex - 1)
                    syncPairedDevices = updated
                } else {
                    return // Cancel
                }
            }
        } else {
            syncPairedDevices = []
        }

        syncEntries = syncEntries.filter { entry in
            syncPairedDevices.contains(where: { $0.productId == entry.deviceProductId })
        }
        syncCheckedRecordings = syncCheckedRecordings.intersection(Set(syncEntries.map(\.recording.name)))
        syncTableView?.reloadData()
        if syncPairedDevices.isEmpty {
            syncStatusLabel?.stringValue = "Status: Unpaired"
            syncStatusLabel?.textColor = .secondaryLabelColor
            syncSummaryLabel?.stringValue = "No recordings loaded"
        }
        updateSyncWindowState()

        updateMenuSyncStatus(connected: false)
    }

    @objc private func markSyncRecordingsAsDownloaded() {
        let entries = selectedSyncEntries()
        let notDownloaded = entries.filter { !$0.recording.downloaded }
        guard !notDownloaded.isEmpty else { return }

        // Group by device so product_id is stored correctly
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

    @objc private func revealSelectedSyncRecordingInFinder() {
        let entries = selectedSyncEntries()
        guard let entry = entries.first, entry.recording.downloaded else { return }
        let url = URL(fileURLWithPath: entry.recording.outputPath)
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }

    @objc private func revealSyncRecordingInFinder(_ sender: NSButton) {
        let entries = visibleSyncEntries
        guard sender.tag >= 0, sender.tag < entries.count else { return }
        let url = URL(fileURLWithPath: entries[sender.tag].recording.outputPath)
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }

    @objc private func stopSyncDownload() {
        log("User requested stop download")
        syncDownloadStopping = true
        if let proc = syncExtractorProcess, proc.isRunning {
            proc.terminate()
        }
        syncExtractorProcess = nil
        stopDownloadTimer()
        syncBusy = false
        syncDownloading = false
        syncStatusLabel?.stringValue = "Status: Download stopped"
        syncStatusLabel?.textColor = .systemOrange
        updateSyncWindowState()
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
            self.syncStopButton?.title = "Stop Download (\(timeStr))"
        }
    }

    private func stopDownloadTimer() {
        syncDownloadTimer?.invalidate()
        syncDownloadTimer = nil
        syncDownloadStartDate = nil
        syncStopButton?.title = "Stop Download"
    }

    @objc private func downloadSelectedSyncRecording() {
        guard ensureExtractorReady() else { return }
        let entries = selectedSyncEntries()
        guard !entries.isEmpty else { return }

        syncBusy = true
        syncDownloading = true
        startDownloadTimer()
        if entries.count == 1, let entry = entries.first {
            syncStatusLabel?.stringValue = "Status: Downloading \(entry.recording.outputName)..."
        } else {
            syncStatusLabel?.stringValue = "Status: Downloading \(entries.count) recordings..."
        }
        syncStatusLabel?.textColor = .secondaryLabelColor
        updateSyncWindowState()

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
                // Auto-transcribe downloaded recordings
                let paths = downloaded.map { d in
                    entries.first(where: { $0.recording.outputName == d.filename.replacingOccurrences(of: ".hda", with: ".mp3") })?.recording.outputPath
                        ?? "\(self.syncOutputFolder ?? "")/\(d.filename.replacingOccurrences(of: ".hda", with: ".mp3"))"
                }
                if !paths.isEmpty, self.ensureTranscriptionReady() {
                    self.transcriptionBusy = true
                    self.transcribeSequentially(paths, index: 0)
                }
            case .failure(let error):
                if self.syncDownloadStopping {
                    self.syncDownloadStopping = false
                    return
                }
                self.syncStatusLabel?.stringValue = "Status: Download failed"
                self.syncStatusLabel?.textColor = .systemRed
                let label = entries.count == 1 ? entries[0].recording.name : "\(entries.count) recordings"
                self.showError("Failed to download \(label):\n\(error.localizedDescription)")
                self.updateSyncWindowState()
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
            self.syncStatusLabel?.stringValue = "Status: Downloading \(current.recording.outputName) — \(pct)% (\(receivedMB)/\(totalMB) MB)"
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

    @objc private func downloadNewSyncRecordings() {
        guard ensureExtractorReady() else { return }
        let devices = syncPairedDevices
        guard !devices.isEmpty else { return }

        syncBusy = true
        syncDownloading = true
        startDownloadTimer()
        syncStatusLabel?.stringValue = "Status: Downloading new recordings..."
        syncStatusLabel?.textColor = .secondaryLabelColor
        updateSyncWindowState()

        downloadNewFromDevices(devices, totalDownloaded: 0)
    }

    private func downloadNewFromDevices(_ remaining: [HiDockPairedDevice], totalDownloaded: Int) {
        guard let device = remaining.first else {
            // All devices done
            stopDownloadTimer()
            syncBusy = false
            syncDownloading = false
            if totalDownloaded > 0 {
                let body = totalDownloaded == 1
                    ? "1 new recording was saved successfully."
                    : "\(totalDownloaded) new recordings were saved successfully."
                postSyncDownloadNotification(title: "✅ HiDock Downloads Complete", body: body)
            }
            syncStatusLabel?.stringValue = "Status: Downloaded \(totalDownloaded) new recordings"
            syncStatusLabel?.textColor = .systemGreen
            refreshSyncStatus()
            // Auto-transcribe newly downloaded recordings
            if totalDownloaded > 0, ensureTranscriptionReady(), !transcriptionBusy {
                transcriptionBusy = true
                runTranscription(arguments: ["transcribe-batch"]) { [weak self] result in
                    guard let self = self else { return }
                    self.transcriptionBusy = false
                    if case .success(let data) = result,
                       let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                       let succeeded = json["succeeded"] as? Int, succeeded > 0 {
                        self.postNotification(title: "📝 Transcription Complete", body: "\(succeeded) new recording\(succeeded == 1 ? "" : "s") transcribed.")
                    }
                    self.refreshTranscriptionState()
                }
            }
            return
        }

        runExtractorWithProgress(arguments: ["download-new"], productId: device.productId, onProgress: { [weak self] received, total, pct in
            guard let self = self else { return }
            let receivedMB = String(format: "%.1f", Double(received) / 1_000_000)
            let totalMB = String(format: "%.1f", Double(total) / 1_000_000)
            self.syncStatusLabel?.stringValue = "Status: Downloading (\(device.cleanName)) — \(pct)% (\(receivedMB)/\(totalMB) MB)"
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
                    return
                }
                self.log("download-new failed for \(device.cleanName): \(error.localizedDescription)")
                // Continue to next device even on failure
                self.downloadNewFromDevices(Array(remaining.dropFirst()), totalDownloaded: totalDownloaded)
            }
        }
    }

    func numberOfRows(in tableView: NSTableView) -> Int {
        if tableView == syncTableView {
            return visibleSyncEntries.count
        }
        return 0
    }

    func tableViewSelectionDidChange(_ notification: Notification) {
        updateSyncWindowState()
    }

    func tableView(_ tableView: NSTableView, sortDescriptorsDidChange oldDescriptors: [NSSortDescriptor]) {
        guard tableView == syncTableView, let descriptor = tableView.sortDescriptors.first, let key = descriptor.key else { return }
        syncSortKey = key
        syncSortAscending = descriptor.ascending
        syncTableView?.reloadData()
    }

    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        let entries = visibleSyncEntries
        guard tableView == syncTableView, row >= 0, row < entries.count, let tableColumn = tableColumn else {
            return nil
        }

        let entry = entries[row]
        let recording = entry.recording
        let identifier = tableColumn.identifier
        if identifier.rawValue == "selected" {
            let button = NSButton(checkboxWithTitle: "", target: self, action: #selector(toggleSyncRecordingCheckbox(_:)))
            button.tag = row
            button.state = syncCheckedRecordings.contains(recording.name) ? .on : .off
            return button
        }

        if identifier.rawValue == "reveal" {
            guard recording.downloaded, recording.localExists else {
                return NSView()
            }
            let button = NSButton(title: "MP3", target: self, action: #selector(revealSyncRecordingInFinder(_:)))
            button.bezelStyle = .rounded
            button.controlSize = .small
            button.font = .systemFont(ofSize: 11)
            button.tag = row
            return button
        }

        if identifier.rawValue == "transcribed" {
            if entry.transcribed {
                let button = NSButton(title: "", target: self, action: #selector(revealTranscriptInFinder(_:)))
                button.bezelStyle = .rounded
                button.controlSize = .small
                let greenTick = NSAttributedString(string: "✓", attributes: [
                    .font: NSFont.systemFont(ofSize: 12, weight: .bold),
                    .foregroundColor: NSColor.systemGreen,
                ])
                button.attributedTitle = greenTick
                button.tag = row
                return button
            } else if transcriptionBusy && transcriptionCurrentFile == recording.outputName {
                let label = NSTextField(labelWithString: "\(transcriptionProgress)%")
                label.textColor = .systemOrange
                label.font = .monospacedDigitSystemFont(ofSize: 12, weight: .medium)
                label.alignment = .center
                return label
            } else {
                let label = NSTextField(labelWithString: "-")
                label.textColor = .tertiaryLabelColor
                label.alignment = .center
                return label
            }
        }

        let text: String
        switch identifier.rawValue {
        case "device":
            text = entry.deviceName
        case "status":
            if recording.downloaded && recording.localExists {
                text = "Downloaded"
            } else if recording.downloaded && !recording.localExists {
                text = "Marked"
            } else if recording.lastError != nil {
                text = "Failed"
            } else {
                text = "On device"
            }
        case "name":
            text = recording.outputName
        case "created":
            text = "\(recording.createDate) \(recording.createTime)"
        case "duration":
            text = formatRecordingDuration(recording.duration)
        case "size":
            text = recording.humanLength
        case "path":
            text = recording.downloaded ? recording.outputPath : "-"
        default:
            text = ""
        }

        let view = NSTextField(labelWithString: text)
        view.lineBreakMode = .byTruncatingMiddle
        if identifier.rawValue == "status" {
            if recording.downloaded && recording.localExists {
                view.textColor = .systemGreen
            } else if recording.downloaded && !recording.localExists {
                view.textColor = .systemBlue
            } else if recording.lastError != nil {
                view.textColor = .systemRed
            } else {
                view.textColor = .secondaryLabelColor
            }
        }
        return view
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

            // Collect stdout
            var outData = Data()
            let outQueue = DispatchQueue(label: "hidock.transcription.stdout")
            outPipe.fileHandleForReading.readabilityHandler = { handle in
                let chunk = handle.availableData
                if !chunk.isEmpty { outQueue.sync { outData.append(chunk) } }
            }

            // Read stderr for PROGRESS lines
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
                    // Check partial line (no newline yet)
                    if lineBuffer.hasPrefix("PROGRESS:") && lineBuffer.count < 15 {
                        // might be complete without newline
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

                // Drain remaining data
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
        syncStatusLabel?.stringValue = "Status: Transcribing \(filename)..."
        syncStatusLabel?.textColor = .secondaryLabelColor
        syncTableView?.reloadData()
        log("Starting transcription for \(filename)")

        runTranscription(arguments: ["transcribe", mp3Path], onProgress: { [weak self] pct in
            guard let self = self else { return }
            self.transcriptionProgress = pct
            self.syncStatusLabel?.stringValue = "Status: Transcribing \(filename) — \(pct)%"
            self.syncTableView?.reloadData()
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
                    self.syncStatusLabel?.stringValue = "Status: Transcription complete"
                    self.syncStatusLabel?.textColor = .systemGreen
                } else {
                    let errorMsg = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["error"] as? String ?? "Unknown error"
                    self.log("Transcription failed for \(filename): \(errorMsg)")
                    self.syncStatusLabel?.stringValue = "Status: Transcription failed"
                    self.syncStatusLabel?.textColor = .systemRed
                }
                self.refreshTranscriptionState()
            case .failure(let error):
                self.log("Transcription process failed for \(filename): \(error.localizedDescription)")
                self.syncStatusLabel?.stringValue = "Status: Transcription failed"
                self.syncStatusLabel?.textColor = .systemRed
                self.refreshTranscriptionState()
            }
        }
    }

    @objc private func transcribeSelectedRecordings() {
        guard ensureTranscriptionReady() else { return }
        let entries = selectedSyncEntries().filter { $0.recording.downloaded && $0.recording.localExists && !$0.transcribed }
        guard !entries.isEmpty else { return }

        if entries.count == 1 {
            transcribeFile(mp3Path: entries[0].recording.outputPath)
        } else {
            // For multiple files, use batch approach — transcribe sequentially
            guard !transcriptionBusy else { return }
            transcriptionBusy = true
            syncStatusLabel?.stringValue = "Status: Transcribing \(entries.count) recordings..."
            syncStatusLabel?.textColor = .secondaryLabelColor

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
            syncStatusLabel?.stringValue = "Status: Batch transcription complete"
            syncStatusLabel?.textColor = .systemGreen
            refreshTranscriptionState()
            return
        }

        let filename = (paths[index] as NSString).lastPathComponent
        transcriptionCurrentFile = filename
        transcriptionProgress = 0
        transcriptionFileIndex = index
        transcriptionFileCount = paths.count
        syncStatusLabel?.stringValue = "Status: Transcribing \(filename) (\(index + 1)/\(paths.count))..."
        syncStatusLabel?.textColor = .secondaryLabelColor
        syncTableView?.reloadData()

        runTranscription(arguments: ["transcribe", paths[index]], onProgress: { [weak self] pct in
            guard let self = self else { return }
            self.transcriptionProgress = pct
            self.syncStatusLabel?.stringValue = "Status: Transcribing \(filename) — \(pct)% (\(index + 1)/\(paths.count))"
            self.syncTableView?.reloadData()
        }) { [weak self] _ in
            self?.refreshTranscriptionState()
            self?.transcribeSequentially(paths, index: index + 1)
        }
    }

    @objc private func transcribeAllRecordings() {
        guard ensureTranscriptionReady() else { return }
        guard !transcriptionBusy else { return }

        // Gather un-transcribed downloaded recordings and use sequential transcription for progress
        let paths = visibleSyncEntries
            .filter { $0.recording.downloaded && $0.recording.localExists && !$0.transcribed }
            .map(\.recording.outputPath)

        guard !paths.isEmpty else {
            syncStatusLabel?.stringValue = "Status: All recordings already transcribed"
            syncStatusLabel?.textColor = .systemGreen
            return
        }

        transcriptionBusy = true
        transcribeSequentially(paths, index: 0)
    }

    @objc private func revealTranscriptInFinder(_ sender: NSButton) {
        let entries = visibleSyncEntries
        guard sender.tag >= 0, sender.tag < entries.count,
              let path = entries[sender.tag].transcriptPath else { return }
        let url = URL(fileURLWithPath: path)
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }

    /// Fetch transcription status and merge into syncEntries.
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
                self.syncTableView?.reloadData()
                self.updateSyncWindowState()
            }
        }
    }

    // MARK: - Logging

    private func log(_ message: String) {
        let line = "[\(Date())] \(message)\n"
        NSLog("%@", message)
        guard let data = line.data(using: .utf8) else { return }

        // Rotate if over 5 MB
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
            DispatchQueue.main.async {
                completion(success)
            }
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
