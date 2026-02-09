import AppKit

@main
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private let menu = NSMenu()
    private var startItem: NSMenuItem!
    private var stopItem: NSMenuItem!
    private var process: Process?

    private let binaryPath = "/Users/jameswhiting/_git/hidock-tools/mic-trigger/hidock-mic-trigger"

    func applicationDidFinishLaunching(_ notification: Notification) {
        setupStatusItem()
        startTrigger()
    }

    func applicationWillTerminate(_ notification: Notification) {
        stopTrigger()
    }

    private func setupStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.image = statusImage(running: false)
        statusItem.button?.imagePosition = .imageOnly

        startItem = NSMenuItem(title: "Start", action: #selector(startTrigger), keyEquivalent: "s")
        stopItem = NSMenuItem(title: "Stop", action: #selector(stopTrigger), keyEquivalent: "t")
        let quitItem = NSMenuItem(title: "Quit", action: #selector(quitApp), keyEquivalent: "q")

        startItem.target = self
        stopItem.target = self
        quitItem.target = self

        menu.addItem(startItem)
        menu.addItem(stopItem)
        menu.addItem(NSMenuItem.separator())
        menu.addItem(quitItem)

        statusItem.menu = menu
        updateMenuState()
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
        statusItem.button?.image = statusImage(running: running)
    }

    @objc private func startTrigger() {
        guard process == nil else { return }
        guard FileManager.default.isExecutableFile(atPath: binaryPath) else {
            showError("Binary not found at: \(binaryPath)")
            return
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
}
