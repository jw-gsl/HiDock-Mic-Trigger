import Foundation

/// Outcome of a publish run: success flag plus the script's summary line.
typealias PublishResult = (Bool, String)
/// Outcome of a status read: parsed state dict or an error message.
typealias StatusResult = ([String: Any]?, String)

/// Publishes transcript `.md` files to the private Transcripts GitHub repo.
///
/// Thin Swift front for `shared/transcript_publish.py` (single shared
/// implementation with the CLI).
///
/// **When a transcript is committed.** Not on every edit. Each change marks
/// the transcript as *settling*; it is committed once it has been quiet for
/// `settleSeconds` *and* nothing is still working on it (transcription,
/// re-diarisation, rename/merge, calendar lookup — anything the app is running
/// against that recording). A meeting suggestion still awaiting confirmation
/// holds it for up to `reviewHoldSeconds`, because confirming it re-runs
/// speaker matching. Everything that settles in the same tick goes into one
/// commit whose body lists each transcript's edits.
///
/// The settling set is persisted, so quitting (or a deploy killing the app)
/// never loses an edit: it settles on the next launch instead of blocking quit.
///
/// Failures (auth, network, public repo, diverged remote) are reported through
/// `onProblem` so the app can show them; the transcripts stay queued and are
/// retried after `retryBackoffSeconds`.
///
/// See docs/PLAN-transcripts-github-sync-2026-09-24.md.
final class TranscriptPublish {
    static let shared = TranscriptPublish()

    /// Kill-switch. Default **off** — nothing pushes until the user enables it.
    static let enabledKey = "publishTranscriptsToGitHub"

    var isEnabled: Bool {
        UserDefaults.standard.bool(forKey: Self.enabledKey)
    }

    let settleSeconds: TimeInterval = 10 * 60
    let reviewHoldSeconds: TimeInterval = 12 * 60 * 60
    let retryBackoffSeconds: TimeInterval = 15 * 60
    private let tickSeconds: TimeInterval = 60

    struct PendingTranscript: Codable, Equatable {
        var lastActivity: Date
        var reasons: [String]
    }

    // Main-thread state.
    private(set) var pending: [String: PendingTranscript] = [:]
    private var inFlight: Set<String> = []
    private var retryNotBefore: Date?
    private var timer: Timer?
    private var syncRunning = false

    /// Recording stems with app work in flight, keyed by count. Written from
    /// any thread (`runTranscription` callers), hence the lock.
    private let workLock = NSLock()
    private var activeWork: [String: Int] = [:]

    /// Supplied by the app: true while the transcript's meeting suggestion is
    /// awaiting confirmation (confirming it re-runs speaker matching).
    var isAwaitingReview: (String) -> Bool = { _ in false }
    /// Supplied by the app: true while it is doing anything else to the
    /// transcript that `activeWork` can't see (e.g. a calendar lookup).
    var isBusyElsewhere: (String) -> Bool = { _ in false }
    /// The transcripts folder `--all` mirrors.
    var transcriptsDir: () -> String = { "\(NSHomeDirectory())/HiDock/Raw Transcripts" }
    /// Called on main: a problem description, or nil once publishing works again.
    var onProblem: (String?) -> Void = { _ in }
    var log: (String) -> Void = { NSLog("TranscriptPublish: \($0)") }

    private let workQueue = DispatchQueue(label: "hidock.transcript-publish.run")

    private init() {}

    private static var pendingStoreURL: URL {
        URL(fileURLWithPath: "\(NSHomeDirectory())/HiDock/.transcripts-git.pending.json")
    }

    // MARK: - Lifecycle

    /// Load the persisted settling set and start the settle timer.
    func start() {
        dispatchPrecondition(condition: .onQueue(.main))
        if let data = try? Data(contentsOf: Self.pendingStoreURL),
           let saved = try? JSONDecoder().decode([String: PendingTranscript].self, from: data) {
            pending = saved
        }
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: tickSeconds, repeats: true) { [weak self] _ in
            self?.tick()
        }
    }

    private func persistPending() {
        let url = Self.pendingStoreURL
        if pending.isEmpty {
            try? FileManager.default.removeItem(at: url)
            return
        }
        if let data = try? JSONEncoder().encode(pending) {
            try? data.write(to: url, options: .atomic)
        }
    }

    // MARK: - Triggers

    /// A transcript changed (or is about to). Restarts its settle clock.
    /// - Parameter diarizedPath: the `_diarized.json` sidecar; its sibling
    ///   `.md` is what gets published.
    func schedule(diarizedPath: String, reason: String? = nil) {
        noteActivity(mdPath: Self.markdownPath(forDiarizedPath: diarizedPath), reason: reason)
    }

    func noteActivity(mdPath: String, reason: String?) {
        guard isEnabled else { return }
        let record = {
            var entry = self.pending[mdPath] ?? PendingTranscript(lastActivity: Date(), reasons: [])
            entry.lastActivity = Date()
            if let reason = reason?.trimmingCharacters(in: .whitespacesAndNewlines), !reason.isEmpty,
               entry.reasons.last != reason {
                entry.reasons.append(reason)
                if entry.reasons.count > 12 { entry.reasons.removeFirst(entry.reasons.count - 12) }
            }
            self.pending[mdPath] = entry
            self.persistPending()
        }
        if Thread.isMainThread { record() } else { DispatchQueue.main.async(execute: record) }
    }

    /// Mark the recordings named in a pipeline command as being worked on.
    /// Returns the stems to hand back to `endWork`.
    func beginWork(arguments: [String]) -> [String] {
        let stems = Self.recordingStems(in: arguments)
        guard !stems.isEmpty else { return [] }
        workLock.lock()
        for stem in stems { activeWork[stem, default: 0] += 1 }
        workLock.unlock()
        return stems
    }

    func endWork(_ stems: [String]) {
        guard !stems.isEmpty else { return }
        workLock.lock()
        for stem in stems {
            let remaining = (activeWork[stem] ?? 1) - 1
            activeWork[stem] = remaining > 0 ? remaining : nil
        }
        workLock.unlock()
    }

    private func hasActiveWork(_ stem: String) -> Bool {
        workLock.lock()
        defer { workLock.unlock() }
        return activeWork[stem] != nil
    }

    /// Recording stems referenced by a pipeline command's file arguments.
    static func recordingStems(in arguments: [String]) -> [String] {
        let extensions: Set<String> = ["mp3", "wav", "m4a", "json", "md", "srt", "hda"]
        var stems: [String] = []
        for argument in arguments where argument.hasPrefix("/") {
            let url = URL(fileURLWithPath: argument)
            guard extensions.contains(url.pathExtension.lowercased()) else { continue }
            var stem = url.deletingPathExtension().lastPathComponent
            for suffix in ["_diarized", "_calendar", "_calendar_suggestion"] where stem.hasSuffix(suffix) {
                stem = String(stem.dropLast(suffix.count))
            }
            if !stems.contains(stem) { stems.append(stem) }
        }
        return stems
    }

    // MARK: - Settling

    /// Which settling transcripts are ready to commit at `now`.
    func readyPaths(now: Date = Date()) -> [String] {
        pending.compactMap { path, entry -> String? in
            let stem = Self.stem(ofMarkdownPath: path)
            let quiet = now.timeIntervalSince(entry.lastActivity)
            guard quiet >= settleSeconds, !inFlight.contains(path),
                  !hasActiveWork(stem), !isBusyElsewhere(stem) else { return nil }
            if isAwaitingReview(stem) && quiet < reviewHoldSeconds { return nil }
            return path
        }.sorted()
    }

    private func tick() {
        guard isEnabled, !syncRunning else { return }
        if let retryNotBefore, Date() < retryNotBefore { return }
        let ready = readyPaths()
        guard !ready.isEmpty else { return }
        let entries = ready.compactMap { path in pending[path].map { (path, $0) } }
        let (title, body) = Self.commitMessage(for: entries)
        let snapshot = Dictionary(uniqueKeysWithValues: entries.map { ($0.0, $0.1.lastActivity) })
        inFlight.formUnion(ready)
        syncRunning = true
        runSync(mdPaths: ready, allMD: false, reason: title, body: body) { [weak self] ok, detail in
            DispatchQueue.main.async {
                guard let self else { return }
                self.syncRunning = false
                self.inFlight.subtract(ready)
                if ok {
                    // Keep anything edited again while the sync ran.
                    for (path, stamp) in snapshot where self.pending[path]?.lastActivity == stamp {
                        self.pending[path] = nil
                    }
                    self.persistPending()
                    self.retryNotBefore = nil
                    self.log("published \(ready.count) transcript(s): \(title)")
                    self.onProblem(nil)
                } else {
                    self.retryNotBefore = Date().addingTimeInterval(self.retryBackoffSeconds)
                    self.log("publish failed, retrying in \(Int(self.retryBackoffSeconds / 60)) min: \(detail)")
                    self.onProblem(detail)
                }
            }
        }
    }

    /// Retry now after a failure (banner button), skipping the backoff.
    func retryNow() {
        dispatchPrecondition(condition: .onQueue(.main))
        retryNotBefore = nil
        tick()
    }

    /// One commit for everything that settled together.
    static func commitMessage(for entries: [(String, PendingTranscript)]) -> (String, String) {
        let sorted = entries.sorted { $0.0 < $1.0 }
        let lines = sorted.map { path, entry -> String in
            let stem = stem(ofMarkdownPath: path)
            let reasons = entry.reasons.isEmpty ? "Updated" : entry.reasons.joined(separator: "; ")
            return "- \(stem): \(reasons)"
        }
        let title: String
        if sorted.count == 1, let only = sorted.first {
            let stem = stem(ofMarkdownPath: only.0)
            let reasons = only.1.reasons
            switch reasons.count {
            case 0: title = "\(stem): updated"
            case 1: title = "\(stem): \(reasons[0])"
            default: title = "\(stem): \(reasons.last!) (+\(reasons.count - 1) more edit\(reasons.count == 2 ? "" : "s"))"
            }
        } else {
            title = "Update \(sorted.count) transcripts"
        }
        return (title, lines.joined(separator: "\n"))
    }

    static func stem(ofMarkdownPath path: String) -> String {
        URL(fileURLWithPath: path).deletingPathExtension().lastPathComponent
    }

    // MARK: - Manual

    /// Publish every transcript now (menu / initial import), including any
    /// still settling. Completion is called on main.
    func syncNow(reason: String, completion: ((PublishResult) -> Void)? = nil) {
        dispatchPrecondition(condition: .onQueue(.main))
        let settling = Array(pending.keys)
        let stamps = pending.mapValues(\.lastActivity)
        runSync(mdPaths: settling, allMD: true, reason: reason, body: "") { [weak self] ok, detail in
            DispatchQueue.main.async {
                guard let self else { return }
                if ok {
                    for (path, stamp) in stamps where self.pending[path]?.lastActivity == stamp {
                        self.pending[path] = nil
                    }
                    self.persistPending()
                    self.retryNotBefore = nil
                }
                self.onProblem(ok ? nil : detail)
                completion?((ok, detail))
            }
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

    // MARK: - Python bridge

    /// Run one publish on the serial work queue. Completion is called on the
    /// work queue; callers hop to main.
    private func runSync(mdPaths: [String], allMD: Bool, reason: String, body: String,
                         completion: @escaping (PublishResult) -> Void) {
        let transcriptsDir = transcriptsDir()
        workQueue.async {
            guard let python = Self.pythonPath, FileManager.default.isExecutableFile(atPath: python) else {
                let message = "Transcript publish skipped: no Python found"
                NSLog("TranscriptPublish: \(message)")
                completion((false, message))
                return
            }

            var arguments = [Self.publishScriptPath, "--reason", reason,
                             "--transcripts-dir", transcriptsDir]
            if !body.isEmpty { arguments += ["--body", body] }
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
                completion((false, "launch failed: \(error.localizedDescription)"))
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
            // The script prints one JSON line; surface its human detail.
            var summary = out.isEmpty ? err : out
            if let data = out.data(using: .utf8),
               let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let detail = object["detail"] as? String, !detail.isEmpty {
                summary = detail
            }
            completion((process.terminationStatus == 0, summary))
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
