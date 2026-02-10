import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private var statusItem: NSStatusItem!
    private let menu = NSMenu()
    private var startItem: NSMenuItem!
    private var stopItem: NSMenuItem!
    private var autoStartItem: NSMenuItem!
    private var process: Process?
    private var window: NSWindow?

    private var windowStartButton: NSButton?
    private var windowStopButton: NSButton?
    private var windowStatusLabel: NSTextField?
    private var windowUptimeLabel: NSTextField?
    private var windowAutoStartCheckbox: NSButton?

    private var processStartDate: Date?
    private var uptimeTimer: Timer?

    private let logPath = "\(NSHomeDirectory())/Library/Logs/hidock-menubar.log"
    private let repoRoot = "\(NSHomeDirectory())/_git/hidock-tools"

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

    // MARK: - App lifecycle

    func applicationDidFinishLaunching(_ notification: Notification) {
        log("applicationDidFinishLaunching")
        NSApp.setActivationPolicy(.accessory)
        setupMainMenu()
        setupStatusItem()
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
        stopTrigger()
    }

    // MARK: - NSWindowDelegate

    func windowWillClose(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
    }

    // MARK: - Menu bar

    private func setupStatusItem() {
        log("setupStatusItem")
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "HiDock"
        statusItem.button?.image = statusImage(running: false)
        statusItem.button?.imagePosition = .imageLeft

        startItem = NSMenuItem(title: "Start", action: #selector(startTrigger), keyEquivalent: "s")
        stopItem = NSMenuItem(title: "Stop", action: #selector(stopTrigger), keyEquivalent: "t")
        autoStartItem = NSMenuItem(title: "Auto-start on launch", action: #selector(toggleAutoStart), keyEquivalent: "")
        let logsItem = NSMenuItem(title: "Show Logs", action: #selector(showLogs), keyEquivalent: "l")
        let statusInfoItem = NSMenuItem(title: "Show Status", action: #selector(showStatus), keyEquivalent: "i")
        let quitItem = NSMenuItem(title: "Quit", action: #selector(quitApp), keyEquivalent: "q")

        for item in [startItem, stopItem, autoStartItem, logsItem, statusInfoItem, quitItem] {
            item?.target = self
        }

        menu.addItem(startItem)
        menu.addItem(stopItem)
        menu.addItem(NSMenuItem.separator())
        menu.addItem(autoStartItem)
        menu.addItem(logsItem)
        menu.addItem(statusInfoItem)
        menu.addItem(NSMenuItem.separator())
        menu.addItem(quitItem)

        statusItem.menu = menu
        updateMenuState()
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

    private func updateMenuState() {
        let running = (process != nil)
        startItem.isEnabled = !running
        stopItem.isEnabled = running
        autoStartItem.state = autoStartOnLaunch ? .on : .off
        statusItem.button?.image = statusImage(running: running)
        statusItem.button?.title = "HiDock"
        updateWindowState()
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
        guard let start = processStartDate else {
            windowUptimeLabel?.stringValue = ""
            return
        }
        let elapsed = Int(Date().timeIntervalSince(start))
        let text: String
        if elapsed < 60 {
            text = "Uptime: \(elapsed)s"
        } else if elapsed < 3600 {
            text = "Uptime: \(elapsed / 60)m \(elapsed % 60)s"
        } else {
            let h = elapsed / 3600
            let m = (elapsed % 3600) / 60
            text = "Uptime: \(h)h \(m)m"
        }
        windowUptimeLabel?.stringValue = text
    }

    private func startUptimeTimer() {
        guard uptimeTimer == nil else { return }
        uptimeTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            self?.updateUptimeLabel()
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
        p.standardOutput = Pipe()
        p.standardError = Pipe()
        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async {
                self?.log("Process terminated with status \(proc.terminationStatus)")
                self?.process = nil
                self?.processStartDate = nil
                self?.updateMenuState()
            }
        }

        do {
            try p.run()
            process = p
            processStartDate = Date()
            log("Started hidock-mic-trigger (pid \(p.processIdentifier))")
            updateMenuState()
        } catch {
            log("Failed to start: \(error)")
            showError("Failed to start hidock-mic-trigger:\n\(error.localizedDescription)")
        }
    }

    @objc private func stopTrigger() {
        guard let p = process else { return }
        log("Stopping hidock-mic-trigger (pid \(p.processIdentifier))")
        p.interrupt()
        DispatchQueue.global().async { [weak self] in
            p.waitUntilExit()
            DispatchQueue.main.async {
                self?.log("Process stopped")
                self?.process = nil
                self?.processStartDate = nil
                self?.updateMenuState()
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

    private func showWindow() {
        if window == nil {
            let rect = NSRect(x: 0, y: 0, width: 380, height: 280)
            let style: NSWindow.StyleMask = [.titled, .closable, .miniaturizable]
            let win = NSWindow(contentRect: rect, styleMask: style, backing: .buffered, defer: false)
            win.center()
            win.title = "HiDock Mic Trigger"
            win.isReleasedWhenClosed = false
            win.delegate = self

            let contentView = win.contentView!

            let titleLabel = NSTextField(labelWithString: "HiDock Mic Trigger")
            titleLabel.font = .systemFont(ofSize: 18, weight: .semibold)
            titleLabel.frame = NSRect(x: 20, y: 228, width: 340, height: 30)
            contentView.addSubview(titleLabel)

            let status = NSTextField(labelWithString: "Stopped")
            status.font = .systemFont(ofSize: 14)
            status.textColor = .secondaryLabelColor
            status.frame = NSRect(x: 20, y: 200, width: 340, height: 22)
            contentView.addSubview(status)
            windowStatusLabel = status

            let uptime = NSTextField(labelWithString: "")
            uptime.font = .systemFont(ofSize: 12)
            uptime.textColor = .secondaryLabelColor
            uptime.frame = NSRect(x: 20, y: 178, width: 340, height: 18)
            contentView.addSubview(uptime)
            windowUptimeLabel = uptime

            let startBtn = NSButton(title: "Start", target: self, action: #selector(startTrigger))
            startBtn.bezelStyle = .rounded
            startBtn.frame = NSRect(x: 80, y: 130, width: 100, height: 32)
            contentView.addSubview(startBtn)
            windowStartButton = startBtn

            let stopBtn = NSButton(title: "Stop", target: self, action: #selector(stopTrigger))
            stopBtn.bezelStyle = .rounded
            stopBtn.frame = NSRect(x: 200, y: 130, width: 100, height: 32)
            contentView.addSubview(stopBtn)
            windowStopButton = stopBtn

            let separator = NSBox()
            separator.boxType = .separator
            separator.frame = NSRect(x: 20, y: 110, width: 340, height: 1)
            contentView.addSubview(separator)

            let autoStart = NSButton(checkboxWithTitle: "Auto-start on launch", target: self, action: #selector(toggleAutoStart))
            autoStart.frame = NSRect(x: 20, y: 75, width: 200, height: 22)
            contentView.addSubview(autoStart)
            windowAutoStartCheckbox = autoStart

            let logsBtn = NSButton(title: "Open Logs", target: self, action: #selector(showLogs))
            logsBtn.bezelStyle = .rounded
            logsBtn.frame = NSRect(x: 140, y: 28, width: 100, height: 32)
            contentView.addSubview(logsBtn)

            window = win
            updateWindowState()
        }
        NSApp.setActivationPolicy(.regular)
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
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
