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
        var raw = productName ?? "HiDock"
        // Remove serial number in brackets/parentheses, e.g. "HiDock_H1_(SN12345)" -> "HiDock_H1"
        if let range = raw.range(of: "\\s*[\\(\\[].*[\\)\\]]", options: .regularExpression) {
            raw.removeSubrange(range)
        }
        return raw.replacingOccurrences(of: "_", with: " ").trimmingCharacters(in: .whitespaces)
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
        var raw = displayName
        if let range = raw.range(of: "\\s*[\\(\\[].*[\\)\\]]", options: .regularExpression) {
            raw.removeSubrange(range)
        }
        return raw.replacingOccurrences(of: "_", with: " ").trimmingCharacters(in: .whitespaces)
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
}

private func formatRecordingDuration(_ seconds: Double) -> String {
    let total = max(Int(seconds.rounded()), 0)
    let hours = total / 3600
    let minutes = (total % 3600) / 60
    let secs = total % 60
    if hours > 0 {
        return String(format: "%d:%02d:%02d", hours, minutes, secs)
    }
    return String(format: "%d:%02d", minutes, secs)
}

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
    private var window: NSWindow?

    private var windowStartButton: NSButton?
    private var windowStopButton: NSButton?
    private var windowStatusLabel: NSTextField?
    private var windowUptimeLabel: NSTextField?
    private var windowAutoStartCheckbox: NSButton?
    private var windowMicPopup: NSPopUpButton?
    private var windowSyncButton: NSButton?
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
    private var windowSyncSummaryLabel: NSTextField?
    private var syncExtractorProcess: Process?
    private var syncStopButton: NSButton?
    private var syncDownloadStartDate: Date?
    private var syncDownloadTimer: Timer?
    private var syncDownloadStopping = false
    private var syncDownloading = false

    private var processStartDate: Date?
    private var uptimeTimer: Timer?

    // Auto-restart tracking
    private var stoppingIntentionally = false
    private var crashCount = 0
    private let maxCrashRetries = 3
    private let crashRetryDelay: TimeInterval = 3

    private let logPath = "\(NSHomeDirectory())/Library/Logs/hidock-menubar.log"
    private let repoRoot = "\(NSHomeDirectory())/_git/hidock-tools"
    private let syncPairedKey = "hidockSyncPaired"
    private let syncPairedDevicesKey = "hidockSyncPairedDevices"
    private let syncOutputFolderKey = "hidockSyncOutputFolder"
    private let syncHideDownloadedKey = "hidockSyncHideDownloaded"
    private let syncAutoDownloadKey = "hidockSyncAutoDownload"

    private var extractorRoot: String {
        "\(repoRoot)/usb-extractor"
    }

    private var extractorScriptPath: String {
        "\(extractorRoot)/extractor.py"
    }

    private var extractorPythonPath: String {
        "\(extractorRoot)/.venv/bin/python"
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
        showWindow()
        if autoStartOnLaunch {
            startTrigger()
        }
        autoConnectSyncIfPaired()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return false
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showWindow()
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
                self?.showWindow()
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

        startItem = NSMenuItem(title: "Start", action: #selector(startTrigger), keyEquivalent: "s")
        stopItem = NSMenuItem(title: "Stop", action: #selector(stopTrigger), keyEquivalent: "t")
        autoStartItem = NSMenuItem(title: "Auto-start on launch", action: #selector(toggleAutoStart), keyEquivalent: "")
        syncWindowItem = NSMenuItem(title: "HiDock Sync...", action: #selector(showSyncWindow), keyEquivalent: "d")

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
        // Device sync status right after HiDock
        let pairedDevices = syncPairedDevices
        if !pairedDevices.isEmpty {
            let deviceParts = pairedDevices.map { device -> String in
                let connected = syncDeviceConnected[device.productId] ?? false
                return "\(device.shortName) \(connected ? "✓" : "⚠")"
            }
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

    private func shortenMicName(_ name: String) -> String {
        let noise: [String] = [
            "Microphone", "Mic", "USB Audio", "USB", "Audio Device",
            "Digital", "Sound", "Device", "Input",
        ]
        var parts = name.components(separatedBy: " ")
        parts = parts.filter { word in
            !noise.contains { word.caseInsensitiveCompare($0) == .orderedSame }
        }
        let short = parts.joined(separator: " ")
            .trimmingCharacters(in: .whitespaces)
        if short.isEmpty { return name }
        return short
    }

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
        guard ensureExtractorReady() else { return }

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
            self.windowSyncSummaryLabel?.stringValue = "Sync: Refreshing \(devices.count) device(s)..."
            self.windowSyncSummaryLabel?.textColor = .secondaryLabelColor

            let group = DispatchGroup()
            var anyConnected = false

            for device in devices {
                group.enter()
                self.runExtractor(arguments: ["status", "--timeout-ms", "2000"], productId: device.productId) { [weak self] result in
                    guard let self = self else { group.leave(); return }
                    switch result {
                    case .success(let data):
                        if let status = try? JSONDecoder().decode(HiDockSyncStatusResponse.self, from: data) {
                            self.renderSyncStatus(status, deviceProductId: device.productId, deviceName: device.cleanName)
                            if status.connected { anyConnected = true }
                        }
                    case .failure:
                        break
                    }
                    group.leave()
                }
            }

            group.notify(queue: .main) { [weak self] in
                guard let self = self else { return }
                self.updateWindowSyncSummary()
                self.updateMenuSyncStatus(connected: anyConnected)
            }
        }
    }

    private func updateMenuSyncStatus(connected: Bool) {
        if !syncPaired {
            syncWindowItem?.title = "HiDock Sync..."
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
        showWindow()
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
        if window == nil {
            let rect = NSRect(x: 0, y: 0, width: 380, height: 380)
            let style: NSWindow.StyleMask = [.titled, .closable, .miniaturizable]
            let win = NSWindow(contentRect: rect, styleMask: style, backing: .buffered, defer: false)
            win.center()
            win.title = "HiDock Mic Trigger"
            win.isReleasedWhenClosed = false
            win.delegate = self

            let contentView = win.contentView!

            let titleLabel = NSTextField(labelWithString: "HiDock Mic Trigger")
            titleLabel.font = .systemFont(ofSize: 18, weight: .semibold)
            titleLabel.frame = NSRect(x: 20, y: 268, width: 340, height: 30)
            contentView.addSubview(titleLabel)

            let status = NSTextField(labelWithString: "Stopped")
            status.font = .systemFont(ofSize: 14)
            status.textColor = .secondaryLabelColor
            status.frame = NSRect(x: 20, y: 240, width: 340, height: 22)
            contentView.addSubview(status)
            windowStatusLabel = status

            let uptime = NSTextField(labelWithString: "")
            uptime.font = .systemFont(ofSize: 12)
            uptime.textColor = .secondaryLabelColor
            uptime.frame = NSRect(x: 20, y: 218, width: 340, height: 18)
            contentView.addSubview(uptime)
            windowUptimeLabel = uptime

            let startBtn = NSButton(title: "Start", target: self, action: #selector(startTrigger))
            startBtn.bezelStyle = .rounded
            startBtn.frame = NSRect(x: 80, y: 170, width: 100, height: 32)
            contentView.addSubview(startBtn)
            windowStartButton = startBtn

            let stopBtn = NSButton(title: "Stop", target: self, action: #selector(stopTrigger))
            stopBtn.bezelStyle = .rounded
            stopBtn.frame = NSRect(x: 200, y: 170, width: 100, height: 32)
            contentView.addSubview(stopBtn)
            windowStopButton = stopBtn

            let separator = NSBox()
            separator.boxType = .separator
            separator.frame = NSRect(x: 20, y: 150, width: 340, height: 1)
            contentView.addSubview(separator)

            let micLabel = NSTextField(labelWithString: "Trigger Mic:")
            micLabel.font = .systemFont(ofSize: 13)
            micLabel.frame = NSRect(x: 20, y: 118, width: 90, height: 20)
            contentView.addSubview(micLabel)

            let micPopup = NSPopUpButton(frame: NSRect(x: 112, y: 114, width: 248, height: 28), pullsDown: false)
            micPopup.target = self
            micPopup.action = #selector(windowMicChanged(_:))
            contentView.addSubview(micPopup)
            windowMicPopup = micPopup

            let autoStart = NSButton(checkboxWithTitle: "Auto-start on launch", target: self, action: #selector(toggleAutoStart))
            autoStart.frame = NSRect(x: 20, y: 80, width: 200, height: 22)
            contentView.addSubview(autoStart)
            windowAutoStartCheckbox = autoStart

            let syncButton = NSButton(title: "Open HiDock Sync", target: self, action: #selector(showSyncWindow))
            syncButton.bezelStyle = .rounded
            syncButton.frame = NSRect(x: 220, y: 76, width: 140, height: 28)
            contentView.addSubview(syncButton)
            windowSyncButton = syncButton

            let separator2 = NSBox()
            separator2.boxType = .separator
            separator2.frame = NSRect(x: 20, y: 65, width: 340, height: 1)
            contentView.addSubview(separator2)

            let syncSummary = NSTextField(labelWithString: "HiDock Sync: Not paired")
            syncSummary.font = .systemFont(ofSize: 11)
            syncSummary.textColor = .secondaryLabelColor
            syncSummary.frame = NSRect(x: 20, y: 5, width: 340, height: 56)
            syncSummary.maximumNumberOfLines = 5
            syncSummary.lineBreakMode = .byWordWrapping
            contentView.addSubview(syncSummary)
            windowSyncSummaryLabel = syncSummary

            window = win
            updateWindowState()
        }
        refreshWindowMicPopup()
        NSApp.setActivationPolicy(.regular)
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc private func showSyncWindow() {
        if syncWindow == nil {
            let rect = NSRect(x: 0, y: 0, width: 1120, height: 520)
            let style: NSWindow.StyleMask = [.titled, .closable, .miniaturizable, .resizable]
            let win = NSWindow(contentRect: rect, styleMask: style, backing: .buffered, defer: false)
            win.center()
            win.title = "HiDock Sync"
            win.isReleasedWhenClosed = false
            win.delegate = self
            win.minSize = NSSize(width: 980, height: 440)

            let contentView = win.contentView!

            let titleLabel = NSTextField(labelWithString: "HiDock Sync")
            titleLabel.font = .systemFont(ofSize: 20, weight: .semibold)
            titleLabel.frame = NSRect(x: 20, y: 480, width: 300, height: 26)
            titleLabel.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(titleLabel)

            let status = NSTextField(labelWithString: "Status: Not loaded")
            status.font = .systemFont(ofSize: 13)
            status.frame = NSRect(x: 20, y: 452, width: 760, height: 18)
            status.autoresizingMask = [.width, .minYMargin]
            contentView.addSubview(status)
            syncStatusLabel = status

            let folder = NSTextField(labelWithString: "Output folder: Not set")
            folder.font = .systemFont(ofSize: 12)
            folder.textColor = .secondaryLabelColor
            folder.frame = NSRect(x: 20, y: 430, width: 1080, height: 18)
            folder.autoresizingMask = [.width, .minYMargin]
            contentView.addSubview(folder)
            syncFolderLabel = folder
            if let savedFolder = UserDefaults.standard.string(forKey: syncOutputFolderKey), !savedFolder.isEmpty {
                syncOutputFolder = savedFolder
                syncFolderLabel?.stringValue = "Output folder: \(savedFolder)"
            }

            let pairBtn = NSButton(title: "Pair Dock", target: self, action: #selector(pairSyncDock))
            pairBtn.bezelStyle = .rounded
            pairBtn.frame = NSRect(x: 20, y: 392, width: 100, height: 28)
            pairBtn.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(pairBtn)
            syncPairButton = pairBtn

            let unpairBtn = NSButton(title: "Unpair", target: self, action: #selector(unpairSyncDock))
            unpairBtn.bezelStyle = .rounded
            unpairBtn.frame = NSRect(x: 128, y: 392, width: 90, height: 28)
            unpairBtn.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(unpairBtn)
            syncUnpairButton = unpairBtn

            let chooseBtn = NSButton(title: "Choose Folder", target: self, action: #selector(chooseSyncOutputFolder))
            chooseBtn.bezelStyle = .rounded
            chooseBtn.frame = NSRect(x: 226, y: 392, width: 120, height: 28)
            chooseBtn.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(chooseBtn)

            let refreshBtn = NSButton(title: "Refresh", target: self, action: #selector(refreshSyncStatus))
            refreshBtn.bezelStyle = .rounded
            refreshBtn.frame = NSRect(x: 354, y: 392, width: 90, height: 28)
            refreshBtn.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(refreshBtn)
            syncRefreshButton = refreshBtn

            let downloadSelectedBtn = NSButton(title: "Download Selected", target: self, action: #selector(downloadSelectedSyncRecording))
            downloadSelectedBtn.bezelStyle = .rounded
            downloadSelectedBtn.frame = NSRect(x: 452, y: 392, width: 140, height: 28)
            downloadSelectedBtn.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(downloadSelectedBtn)
            syncDownloadSelectedButton = downloadSelectedBtn

            let downloadNewBtn = NSButton(title: "Download New", target: self, action: #selector(downloadNewSyncRecordings))
            downloadNewBtn.bezelStyle = .rounded
            downloadNewBtn.frame = NSRect(x: 600, y: 392, width: 120, height: 28)
            downloadNewBtn.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(downloadNewBtn)
            syncDownloadNewButton = downloadNewBtn

            let markBtn = NSButton(title: "Mark as Downloaded", target: self, action: #selector(markSyncRecordingsAsDownloaded))
            markBtn.bezelStyle = .rounded
            markBtn.frame = NSRect(x: 728, y: 392, width: 160, height: 28)
            markBtn.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(markBtn)

            let stopBtn = NSButton(title: "Stop Download", target: self, action: #selector(stopSyncDownload))
            stopBtn.bezelStyle = .rounded
            stopBtn.frame = NSRect(x: 452, y: 392, width: 268, height: 28)
            stopBtn.autoresizingMask = [.maxXMargin, .minYMargin]
            stopBtn.contentTintColor = .systemRed
            stopBtn.isHidden = true
            contentView.addSubview(stopBtn)
            syncStopButton = stopBtn

            let hideDownloadedCheckbox = NSButton(checkboxWithTitle: "Hide Downloaded", target: self, action: #selector(toggleHideDownloaded(_:)))
            hideDownloadedCheckbox.frame = NSRect(x: 896, y: 395, width: 150, height: 22)
            hideDownloadedCheckbox.state = UserDefaults.standard.bool(forKey: syncHideDownloadedKey) ? .on : .off
            hideDownloadedCheckbox.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(hideDownloadedCheckbox)
            syncHideDownloadedCheckbox = hideDownloadedCheckbox
            syncHideDownloaded = hideDownloadedCheckbox.state == .on

            let autoDownloadCheckbox = NSButton(checkboxWithTitle: "Auto-download New", target: self, action: #selector(toggleAutoDownload(_:)))
            autoDownloadCheckbox.frame = NSRect(x: 896, y: 375, width: 170, height: 22)
            autoDownloadCheckbox.state = UserDefaults.standard.bool(forKey: syncAutoDownloadKey) ? .on : .off
            autoDownloadCheckbox.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(autoDownloadCheckbox)
            syncAutoDownloadCheckbox = autoDownloadCheckbox
            syncAutoDownload = autoDownloadCheckbox.state == .on

            let summary = NSTextField(labelWithString: "No recordings loaded")
            summary.font = .systemFont(ofSize: 12)
            summary.textColor = .secondaryLabelColor
            summary.alignment = .right
            summary.frame = NSRect(x: 500, y: 430, width: 600, height: 18)
            summary.autoresizingMask = [.width, .minYMargin]
            contentView.addSubview(summary)
            syncSummaryLabel = summary

            let scrollView = NSScrollView(frame: NSRect(x: 20, y: 20, width: 1080, height: 326))
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
                ("name", "Recording", 250),
                ("created", "Created", 170),
                ("duration", "Length", 90),
                ("size", "Size", 90),
                ("path", "Output", 320),
                ("reveal", "", 90),
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

            // Select All / Select Not Downloaded buttons in a row below the toolbar
            let selectAllBtn = NSButton(title: "Select All", target: self, action: #selector(selectAllSyncRecordings))
            selectAllBtn.bezelStyle = .rounded
            selectAllBtn.controlSize = .small
            selectAllBtn.font = .systemFont(ofSize: 11)
            selectAllBtn.frame = NSRect(x: 20, y: 356, width: 80, height: 22)
            selectAllBtn.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(selectAllBtn)

            let selectNoneBtn = NSButton(title: "Select None", target: self, action: #selector(selectNoneSyncRecordings))
            selectNoneBtn.bezelStyle = .rounded
            selectNoneBtn.controlSize = .small
            selectNoneBtn.font = .systemFont(ofSize: 11)
            selectNoneBtn.frame = NSRect(x: 108, y: 356, width: 86, height: 22)
            selectNoneBtn.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(selectNoneBtn)

            let selectNewBtn = NSButton(title: "Select Not Downloaded", target: self, action: #selector(selectNotDownloadedSyncRecordings))
            selectNewBtn.bezelStyle = .rounded
            selectNewBtn.controlSize = .small
            selectNewBtn.font = .systemFont(ofSize: 11)
            selectNewBtn.frame = NSRect(x: 202, y: 356, width: 150, height: 22)
            selectNewBtn.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(selectNewBtn)

            // Device filter buttons
            let filterLabel = NSTextField(labelWithString: "Filter:")
            filterLabel.font = .systemFont(ofSize: 11, weight: .medium)
            filterLabel.frame = NSRect(x: 380, y: 356, width: 40, height: 22)
            filterLabel.autoresizingMask = [.maxXMargin, .minYMargin]
            contentView.addSubview(filterLabel)

            let allDevicesBtn = NSButton(title: "All", target: self, action: #selector(filterSyncByDevice(_:)))
            allDevicesBtn.bezelStyle = .rounded
            allDevicesBtn.controlSize = .small
            allDevicesBtn.font = .systemFont(ofSize: 11, weight: .semibold)
            allDevicesBtn.tag = 0
            allDevicesBtn.frame = NSRect(x: 422, y: 356, width: 40, height: 22)
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
                btn.frame = NSRect(x: filterX, y: 356, width: btnWidth, height: 22)
                btn.autoresizingMask = [.maxXMargin, .minYMargin]
                contentView.addSubview(btn)
                syncDeviceFilterButtons.append(btn)
                filterX += btnWidth + 8
            }

            let tableMenu = NSMenu()
            tableMenu.addItem(NSMenuItem(title: "Mark as Downloaded", action: #selector(markSyncRecordingsAsDownloaded), keyEquivalent: ""))
            tableMenu.addItem(NSMenuItem(title: "Show in Finder", action: #selector(revealSelectedSyncRecordingInFinder), keyEquivalent: ""))
            tableView.menu = tableMenu

            scrollView.documentView = tableView
            contentView.addSubview(scrollView)
            syncTableView = tableView

            syncWindow = win
            updateSyncWindowState()
        }

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
        guard FileManager.default.fileExists(atPath: extractorScriptPath) else {
            showError("HiDock extractor not found.\nExpected: \(extractorScriptPath)")
            return false
        }
        guard FileManager.default.isExecutableFile(atPath: extractorPythonPath) else {
            showError("Extractor Python venv not found.\nExpected executable: \(extractorPythonPath)")
            return false
        }
        return true
    }

    private func runExtractor(arguments: [String], productId: Int? = nil, completion: @escaping (Result<Data, Error>) -> Void) {
        let fullArgs = extractorArguments(arguments, productId: productId)
        DispatchQueue.global(qos: .userInitiated).async {
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
                process.waitUntilExit()
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
        DispatchQueue.global(qos: .userInitiated).async {
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
            let devices = syncPairedDevices
            var parts: [String] = []
            for device in devices {
                let connected = syncDeviceConnected[device.productId] ?? false
                parts.append("\(device.shortName) \(connected ? "✓" : "⚠")")
            }
            if parts.isEmpty {
                syncStatusLabel?.stringValue = "Status: Not connected"
                syncStatusLabel?.textColor = .secondaryLabelColor
            } else {
                syncStatusLabel?.stringValue = "Status: \(parts.joined(separator: " · "))"
                syncStatusLabel?.textColor = syncDeviceConnected.values.contains(true) ? .systemGreen : .systemOrange
            }
            updateSyncWindowState()
            updateMenuSyncStatus(connected: status.connected)
        }
    }

    private func syncErrorDescription(_ error: String) -> String {
        if error.contains("Errno 13") || error.localizedCaseInsensitiveContains("Access denied") {
            return "Dock busy: close browsers or other tools using HiDock, then Refresh"
        }
        return error
    }

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
        updateWindowSyncSummary()
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
                        if payload.connected { anyConnected = true }
                    } catch {
                        self.log("HiDock sync decode failure for \(device.cleanName): \(error.localizedDescription)")
                        lastError = error.localizedDescription
                    }
                case .failure(let error):
                    self.log("HiDock sync status error for \(device.cleanName): \(error.localizedDescription)")
                    lastError = error.localizedDescription
                }
                group.leave()
            }
        }

        group.notify(queue: .main) { [weak self] in
            guard let self = self else { return }
            self.syncBusy = false
            self.stopSyncRefreshTimer()
            if anyConnected {
                self.syncStatusLabel?.stringValue = "Status: Paired and connected (\(devices.count) device\(devices.count == 1 ? "" : "s"))"
                self.syncStatusLabel?.textColor = .systemGreen
            } else if let err = lastError {
                let message = self.syncErrorDescription(err)
                self.syncStatusLabel?.stringValue = "Status: \(message)"
                self.syncStatusLabel?.textColor = .systemRed
            }
            self.updateSyncSummary()
            self.updateSyncWindowState()
            self.updateMenuSyncStatus(connected: anyConnected)
        }
    }

    @objc private func chooseSyncOutputFolder() {
        guard ensureExtractorReady() else { return }
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Choose Output Folder"
        if let current = syncOutputFolder {
            panel.directoryURL = URL(fileURLWithPath: current)
        }

        if panel.runModal() == .OK, let url = panel.url {
            runExtractor(arguments: ["set-output", url.path]) { [weak self] result in
                guard let self = self else { return }
                switch result {
                case .success:
                    self.syncOutputFolder = url.path
                    self.syncFolderLabel?.stringValue = "Output folder: \(url.path)"
                    UserDefaults.standard.set(url.path, forKey: self.syncOutputFolderKey)
                    self.refreshSyncStatus()
                case .failure(let error):
                    self.log("HiDock sync set-output error: \(error.localizedDescription)")
                    self.showError("Failed to set HiDock output folder:\n\(error.localizedDescription)")
                }
            }
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
        updateWindowSyncSummary()
        updateMenuSyncStatus(connected: false)
    }

    private func updateWindowSyncSummary() {
        guard let label = windowSyncSummaryLabel else { return }
        let devices = syncPairedDevices
        if devices.isEmpty {
            label.stringValue = "HiDock Sync: Not paired"
            label.textColor = .secondaryLabelColor
            return
        }

        let result = NSMutableAttributedString()
        let headerAttrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 11, weight: .semibold),
            .foregroundColor: NSColor.labelColor,
        ]
        let normalAttrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: 11),
            .foregroundColor: NSColor.secondaryLabelColor,
        ]

        let connectedCount = devices.filter { syncDeviceConnected[$0.productId] == true }.count
        let header = "Sync: \(devices.count) paired · \(connectedCount) connected\n"
        result.append(NSAttributedString(string: header, attributes: headerAttrs))

        for device in devices {
            let connected = syncDeviceConnected[device.productId] ?? false
            let deviceEntries = syncEntries.filter { $0.deviceProductId == device.productId }
            let total = deviceEntries.count
            let downloaded = deviceEntries.filter(\.recording.downloaded).count
            let pending = total - downloaded
            let statusIcon = connected ? "✓" : "⚠"
            var line = "\(statusIcon) \(device.cleanName) — \(total) recordings"
            if downloaded > 0 { line += ", \(downloaded) downloaded" }
            if pending > 0 { line += ", \(pending) pending" }
            line += "\n"

            let lineColor: NSColor = connected ? .secondaryLabelColor : .systemOrange
            var lineAttrs = normalAttrs
            lineAttrs[.foregroundColor] = lineColor
            result.append(NSAttributedString(string: line, attributes: lineAttrs))
        }

        label.attributedStringValue = result
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
            let button = NSButton(title: "Show", target: self, action: #selector(revealSyncRecordingInFinder(_:)))
            button.bezelStyle = .rounded
            button.controlSize = .small
            button.font = .systemFont(ofSize: 11)
            button.tag = row
            return button
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

    // MARK: - Logging

    private func log(_ message: String) {
        let line = "[\(Date())] \(message)\n"
        NSLog("%@", message)
        guard let data = line.data(using: .utf8) else { return }
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
