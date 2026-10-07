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
    /// When publishing was switched on (epoch seconds). Transcripts never
    /// published and untouched since before then are left alone by the scan,
    /// so "Enable only" really means "from now on".
    static let enabledSinceKey = "publishTranscriptsToGitHubSince"
    /// The target repo as the user typed it (`owner/repo` or a URL). Shared
    /// with `transcript_publish.py`, which reads the same key for terminal runs.
    static let repoKey = "transcriptsGitHubRepo"

    static var configuredRepo: String? {
        let value = UserDefaults.standard.string(forKey: repoKey)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return (value?.isEmpty ?? true) ? nil : value
    }

    /// `owner/repo` from the forms the Repository field accepts, or nil for
    /// anything that isn't a GitHub repo.
    static func repoSlug(from input: String?) -> String? {
        var text = (input ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        for prefix in ["https://github.com/", "http://github.com/", "git@github.com:", "ssh://git@github.com/"]
            where text.lowercased().hasPrefix(prefix) {
            text = String(text.dropFirst(prefix.count))
        }
        while text.hasSuffix("/") { text.removeLast() }
        if text.hasSuffix(".git") { text.removeLast(4) }
        let parts = text.split(separator: "/", omittingEmptySubsequences: false)
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_."))
        guard parts.count == 2, parts.allSatisfy({ !$0.isEmpty && $0.unicodeScalars.allSatisfy(allowed.contains) })
        else { return nil }
        return "\(parts[0])/\(parts[1])"
    }

    /// Per-transcript publish state from the last scan, for the file list.
    struct FileStatus: Equatable {
        var state: String
        var commits: Int
    }

    var isEnabled: Bool {
        UserDefaults.standard.bool(forKey: Self.enabledKey)
    }

    let settleSeconds: TimeInterval = 10 * 60
    let reviewHoldSeconds: TimeInterval = 12 * 60 * 60
    let retryBackoffSeconds: TimeInterval = 15 * 60
    /// Reconciliation: catches edits made outside HiDock (Obsidian, an
    /// editor, a terminal run with publishing off), deletions, and anything a
    /// missed trigger would otherwise leave unpublished.
    let scanIntervalSeconds: TimeInterval = 30 * 60
    private let tickSeconds: TimeInterval = 60
    private var lastScan: Date?
    private var scanRunning = false

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
    /// Called on main whenever the settling set or scanned file states change:
    /// (states by stem, settling stems, GitHub web URL, branch).
    var onStatusChanged: ([String: FileStatus], Set<String>, String?, String) -> Void = { _, _, _, _ in }
    private(set) var fileStatus: [String: FileStatus] = [:]
    private(set) var webURL: String?
    private(set) var branch = "main"
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
        publishStatus()
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

    /// A change the app didn't make (found by the scan). Its settle clock
    /// starts at the file's own modification time, so an edit made hours ago
    /// is ready at once while one still being typed in Obsidian keeps waiting.
    func noteExternalChange(mdPath: String, modified: Date, reason: String) {
        dispatchPrecondition(condition: .onQueue(.main))
        guard isEnabled, pending[mdPath] == nil else { return }
        pending[mdPath] = PendingTranscript(lastActivity: modified, reasons: [reason])
        persistPending()
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

    /// Files can change without the app knowing (a step that names no file,
    /// an external editor). Any write to the transcript or its sidecar after
    /// the last known activity restarts the settle clock.
    private func absorbDiskChanges() {
        var changed = false
        for (path, entry) in pending {
            let latest = Self.latestModification(forMarkdownPath: path)
            if let latest, latest > entry.lastActivity.addingTimeInterval(1) {
                pending[path]?.lastActivity = latest
                changed = true
            }
        }
        if changed { persistPending() }
    }

    static func latestModification(forMarkdownPath path: String) -> Date? {
        let diarized = path.hasSuffix(".md") ? String(path.dropLast(3)) + "_diarized.json" : path
        return [path, diarized].compactMap {
            (try? FileManager.default.attributesOfItem(atPath: $0))?[.modificationDate] as? Date
        }.max()
    }

    private func tick() {
        guard isEnabled else { return }
        if !scanRunning, lastScan.map({ Date().timeIntervalSince($0) >= scanIntervalSeconds }) ?? true {
            scan()
        }
        guard !syncRunning else { return }
        absorbDiskChanges()
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
                    self.scan()
                } else {
                    self.retryNotBefore = Date().addingTimeInterval(self.retryBackoffSeconds)
                    self.log("publish failed, retrying in \(Int(self.retryBackoffSeconds / 60)) min: \(detail)")
                    self.onProblem(detail)
                }
            }
        }
    }

    // MARK: - Reconciliation scan

    /// Compare the transcripts folder with what's published (read-only), feed
    /// anything out of step into the settle queue, and refresh the per-file
    /// states the file list shows.
    func scan() {
        dispatchPrecondition(condition: .onQueue(.main))
        guard isEnabled, !scanRunning else { return }
        scanRunning = true
        lastScan = Date()
        var arguments = ["--scan", "--transcripts-dir", transcriptsDir()]
        if let repo = Self.configuredRepo { arguments += ["--remote", repo] }
        let since = UserDefaults.standard.double(forKey: Self.enabledSinceKey)
        if since > 0 { arguments += ["--since", String(since)] }
        runScript(arguments) { [weak self] object in
            DispatchQueue.main.async {
                guard let self else { return }
                self.scanRunning = false
                guard let object, object["ok"] as? Bool == true else {
                    self.log("scan failed")
                    return
                }
                self.applyScan(object)
            }
        }
    }

    private func applyScan(_ object: [String: Any]) {
        webURL = object["web_url"] as? String
        branch = object["branch"] as? String ?? "main"
        var states: [String: FileStatus] = [:]
        for (stem, value) in object["files"] as? [String: [String: Any]] ?? [:] {
            states[stem] = FileStatus(state: value["state"] as? String ?? "unpublished",
                                      commits: value["commits"] as? Int ?? 0)
        }
        fileStatus = states
        for path in object["changed"] as? [String] ?? [] {
            let modified = Self.latestModification(forMarkdownPath: path) ?? Date()
            let state = states[Self.stem(ofMarkdownPath: path)]?.state
            noteExternalChange(mdPath: path, modified: modified,
                               reason: state == "new" ? "Added" : "Edited outside HiDock")
        }
        for path in object["deleted"] as? [String] ?? [] {
            let superseded = states[Self.stem(ofMarkdownPath: path)]?.state == "excluded"
            noteExternalChange(mdPath: path, modified: .distantPast,
                               reason: superseded ? "Replaced by merged transcript" : "Removed transcript")
        }
        publishStatus()
    }

    private func publishStatus() {
        let settling = Set(pending.keys.map(Self.stem(ofMarkdownPath:)))
        onStatusChanged(fileStatus, settling, webURL, branch)
    }

    /// The transcript's page on GitHub, once it has been published.
    func githubURL(forStem stem: String, history: Bool = false) -> URL? {
        guard let webURL, (fileStatus[stem]?.commits ?? 0) > 0 else { return nil }
        let name = "\(stem).md".addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? stem
        return URL(string: "\(webURL)/\(history ? "commits" : "blob")/\(branch)/\(name)")
    }

    /// Run the publish script with extra arguments and parse its JSON output.
    private func runScript(_ extra: [String], completion: @escaping ([String: Any]?) -> Void) {
        workQueue.async {
            guard let python = Self.pythonPath else { completion(nil); return }
            let process = Process()
            process.currentDirectoryURL = URL(fileURLWithPath: Self.sharedDir)
            process.executableURL = URL(fileURLWithPath: python)
            process.arguments = [Self.publishScriptPath] + extra
            var env = ProcessInfo.processInfo.environment
            env["HOME"] = NSHomeDirectory()
            env["PYTHONPATH"] = Self.repoRoot
            process.environment = env
            let pipe = Pipe()
            process.standardOutput = pipe
            process.standardError = Pipe()
            do { try process.run() } catch { completion(nil); return }
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            completion(try? JSONSerialization.jsonObject(with: data) as? [String: Any])
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
                self.scan()
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

            guard let repo = Self.configuredRepo else {
                completion((false, "no repository set (Settings → Transcripts on GitHub → Repository)"))
                return
            }
            var arguments = [Self.publishScriptPath, "--remote", repo, "--reason", reason,
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
               let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                summary = Self.describe(result: object)
            }
            completion((process.terminationStatus == 0, summary))
        }
    }

    /// One readable line for a sync result (never the raw JSON).
    static func describe(result object: [String: Any]) -> String {
        if let detail = object["detail"] as? String, !detail.isEmpty, detail != "no changes" || object["ok"] as? Bool != true {
            return detail
        }
        let committed = object["committed"] as? Int ?? 0
        let pushed = object["pushed"] as? Bool ?? false
        let pending = object["pending"] as? Int ?? 0
        if committed > 0 && pushed { return "\(committed) commit pushed" }
        if pushed { return "pushed \(pending == 0 ? "earlier commits" : "")".trimmingCharacters(in: .whitespaces) }
        if committed > 0 { return "committed locally, \(pending) waiting to push" }
        return "nothing changed"
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
