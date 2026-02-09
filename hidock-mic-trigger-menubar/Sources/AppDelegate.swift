import AppKit

@main
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private let menu = NSMenu()
    private var startItem: NSMenuItem!
    private var stopItem: NSMenuItem!
    private var autoStartItem: NSMenuItem!
    private var logsItem: NSMenuItem!
    private var process: Process?
    private var window: NSWindow?

    private let repoRoot = "/Users/jameswhiting/_git/hidock-tools"
    private lazy var binaryPath: String = {
        if let override = ProcessInfo.processInfo.environment["HIDOCK_MIC_TRIGGER_PATH"], !override.isEmpty {
            return override
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

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        setupMainMenu()
        setupStatusItem()
        setDockIcon()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) { [weak self] in
            self?.showWindow()
            self?.showStartupAlert()
        }
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

    private func setupStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.title = "HiDock"
        statusItem.button?.image = statusImage(running: false)
        statusItem.button?.imagePosition = .imageLeft

        startItem = NSMenuItem(title: "Start", action: #selector(startTrigger), keyEquivalent: "s")
        stopItem = NSMenuItem(title: "Stop", action: #selector(stopTrigger), keyEquivalent: "t")
        autoStartItem = NSMenuItem(title: "Auto-start on launch", action: #selector(toggleAutoStart), keyEquivalent: "")
        logsItem = NSMenuItem(title: "Show Logs", action: #selector(showLogs), keyEquivalent: "l")
        let statusInfoItem = NSMenuItem(title: "Show Status", action: #selector(showStatus), keyEquivalent: "i")
        let quitItem = NSMenuItem(title: "Quit", action: #selector(quitApp), keyEquivalent: "q")

        startItem.target = self
        stopItem.target = self
        autoStartItem.target = self
        logsItem.target = self
        statusInfoItem.target = self
        quitItem.target = self

        menu.addItem(startItem)
        menu.addItem(stopItem)
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
        appMenu.addItem(NSMenuItem(title: "Quit", action: #selector(quitApp), keyEquivalent: "q"))
        appMenuItem.submenu = appMenu

        NSApp.mainMenu = mainMenu
    }

    private func statusImage(running: Bool) -> NSImage? {
        let name = running ? "waveform" : "waveform.slash"
        let image = NSImage(systemSymbolName: name, accessibilityDescription: nil)
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

    @objc private func startTrigger() {
        guard process == nil else { return }
        if !FileManager.default.isExecutableFile(atPath: binaryPath) {
            if !buildTriggerBinary() {
                showError("Binary not found and build failed.\nExpected: \(binaryPath)")
                return
            }
        }

        let p = Process()
        p.executableURL = URL(fileURLWithPath: binaryPath)
        p.standardOutput = Pipe()
        p.standardError = Pipe()
        p.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async {
                self?.process = nil
                self?.updateMenuState()
            }
        }

        do {
            try p.run()
            process = p
            updateMenuState()
        } catch {
            showError("Failed to start hidock-mic-trigger: \(error)")
        }
    }

    @objc private func stopTrigger() {
        guard let p = process else { return }
        p.terminate()
        process = nil
        updateMenuState()
    }

    @objc private func toggleAutoStart() {
        autoStartOnLaunch.toggle()
        updateMenuState()
        if autoStartOnLaunch && process == nil {
            startTrigger()
        }
    }

    @objc private func showLogs() {
        let logPath = "\(NSHomeDirectory())/Library/Logs/mic-trigger.log"
        let errPath = "\(NSHomeDirectory())/Library/Logs/mic-trigger.err"
        if FileManager.default.fileExists(atPath: logPath) {
            NSWorkspace.shared.open(URL(fileURLWithPath: logPath))
        } else if FileManager.default.fileExists(atPath: errPath) {
            NSWorkspace.shared.open(URL(fileURLWithPath: errPath))
        } else {
            showError("No log files found yet.\nExpected:\n\(logPath)\n\(errPath)")
        }
    }

    @objc private func showStatus() {
        let running = (process != nil)
        let message = running ? "hidock-mic-trigger is running." : "hidock-mic-trigger is not running."
        let alert = NSAlert()
        alert.alertStyle = .informational
        alert.messageText = "Status"
        alert.informativeText = message
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

    private func showStartupAlert() {
        let alert = NSAlert()
        alert.alertStyle = .informational
        alert.messageText = "HiDock Mic Trigger"
        alert.informativeText = "App launched. If you see this, UI is working."
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    private func setDockIcon() {
        guard let base = NSImage(systemSymbolName: "waveform", accessibilityDescription: nil) else { return }
        let size = NSSize(width: 512, height: 512)
        let image = NSImage(size: size)
        image.lockFocus()
        NSColor.clear.set()
        NSRect(origin: .zero, size: size).fill()
        base.size = NSSize(width: 256, height: 256)
        base.draw(in: NSRect(x: 128, y: 128, width: 256, height: 256))
        image.unlockFocus()
        NSApplication.shared.applicationIconImage = image
    }

    @objc private func quitApp() {
        NSApp.terminate(nil)
    }

    private func showError(_ message: String) {
        let alert = NSAlert()
        alert.alertStyle = .critical
        alert.messageText = "hidock-mic-trigger"
        alert.informativeText = message
        alert.runModal()
    }

    private func buildTriggerBinary() -> Bool {
        let dir = "\(repoRoot)/mic-trigger"
        let source = sourcePath
        guard FileManager.default.fileExists(atPath: source) else { return false }

        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        p.currentDirectoryURL = URL(fileURLWithPath: dir)
        p.arguments = ["swiftc", "MicTrigger.swift", "-o", "hidock-mic-trigger"]
        p.standardOutput = Pipe()
        p.standardError = Pipe()

        do {
            try p.run()
            p.waitUntilExit()
        } catch {
            return false
        }

        return p.terminationStatus == 0 && FileManager.default.isExecutableFile(atPath: binaryPath)
    }
}
