import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private let menu = NSMenu()
    private var startItem: NSMenuItem!
    private var stopItem: NSMenuItem!
    private var autoStartItem: NSMenuItem!
    private var process: Process?
    private var window: NSWindow?

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
        NSApp.setActivationPolicy(.regular)
        setupMainMenu()
        setupStatusItem()
        setDockIcon()
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
        statusItem.button?.title = running ? "HiDock*" : "HiDock"
    }

    // MARK: - Process management

    @objc private func startTrigger() {
        guard process == nil else { return }

        if !FileManager.default.isExecutableFile(atPath: binaryPath) {
            log("Binary not found at \(binaryPath), attempting build...")
            startItem.isEnabled = false
            buildTriggerBinaryAsync { [weak self] success in
                guard let self = self else { return }
                if success {
                    self.log("Build succeeded, starting trigger")
                    self.launchProcess()
                } else {
                    self.startItem.isEnabled = true
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
                self?.updateMenuState()
            }
        }

        do {
            try p.run()
            process = p
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
        let running = (process != nil)
        let pid = process.map { " (pid \($0.processIdentifier))" } ?? ""
        let message = running
            ? "hidock-mic-trigger is running\(pid)."
            : "hidock-mic-trigger is not running."
        let alert = NSAlert()
        alert.alertStyle = .informational
        alert.messageText = "Status"
        alert.informativeText = message
        alert.runModal()
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
            let rect = NSRect(x: 0, y: 0, width: 380, height: 180)
            let style: NSWindow.StyleMask = [.titled, .closable, .miniaturizable]
            let win = NSWindow(contentRect: rect, styleMask: style, backing: .buffered, defer: false)
            win.center()
            win.title = "HiDock Mic Trigger"
            let label = NSTextField(labelWithString: "HiDock Mic Trigger is running.\nUse the menu bar or Dock menu to Start/Stop.")
            label.frame = NSRect(x: 20, y: 70, width: 340, height: 60)
            label.alignment = .left
            win.contentView?.addSubview(label)
            window = win
        }
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    // MARK: - Dock icon

    private func setDockIcon() {
        guard let base = NSImage(systemSymbolName: "waveform", accessibilityDescription: "HiDock") else { return }
        let size = NSSize(width: 512, height: 512)
        let image = NSImage(size: size, flipped: false) { _ in
            base.draw(in: NSRect(x: 128, y: 128, width: 256, height: 256))
            return true
        }
        NSApp.applicationIconImage = image
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
