import Foundation

/// Outcome of a publish run: success flag plus the script's summary line.
typealias PublishResult = (Bool, String)
/// Outcome of a status read: parsed state dict or an error message.
typealias StatusResult = ([String: Any]?, String)

/// Publishes transcript `.md` files to the private Transcripts GitHub repo.
///
/// Thin Swift front for `shared/transcript_publish.py` (single shared
/// implementation with the CLI). Triggers are fired *after* a write and
/// debounced, so a burst of speaker edits lands as one commit instead of 15.
///
/// Best-effort by design — the same philosophy as the snapshot/rollback path:
/// a network or auth failure never surfaces mid-edit. State and errors live in
/// `~/HiDock/.transcripts-git/.hidock-publish-state.json` and are surfaced via
/// the Transcripts GitHub menu.
///
/// See docs/PLAN-transcripts-github-sync-2026-09-24.md.
final class TranscriptPublish {
    static let shared = TranscriptPublish()

    /// Kill-switch. Default **off** — nothing pushes until the user enables it.
    static let enabledKey = "publishTranscriptsToGitHub"

    var isEnabled: Bool {
        UserDefaults.standard.bool(forKey: Self.enabledKey)
    }

    private let debounceSeconds: TimeInterval = 45
    private let queue = DispatchQueue(label: "hidock.transcript-publish")
    private let workQueue = DispatchQueue(label: "hidock.transcript-publish.run")
    private var pendingMDPaths = Set<String>()
    private var pendingReason = "Transcript update"
    private var debounceWork: DispatchWorkItem?
    private var syncRunning = false
    private var queuedPaths = Set<String>()
    private var queuedAllMD = false
    private var queuedCompletions: [(PublishResult) -> Void] = []

    private init() {}

    // MARK: - Triggers

    /// Queue one transcript's markdown for a debounced publish.
    /// - Parameter diarizedPath: the `_diarized.json` sidecar just rewritten;
    ///   its sibling `.md` is what gets published.
    func schedule(diarizedPath: String, reason: String? = nil) {
        schedulePublish(mdPaths: [Self.markdownPath(forDiarizedPath: diarizedPath)], reason: reason)
    }

    func schedulePublish(mdPaths: [String], reason: String?) {
        guard isEnabled else { return }
        queue.async {
            self.pendingMDPaths.formUnion(mdPaths)
            if let reason, !reason.isEmpty {
                self.pendingReason = reason
            }
            self.debounceWork?.cancel()
            let work = DispatchWorkItem { [weak self] in self?.flush() }
            self.debounceWork = work
            self.queue.asyncAfter(deadline: .now() + self.debounceSeconds, execute: work)
        }
    }

    /// Publish now (menu command / app terminate). Completion is called on a
    /// background queue — hop to main for UI.
    func syncNow(reason: String, completion: ((PublishResult) -> Void)? = nil) {
        queue.async {
            self.debounceWork?.cancel()
            let paths = Array(self.pendingMDPaths)
            self.pendingMDPaths.removeAll()
            self.debounceWork = nil
            self.runSync(mdPaths: paths, allMD: true, reason: reason, completion: completion)
        }
    }

    /// Read last-sync state (JSON from the Python CLI) on a background queue.
    func status(completion: @escaping (StatusResult) -> Void) {
        workQueue.async {
            guard let python = Self.pythonPath else {
                DispatchQueue.main.async { completion((nil, "No Python found")) }
                return
            }
            let process = Process()
            process.currentDirectoryURL = URL(fileURLWithPath: Self.sharedDir)
            process.executableURL = URL(fileURLWithPath: python)
            process.arguments = [Self.publishScriptPath, "--status"]
            var env = ProcessInfo.processInfo.environment
            env["HOME"] = NSHomeDirectory()
            env["PYTHONPATH"] = Self.repoRoot
            process.environment = env
            let pipe = Pipe()
            process.standardOutput = pipe
            process.standardError = Pipe()
            do {
                try process.run()
            } catch {
                DispatchQueue.main.async { completion((nil, error.localizedDescription)) }
                return
            }
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            guard process.terminationStatus == 0,
                  let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                DispatchQueue.main.async { completion((nil, "Could not read publish state")) }
                return
            }
            DispatchQueue.main.async { completion((object, "")) }
        }
    }

    /// Best-effort final flush on app terminate — blocks up to ~20 s.
    func flushOnQuit() {
        guard isEnabled else { return }
        let semaphore = DispatchSemaphore(value: 0)
        syncNow(reason: "App terminate") { _ in semaphore.signal() }
        _ = semaphore.wait(timeout: .now() + 20)
    }

    private func flush() {
        let paths: [String]
        let reason: String
        paths = Array(pendingMDPaths)
        reason = pendingReason
        pendingMDPaths.removeAll()
        pendingReason = "Transcript update"
        runSync(mdPaths: paths, allMD: false, reason: reason, completion: nil)
    }

    // MARK: - Python bridge

    private func runSync(mdPaths: [String], allMD: Bool, reason: String,
                         completion: ((PublishResult) -> Void)?) {
        workQueue.async {
            if self.syncRunning {
                // One sync at a time; coalesce the request into the next run.
                self.queuedPaths.formUnion(mdPaths)
                self.queuedAllMD = self.queuedAllMD || allMD
                if let completion { self.queuedCompletions.append(completion) }
                return
            }
            self.syncRunning = true
            defer {
                self.syncRunning = false
                if !self.queuedPaths.isEmpty || self.queuedAllMD || !self.queuedCompletions.isEmpty {
                    let paths = Array(self.queuedPaths)
                    let all = self.queuedAllMD
                    let completions = self.queuedCompletions
                    self.queuedPaths.removeAll()
                    self.queuedAllMD = false
                    self.queuedCompletions.removeAll()
                    self.runSync(mdPaths: paths, allMD: all, reason: "Pending sync retry",
                                 completion: { result in
                        completions.forEach { $0(result) }
                    })
                }
            }

            guard let python = Self.pythonPath, FileManager.default.isExecutableFile(atPath: python) else {
                let message = "Transcript publish skipped: no Python found"
                NSLog("TranscriptPublish: \(message)")
                completion?((false, message))
                return
            }

            var arguments = [Self.publishScriptPath, "--reason", reason]
            if allMD { arguments.append("--all") }
            arguments.append(contentsOf: mdPaths)

            let process = Process()
            process.currentDirectoryURL = URL(fileURLWithPath: Self.sharedDir)
            process.executableURL = URL(fileURLWithPath: python)
            process.arguments = arguments

            var env = ProcessInfo.processInfo.environment
            env["HOME"] = NSHomeDirectory()
            env["PYTHONPATH"] = Self.repoRoot
            if env["PATH"] == nil || !env["PATH"]!.contains("/opt/homebrew") {
                env["PATH"] = "\(NSHomeDirectory())/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
            } else if let existing = env["PATH"], !existing.contains("/.local/bin") {
                env["PATH"] = "\(NSHomeDirectory())/.local/bin:" + existing
            }
            process.environment = env

            let outPipe = Pipe()
            let errPipe = Pipe()
            process.standardOutput = outPipe
            process.standardError = errPipe

            do {
                try process.run()
            } catch {
                DispatchQueue.main.async { completion?((false, "launch failed: \(error.localizedDescription)")) }
                return
            }

            // Drain before waiting — read-after-waitUntilExit can deadlock
            // once output exceeds the pipe buffer.
            let outData = outPipe.fileHandleForReading.readDataToEndOfFile()
            let errData = errPipe.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()

            let out = String(data: outData, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            let err = String(data: errData, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            let summary = out.isEmpty ? err : out
            // Completion is delivered on this work queue — callers hop to
            // main themselves. `flushOnQuit` blocks main, so never bounce.
            if process.terminationStatus == 0 {
                NSLog("TranscriptPublish: \(summary)")
                completion?((true, summary))
            } else {
                NSLog("TranscriptPublish (exit \(process.terminationStatus)): \(summary)")
                completion?((false, summary))
            }
        }
    }

    // MARK: - Paths (mirrors AppDelegate's resolution, kept self-contained)

    static var repoRoot: String {
        if let saved = UserDefaults.standard.string(forKey: "hidockRepoRoot"), !saved.isEmpty {
            return saved
        }
        return "\(NSHomeDirectory())/_git/hidock-tools"
    }

    static var bundledResourcesRoot: String? {
        guard let resPath = Bundle.main.resourcePath else { return nil }
        if FileManager.default.fileExists(atPath: "\(resPath)/usb-extractor/extractor.py") {
            return resPath
        }
        return nil
    }

    static var sharedDir: String {
        if let root = bundledResourcesRoot { return "\(root)/shared" }
        return "\(repoRoot)/shared"
    }

    static var publishScriptPath: String { "\(sharedDir)/transcript_publish.py" }

    static var pythonPath: String? {
        // Prefer the pipeline venv (both bundled and dev layouts); fall back
        // to the system python — transcript_publish is stdlib-only.
        let transcriptionRoot = bundledResourcesRoot.map { "\($0)/transcription-pipeline" }
            ?? "\(repoRoot)/transcription-pipeline"
        let venv = "\(transcriptionRoot)/.venv/bin/python3"
        if FileManager.default.isExecutableFile(atPath: venv) { return venv }
        return FileManager.default.isExecutableFile(atPath: "/usr/bin/python3")
            ? "/usr/bin/python3" : nil
    }

    static func markdownPath(forDiarizedPath diarizedPath: String) -> String {
        let url = URL(fileURLWithPath: diarizedPath)
        let name = url.deletingPathExtension().lastPathComponent
        let base = name.hasSuffix("_diarized") ? String(name.dropLast("_diarized".count)) : name
        return url.deletingLastPathComponent().appendingPathComponent(base).appendingPathExtension("md").path
    }
}
