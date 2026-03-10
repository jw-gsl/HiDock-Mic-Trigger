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
    private var automationProcess: Process?
    private var automationBootstrapProcess: Process?
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
    private var syncOutputFolder: String?
    private var syncRecords: [HiDockSyncRecording] = []
    private var syncBusy = false
    private var syncRefreshStartDate: Date?
    private var syncRefreshTimer: Timer?

    private var processStartDate: Date?
    private var uptimeTimer: Timer?
    private var lastAutomationMessage: String?

    // Auto-restart tracking
    private var stoppingIntentionally = false
    private var stoppingAutomationIntentionally = false
    private var crashCount = 0
    private let maxCrashRetries = 3
    private let crashRetryDelay: TimeInterval = 3

    private let logPath = "\(NSHomeDirectory())/Library/Logs/hidock-menubar.log"
    private let repoRoot = "\(NSHomeDirectory())/_git/hidock-tools"
    private let syncPairedKey = "hidockSyncPaired"
    private let syncOutputFolderKey = "hidockSyncOutputFolder"

    private var automationRoot: String {
        "\(repoRoot)/hidock-automation"
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

    private var automationRunnerPath: String {
        "\(automationRoot)/src/runner.py"
    }

    private var automationPythonPath: String {
        "\(automationRoot)/.venv/bin/python"
    }

    private var automationEnvPath: String {
        "\(automationRoot)/.env"
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
        get { UserDefaults.standard.bool(forKey: syncPairedKey) }
        set { UserDefaults.standard.set(newValue, forKey: syncPairedKey) }
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
        stoppingAutomationIntentionally = true
        stopAutomationLoopInternal()
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
        if let mic = selectedMicName, !mic.isEmpty {
            let isFallback = preferredMicName != nil && !preferredMicName!.isEmpty && mic != preferredMicName
            let suffix = isFallback ? " (fallback)" : ""
            statusItem.button?.title = "HiDock · \(shortenMicName(mic))\(suffix)"
        } else {
            statusItem.button?.title = "HiDock · No Mic"
        }

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
        if let mic = selectedMicName, !mic.isEmpty {
            let isFallback = preferredMicName != nil && !preferredMicName!.isEmpty && mic != preferredMicName
            let suffix = isFallback ? " (fallback)" : ""
            statusItem.button?.title = "HiDock · \(shortenMicName(mic))\(suffix)"
        } else {
            statusItem.button?.title = "HiDock · No Mic"
        }
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

    // MARK: - HiNotes automation

    private func ensureAutomationReady() -> Bool {
        guard FileManager.default.fileExists(atPath: automationRunnerPath) else {
            showError("HiNotes automation runner not found.\nExpected: \(automationRunnerPath)")
            return false
        }
        guard FileManager.default.isExecutableFile(atPath: automationPythonPath) else {
            showError("Python venv not found for HiNotes automation.\nExpected executable: \(automationPythonPath)\n\nRun setup in hidock-automation first.")
            return false
        }
        guard FileManager.default.fileExists(atPath: automationEnvPath) else {
            showError("HiNotes automation .env not found.\nExpected: \(automationEnvPath)")
            return false
        }
        return true
    }

    @objc private func bootstrapAutomationAuth() {
        guard automationBootstrapProcess == nil else { return }
        guard ensureAutomationReady() else { return }
        launchAutomation(arguments: ["bootstrap-auth"], isBootstrap: true)
    }

    @objc private func startAutomationLoop() {
        guard automationProcess == nil else { return }
        guard ensureAutomationReady() else { return }
        launchAutomation(arguments: ["loop"], isBootstrap: false)
    }

    @objc private func stopAutomationLoop() {
        stoppingAutomationIntentionally = true
        stopAutomationLoopInternal()
    }

    private func stopAutomationLoopInternal() {
        if let p = automationProcess {
            log("Stopping HiNotes automation loop (pid \(p.processIdentifier))")
            p.terminate()
        }
        if let p = automationBootstrapProcess {
            log("Stopping HiNotes auth bootstrap (pid \(p.processIdentifier))")
            p.terminate()
        }
    }

    private func handleAutomationOutput(_ text: String) {
        for raw in text.components(separatedBy: .newlines) {
            let line = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            if line.isEmpty { continue }
            log("[automation] \(line)")

            if line.contains("auth_required:") {
                postNotification(title: "HiDock Reauth Required", body: "HiNotes session expired. Run auth bootstrap and solve CAPTCHA.")
                lastAutomationMessage = "Reauth required"
            } else if line.contains("downloaded=") {
                lastAutomationMessage = line
            } else if line.lowercased().contains("authentication saved") {
                lastAutomationMessage = "Authentication saved"
            }
        }
    }

    private func launchAutomation(arguments: [String], isBootstrap: Bool) {
        let p = Process()
        p.currentDirectoryURL = URL(fileURLWithPath: automationRoot)
        p.executableURL = URL(fileURLWithPath: automationPythonPath)
        p.arguments = [automationRunnerPath] + arguments

        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"
        p.environment = env

        let outPipe = Pipe()
        let errPipe = Pipe()
        p.standardOutput = outPipe
        p.standardError = errPipe

        outPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            DispatchQueue.main.async {
                self?.handleAutomationOutput(text)
            }
        }

        errPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty, let text = String(data: data, encoding: .utf8) else { return }
            DispatchQueue.main.async {
                self?.handleAutomationOutput(text)
            }
        }

        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async {
                guard let self = self else { return }
                let status = proc.terminationStatus

                outPipe.fileHandleForReading.readabilityHandler = nil
                errPipe.fileHandleForReading.readabilityHandler = nil

                if isBootstrap {
                    self.automationBootstrapProcess = nil
                    if !self.stoppingAutomationIntentionally {
                        if status == 0 {
                            self.postNotification(title: "HiDock Auth Saved", body: "HiNotes login session saved successfully.")
                            self.lastAutomationMessage = "Auth saved"
                        } else {
                            self.postNotification(title: "HiDock Auth Failed", body: "Authentication did not complete. Retry bootstrap and solve CAPTCHA.")
                            self.lastAutomationMessage = "Auth bootstrap failed"
                        }
                    }
                } else {
                    self.automationProcess = nil
                    if !self.stoppingAutomationIntentionally && status != 0 {
                        self.postNotification(title: "HiNotes Automation Stopped", body: "Sync loop exited with status \(status).")
                        self.lastAutomationMessage = "Loop exited (\(status))"
                    }
                }

                self.stoppingAutomationIntentionally = false
                self.log("HiNotes automation process ended (bootstrap=\(isBootstrap), status=\(status))")
                self.updateMenuState()
            }
        }

        do {
            try p.run()
            if isBootstrap {
                automationBootstrapProcess = p
                postNotification(title: "HiDock Auth Bootstrap", body: "Browser should open. Complete login + CAPTCHA.")
                log("Started HiNotes auth bootstrap (pid \(p.processIdentifier))")
            } else {
                automationProcess = p
                postNotification(title: "HiNotes Automation Started", body: "Background sync loop started.")
                log("Started HiNotes automation loop (pid \(p.processIdentifier))")
            }
            updateMenuState()
        } catch {
            log("Failed to start HiNotes automation: \(error)")
            showError("Failed to start HiNotes automation:\n\(error.localizedDescription)")
        }
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
            let rect = NSRect(x: 0, y: 0, width: 380, height: 320)
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
            let rect = NSRect(x: 0, y: 0, width: 860, height: 520)
            let style: NSWindow.StyleMask = [.titled, .closable, .miniaturizable, .resizable]
            let win = NSWindow(contentRect: rect, styleMask: style, backing: .buffered, defer: false)
            win.center()
            win.title = "HiDock Sync"
            win.isReleasedWhenClosed = false
            win.delegate = self

            let contentView = win.contentView!

            let titleLabel = NSTextField(labelWithString: "HiDock Sync")
            titleLabel.font = .systemFont(ofSize: 20, weight: .semibold)
            titleLabel.frame = NSRect(x: 20, y: 480, width: 300, height: 26)
            contentView.addSubview(titleLabel)

            let status = NSTextField(labelWithString: "Status: Not loaded")
            status.font = .systemFont(ofSize: 13)
            status.frame = NSRect(x: 20, y: 452, width: 520, height: 18)
            contentView.addSubview(status)
            syncStatusLabel = status

            let folder = NSTextField(labelWithString: "Output folder: Not set")
            folder.font = .systemFont(ofSize: 12)
            folder.textColor = .secondaryLabelColor
            folder.frame = NSRect(x: 20, y: 430, width: 760, height: 18)
            contentView.addSubview(folder)
            syncFolderLabel = folder
            if let savedFolder = UserDefaults.standard.string(forKey: syncOutputFolderKey), !savedFolder.isEmpty {
                syncOutputFolder = savedFolder
                syncFolderLabel?.stringValue = "Output folder: \(savedFolder)"
            }

            let pairBtn = NSButton(title: "Pair Dock", target: self, action: #selector(pairSyncDock))
            pairBtn.bezelStyle = .rounded
            pairBtn.frame = NSRect(x: 20, y: 392, width: 100, height: 28)
            contentView.addSubview(pairBtn)
            syncPairButton = pairBtn

            let unpairBtn = NSButton(title: "Unpair", target: self, action: #selector(unpairSyncDock))
            unpairBtn.bezelStyle = .rounded
            unpairBtn.frame = NSRect(x: 128, y: 392, width: 90, height: 28)
            contentView.addSubview(unpairBtn)
            syncUnpairButton = unpairBtn

            let chooseBtn = NSButton(title: "Choose Folder", target: self, action: #selector(chooseSyncOutputFolder))
            chooseBtn.bezelStyle = .rounded
            chooseBtn.frame = NSRect(x: 226, y: 392, width: 120, height: 28)
            contentView.addSubview(chooseBtn)

            let refreshBtn = NSButton(title: "Refresh", target: self, action: #selector(refreshSyncStatus))
            refreshBtn.bezelStyle = .rounded
            refreshBtn.frame = NSRect(x: 354, y: 392, width: 90, height: 28)
            contentView.addSubview(refreshBtn)
            syncRefreshButton = refreshBtn

            let downloadSelectedBtn = NSButton(title: "Download Selected", target: self, action: #selector(downloadSelectedSyncRecording))
            downloadSelectedBtn.bezelStyle = .rounded
            downloadSelectedBtn.frame = NSRect(x: 452, y: 392, width: 140, height: 28)
            contentView.addSubview(downloadSelectedBtn)
            syncDownloadSelectedButton = downloadSelectedBtn

            let downloadNewBtn = NSButton(title: "Download New", target: self, action: #selector(downloadNewSyncRecordings))
            downloadNewBtn.bezelStyle = .rounded
            downloadNewBtn.frame = NSRect(x: 600, y: 392, width: 120, height: 28)
            contentView.addSubview(downloadNewBtn)
            syncDownloadNewButton = downloadNewBtn

            let summary = NSTextField(labelWithString: "No recordings loaded")
            summary.font = .systemFont(ofSize: 12)
            summary.textColor = .secondaryLabelColor
            summary.frame = NSRect(x: 20, y: 366, width: 760, height: 18)
            contentView.addSubview(summary)
            syncSummaryLabel = summary

            let scrollView = NSScrollView(frame: NSRect(x: 20, y: 20, width: 820, height: 334))
            scrollView.hasVerticalScroller = true
            scrollView.hasHorizontalScroller = false
            scrollView.borderType = .bezelBorder

            let tableView = NSTableView(frame: scrollView.bounds)
            tableView.delegate = self
            tableView.dataSource = self
            tableView.usesAlternatingRowBackgroundColors = true
            tableView.allowsEmptySelection = true
            tableView.rowSizeStyle = .medium

            let columns: [(String, String, CGFloat)] = [
                ("status", "Status", 110),
                ("name", "Recording", 250),
                ("created", "Created", 170),
                ("duration", "Length", 80),
                ("size", "Size", 90),
                ("mode", "Mode", 90),
                ("path", "Output", 220),
            ]
            for (identifier, title, width) in columns {
                let column = NSTableColumn(identifier: NSUserInterfaceItemIdentifier(rawValue: identifier))
                column.title = title
                column.width = width
                tableView.addTableColumn(column)
            }

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

    private func runExtractor(arguments: [String], completion: @escaping (Result<Data, Error>) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            let process = Process()
            process.currentDirectoryURL = URL(fileURLWithPath: self.extractorRoot)
            process.executableURL = URL(fileURLWithPath: self.extractorPythonPath)
            process.arguments = [self.extractorScriptPath] + arguments

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

    private func updateSyncWindowState() {
        let selectedRow = syncTableView?.selectedRow ?? -1
        let hasSelection = selectedRow >= 0 && selectedRow < syncRecords.count

        syncRefreshButton?.isEnabled = !syncBusy
        syncDownloadNewButton?.isEnabled = !syncBusy && syncPaired
        syncDownloadSelectedButton?.isEnabled = !syncBusy && syncPaired && hasSelection
        syncPairButton?.isEnabled = !syncBusy && !syncPaired
        syncUnpairButton?.isEnabled = !syncBusy && syncPaired
    }

    private func renderSyncStatus(_ status: HiDockSyncStatusResponse) {
        syncOutputFolder = status.outputDir
        UserDefaults.standard.set(status.outputDir, forKey: syncOutputFolderKey)
        syncRecords = status.recordings
        syncTableView?.reloadData()

        let downloadedCount = status.recordings.filter(\.downloaded).count
        let statusText: String
        if status.connected {
            statusText = syncPaired ? "Status: Paired and connected" : "Status: Connected"
            syncStatusLabel?.textColor = .systemGreen
        } else if let error = status.error {
            statusText = syncPaired ? "Status: Paired but unavailable (\(error))" : "Status: Unavailable (\(error))"
            syncStatusLabel?.textColor = .systemOrange
        } else {
            statusText = "Status: Not connected"
            syncStatusLabel?.textColor = .secondaryLabelColor
        }
        syncStatusLabel?.stringValue = statusText
        syncFolderLabel?.stringValue = "Output folder: \(status.outputDir)"
        syncSummaryLabel?.stringValue = "\(status.recordings.count) recordings, \(downloadedCount) downloaded"
        updateSyncWindowState()
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

    private func selectedSyncRecording() -> HiDockSyncRecording? {
        let row = syncTableView?.selectedRow ?? -1
        guard row >= 0, row < syncRecords.count else { return nil }
        return syncRecords[row]
    }

    @objc private func refreshSyncStatus() {
        guard ensureExtractorReady() else { return }
        syncBusy = true
        syncStatusLabel?.stringValue = "Status: Refreshing..."
        syncStatusLabel?.textColor = .secondaryLabelColor
        startSyncRefreshTimer()
        updateSyncWindowState()

        runExtractor(arguments: ["status", "--timeout-ms", "2000"]) { [weak self] result in
            guard let self = self else { return }
            self.syncBusy = false
            self.stopSyncRefreshTimer()
            switch result {
            case .success(let data):
                do {
                    let payload = try JSONDecoder().decode(HiDockSyncStatusResponse.self, from: data)
                    self.renderSyncStatus(payload)
                } catch {
                    self.syncStatusLabel?.stringValue = "Status: Failed to decode extractor output"
                    self.syncStatusLabel?.textColor = .systemRed
                    self.log("HiDock sync decode failure: \(error.localizedDescription)")
                    self.showError("Failed to decode HiDock sync status:\n\(error.localizedDescription)")
                }
            case .failure(let error):
                let message = self.syncErrorDescription(error.localizedDescription)
                self.syncStatusLabel?.stringValue = "Status: \(message)"
                self.syncStatusLabel?.textColor = .systemRed
                self.log("HiDock sync status error: \(error.localizedDescription)")
            }
            self.updateSyncWindowState()
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
        syncPaired = true
        refreshSyncStatus()
    }

    @objc private func unpairSyncDock() {
        syncPaired = false
        syncRecords = []
        syncTableView?.reloadData()
        syncStatusLabel?.stringValue = "Status: Unpaired"
        syncStatusLabel?.textColor = .secondaryLabelColor
        syncSummaryLabel?.stringValue = "No recordings loaded"
        updateSyncWindowState()
    }

    @objc private func downloadSelectedSyncRecording() {
        guard ensureExtractorReady() else { return }
        guard let recording = selectedSyncRecording() else { return }

        syncBusy = true
        syncStatusLabel?.stringValue = "Status: Downloading \(recording.outputName)..."
        syncStatusLabel?.textColor = .secondaryLabelColor
        updateSyncWindowState()

        runExtractor(arguments: ["download", recording.name, "--length", "\(recording.length)"]) { [weak self] result in
            guard let self = self else { return }
            self.syncBusy = false
            switch result {
            case .success(let data):
                if let payload = try? JSONDecoder().decode(HiDockSyncDownloadResult.self, from: data) {
                    let body = "\(payload.filename.replacingOccurrences(of: ".hda", with: ".mp3")) saved successfully."
                    self.postSyncDownloadNotification(title: "✅ HiDock Download Complete", body: body)
                } else {
                    let body = "\(recording.outputName) saved successfully."
                    self.postSyncDownloadNotification(title: "✅ HiDock Download Complete", body: body)
                }
                self.refreshSyncStatus()
            case .failure(let error):
                self.syncStatusLabel?.stringValue = "Status: Download failed"
                self.syncStatusLabel?.textColor = .systemRed
                self.showError("Failed to download \(recording.name):\n\(error.localizedDescription)")
                self.updateSyncWindowState()
            }
        }
    }

    @objc private func downloadNewSyncRecordings() {
        guard ensureExtractorReady() else { return }

        syncBusy = true
        syncStatusLabel?.stringValue = "Status: Downloading new recordings..."
        syncStatusLabel?.textColor = .secondaryLabelColor
        updateSyncWindowState()

        runExtractor(arguments: ["download-new"]) { [weak self] result in
            guard let self = self else { return }
            self.syncBusy = false
            switch result {
            case .success(let data):
                do {
                    let payload = try JSONDecoder().decode(HiDockSyncDownloadNewResponse.self, from: data)
                    if let error = payload.error {
                        self.syncStatusLabel?.stringValue = "Status: \(error)"
                        self.syncStatusLabel?.textColor = .systemRed
                    } else {
                        self.syncStatusLabel?.stringValue = "Status: Downloaded \(payload.downloaded.count) new recordings"
                        self.syncStatusLabel?.textColor = .systemGreen
                        if payload.downloaded.count > 0 {
                            let body: String
                            if payload.downloaded.count == 1, let first = payload.downloaded.first {
                                body = "\(first.filename.replacingOccurrences(of: ".hda", with: ".mp3")) saved successfully."
                            } else {
                                body = "\(payload.downloaded.count) new recordings were saved successfully."
                            }
                            self.postSyncDownloadNotification(title: "✅ HiDock Downloads Complete", body: body)
                        }
                    }
                    self.refreshSyncStatus()
                } catch {
                    self.syncStatusLabel?.stringValue = "Status: Download finished"
                    self.syncStatusLabel?.textColor = .secondaryLabelColor
                    self.refreshSyncStatus()
                }
            case .failure(let error):
                self.syncStatusLabel?.stringValue = "Status: Download failed"
                self.syncStatusLabel?.textColor = .systemRed
                self.showError("Failed to download new HiDock recordings:\n\(error.localizedDescription)")
                self.updateSyncWindowState()
            }
        }
    }

    func numberOfRows(in tableView: NSTableView) -> Int {
        if tableView == syncTableView {
            return syncRecords.count
        }
        return 0
    }

    func tableViewSelectionDidChange(_ notification: Notification) {
        updateSyncWindowState()
    }

    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        guard tableView == syncTableView, row >= 0, row < syncRecords.count, let tableColumn = tableColumn else {
            return nil
        }

        let recording = syncRecords[row]
        let identifier = tableColumn.identifier
        let text: String
        switch identifier.rawValue {
        case "status":
            text = recording.downloaded ? "Downloaded" : (recording.lastError == nil ? "On device" : "Failed")
        case "name":
            text = recording.outputName
        case "created":
            text = "\(recording.createDate) \(recording.createTime)"
        case "duration":
            text = formatRecordingDuration(recording.duration)
        case "size":
            text = recording.humanLength
        case "mode":
            text = recording.mode.capitalized
        case "path":
            text = recording.downloaded ? recording.outputPath : "-"
        default:
            text = ""
        }

        let view = NSTextField(labelWithString: text)
        view.lineBreakMode = .byTruncatingMiddle
        if identifier.rawValue == "status" {
            if recording.downloaded {
                view.textColor = .systemGreen
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
