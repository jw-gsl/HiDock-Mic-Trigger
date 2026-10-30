import AppKit
import Foundation
import UserNotifications

/// Updates a developer build from GitHub: the repo checkout it runs from is
/// fetched, compared with `origin/main`, and — when the reviewer agrees —
/// fast-forwarded and rebuilt with the usual deploy (which asks for approval
/// in the app before replacing it).
///
/// A developer build runs the pipeline from the checkout, so "update" means
/// pulling the code, not downloading the standalone release bundle (which is
/// a different app — see `UpdateChecker.isDeveloperBuild`).
enum RepoUpdater {
    struct Status {
        var branch = ""
        var behind = 0
        var ahead = 0
        /// Uncommitted changes, ignoring the files xcodegen regenerates.
        var dirtyFiles: [String] = []
        var remoteVersion: String?
        var remoteHead = ""
        /// Subjects of the commits that would be pulled, newest first.
        var incoming: [String] = []
        var error: String?

        /// Commits the checkout itself is behind (0 = only a rebuild is needed).
        var checkoutBehind = 0

        var canUpdate: Bool { error == nil && branch == "main" && behind > 0 && ahead == 0 && dirtyFiles.isEmpty }
    }

    /// Files every build regenerates from project.yml; restored before a pull.
    static let generatedFiles = [
        "hidock-mic-trigger/Info.plist",
        "hidock-mic-trigger/hidock-mic-trigger.xcodeproj/project.pbxproj",
    ]

    static var repoRoot: String {
        if let saved = UserDefaults.standard.string(forKey: "hidockRepoRoot"), !saved.isEmpty { return saved }
        return "\(NSHomeDirectory())/_git/hidock-tools"
    }

    /// The commit this app was built from (written by the deploy phase).
    static var installedCommit: String? {
        Bundle.main.infoDictionary?["HiDockGitCommit"] as? String
    }

    static var logPath: String { "\(NSHomeDirectory())/Library/Logs/hidock-self-update.log" }

    private static var env: [String: String] {
        var env = ProcessInfo.processInfo.environment
        env["HOME"] = NSHomeDirectory()
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        env["GIT_TERMINAL_PROMPT"] = "0"
        return env
    }

    @discardableResult
    private static func run(_ launch: String, _ args: [String], cwd: String? = nil,
                            extraEnv: [String: String] = [:], log: FileHandle? = nil) -> (Int32, String) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: launch)
        p.arguments = args
        p.currentDirectoryURL = URL(fileURLWithPath: cwd ?? repoRoot)
        p.environment = env.merging(extraEnv) { $1 }
        if let log {
            // Straight to the log file, never through a pipe to this app: the
            // deploy quits the app mid-build, and a write to a pipe with no
            // reader would kill the installer half way (SIGPIPE).
            p.standardOutput = log
            p.standardError = log
            do { try p.run() } catch { return (-1, "\(launch): \(error.localizedDescription)") }
            p.waitUntilExit()
            return (p.terminationStatus, (try? String(contentsOfFile: logPath, encoding: .utf8)) ?? "")
        }
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        var output = Data()
        pipe.fileHandleForReading.readabilityHandler = { h in
            let chunk = h.availableData
            if !chunk.isEmpty { output.append(chunk) }
        }
        do { try p.run() } catch { return (-1, "\(launch): \(error.localizedDescription)") }
        p.waitUntilExit()
        pipe.fileHandleForReading.readabilityHandler = nil
        output.append(pipe.fileHandleForReading.readDataToEndOfFile())
        return (p.terminationStatus, String(decoding: output, as: UTF8.self))
    }

    private static func git(_ args: [String]) -> (Int32, String) {
        run("/usr/bin/git", ["-C", repoRoot] + args)
    }

    /// Fetch and compare with origin/main. Call off the main thread.
    static func status() -> Status {
        var s = Status()
        guard FileManager.default.fileExists(atPath: "\(repoRoot)/.git") else {
            s.error = "No hidock-tools checkout at \(repoRoot)."
            return s
        }
        let fetch = git(["fetch", "--quiet", "origin", "main"])
        if fetch.0 != 0 {
            s.error = "Couldn't reach GitHub: \(fetch.1.trimmingCharacters(in: .whitespacesAndNewlines).suffix(200))"
            return s
        }
        s.branch = git(["rev-parse", "--abbrev-ref", "HEAD"]).1.trimmingCharacters(in: .whitespacesAndNewlines)
        let counts = git(["rev-list", "--left-right", "--count", "HEAD...origin/main"]).1
            .split(whereSeparator: \.isWhitespace).compactMap { Int($0) }
        if counts.count == 2 { s.ahead = counts[0]; s.checkoutBehind = counts[1] }
        // Behind = what the *installed app* lacks. The checkout can be current
        // (pulled by hand) while the app was built from an older commit.
        let base = installedCommit.flatMap { c in git(["cat-file", "-e", "\(c)^{commit}"]).0 == 0 ? c : nil } ?? "HEAD"
        s.behind = Int(git(["rev-list", "--count", "\(base)..origin/main"]).1
            .trimmingCharacters(in: .whitespacesAndNewlines)) ?? s.checkoutBehind
        s.remoteHead = git(["rev-parse", "--short", "origin/main"]).1.trimmingCharacters(in: .whitespacesAndNewlines)
        s.dirtyFiles = git(["status", "--porcelain", "--untracked-files=no"]).1
            .split(separator: "\n").map { String($0.dropFirst(3)) }
            .filter { !generatedFiles.contains($0) }
        let yml = git(["show", "origin/main:hidock-mic-trigger/project.yml"]).1
        s.remoteVersion = yml.split(separator: "\n")
            .first { $0.contains("MARKETING_VERSION:") }
            .map { $0.split(separator: ":").last.map { $0.trimmingCharacters(in: .whitespaces) } ?? "" }
        if s.behind > 0 {
            s.incoming = git(["log", "--format=%s", "--first-parent", "-n", "8", "\(base)..origin/main"]).1
                .split(separator: "\n").map(String.init)
        }
        return s
    }

    /// Pull origin/main and rebuild + deploy. Runs off the main thread; the
    /// deploy asks for approval in this app, then quits and relaunches it, so
    /// on success `completion` usually never runs. Called with an error text
    /// otherwise.
    static func update(completion: @escaping (String?) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async {
            FileManager.default.createFile(atPath: logPath, contents: nil)
            let log = FileHandle(forWritingAtPath: logPath)
            defer { try? log?.close() }
            func fail(_ message: String) { DispatchQueue.main.async { completion(message) } }
            log?.write(Data("== Update from GitHub, \(Date())\n".utf8))

            let before = git(["rev-parse", "HEAD"]).1.trimmingCharacters(in: .whitespacesAndNewlines)
            _ = run("/usr/bin/git", ["-C", repoRoot, "checkout", "--"] + generatedFiles, log: log)
            let pull = run("/usr/bin/git", ["-C", repoRoot, "pull", "--ff-only", "origin", "main"], log: log)
            guard pull.0 == 0 else { return fail("git pull failed. See \(logPath).") }
            // Packages may have changed since the installed app was built, even
            // when the checkout was already current.
            let since = installedCommit ?? before

            // New pipeline packages arrive with the code; install them so the
            // transcription pipeline doesn't break on an import.
            let changed = git(["diff", "--name-only", since, "HEAD"]).1
            if changed.contains("transcription-pipeline/requirements.txt") {
                let pip = "\(repoRoot)/transcription-pipeline/.venv/bin/python3"
                let r = run(pip, ["-m", "pip", "install", "-q", "-r",
                                  "\(repoRoot)/transcription-pipeline/requirements.txt"], log: log)
                if r.0 != 0 { log?.write(Data("pip install failed; the setup check will flag what's missing\n".utf8)) }
            }

            let project = "\(repoRoot)/hidock-mic-trigger"
            let gen = run("/opt/homebrew/bin/xcodegen", ["generate", "--spec", "\(project)/project.yml"],
                          cwd: project, log: log)
            guard gen.0 == 0 else { return fail("xcodegen failed. See \(logPath).") }
            // A private build folder: /tmp/hidock-build is shared with other
            // builds on this Mac and a parallel one can swap the product.
            let derived = "\(NSHomeDirectory())/Library/Caches/HiDock/SelfUpdateBuild"
            let build = run("/usr/bin/xcodebuild",
                            ["-project", "\(project)/hidock-mic-trigger.xcodeproj", "-scheme", "hidock-mic-trigger",
                             "-configuration", "Debug", "-derivedDataPath", derived, "build"],
                            cwd: project, extraEnv: ["HIDOCK_DEPLOY": "1"], log: log)
            _ = run("/usr/bin/git", ["-C", repoRoot, "checkout", "--"] + generatedFiles, log: log)
            if build.0 != 0 { return fail("The build failed. See \(logPath).") }
            if build.1.contains("Deployment declined") { return fail("Update cancelled; the code is pulled and will be installed next time.") }
            DispatchQueue.main.async { completion(nil) }
        }
    }

    // MARK: - UI

    /// Check for Updates in a developer build.
    static func manualCheck(currentVersion: String) {
        UpdateChecker.onStatusUpdate?("Checking GitHub…")
        DispatchQueue.global(qos: .userInitiated).async {
            let s = status()
            let latestRelease = UpdateChecker.latestReleaseVersion()
            DispatchQueue.main.async {
                UpdateChecker.onStatusUpdate?("")
                showAlert(s, currentVersion: currentVersion, latestRelease: latestRelease)
            }
        }
    }

    private static func showAlert(_ s: Status, currentVersion: String, latestRelease: String?) {
        let alert = NSAlert()
        var lines = ["You're running \(currentVersion), a developer build from \((repoRoot as NSString).abbreviatingWithTildeInPath)."]
        if let latestRelease { lines.append("Latest release on GitHub: \(latestRelease).") }
        if let error = s.error {
            alert.messageText = "Couldn't check for updates"
            lines.append(error)
        } else if s.behind == 0 {
            alert.messageText = "HiDock is up to date"
            lines.append("This app is built from GitHub's latest main (\(s.remoteHead)).")
        } else {
            alert.messageText = "\(s.behind) new change\(s.behind == 1 ? "" : "s") on GitHub"
            if let v = s.remoteVersion, v != currentVersion { lines.append("main is at \(v).") }
            lines.append(s.incoming.map { "• \($0)" }.joined(separator: "\n")
                         + (s.behind > s.incoming.count ? "\n• …and \(s.behind - s.incoming.count) more" : ""))
            if s.branch != "main" {
                lines.append("The checkout is on '\(s.branch)', not main, so it won't be updated automatically.")
            } else if s.ahead > 0 {
                lines.append("The checkout has \(s.ahead) commit\(s.ahead == 1 ? "" : "s") GitHub doesn't, so it can't fast-forward.")
            } else if !s.dirtyFiles.isEmpty {
                lines.append("Uncommitted changes in: \(s.dirtyFiles.prefix(4).joined(separator: ", ")). Commit or discard them first.")
            } else {
                lines.append(s.checkoutBehind > 0
                    ? "Update now pulls them and rebuilds. You'll be asked to approve the restart, which waits for anything that's running."
                    : "Your checkout already has them; Update now rebuilds the app. You'll be asked to approve the restart, which waits for anything that's running.")
            }
        }
        alert.informativeText = lines.joined(separator: "\n\n")
        if s.canUpdate {
            alert.addButton(withTitle: "Update now")
            alert.addButton(withTitle: "Later")
        } else {
            alert.addButton(withTitle: "OK")
        }
        guard alert.runModal() == .alertFirstButtonReturn, s.canUpdate else { return }
        UpdateChecker.onStatusUpdate?("Updating from GitHub…")
        update { error in
            UpdateChecker.onStatusUpdate?("")
            guard let error else { return }
            let failed = NSAlert()
            failed.messageText = "Update didn't finish"
            failed.informativeText = error
            failed.runModal()
        }
    }

    /// At launch: if GitHub has new changes, say so in the footer, and notify
    /// once per new GitHub head.
    static func backgroundCheck() {
        DispatchQueue.global(qos: .utility).async {
            let s = status()
            guard s.error == nil, s.behind > 0 else { return }
            DispatchQueue.main.async {
                UpdateChecker.onStatusUpdate?("\(s.behind) update\(s.behind == 1 ? "" : "s") on GitHub — Check for Updates")
                let key = "repoUpdaterNotifiedHead"
                guard UserDefaults.standard.string(forKey: key) != s.remoteHead else { return }
                UserDefaults.standard.set(s.remoteHead, forKey: key)
                let content = UNMutableNotificationContent()
                content.title = "HiDock update on GitHub"
                content.body = "\(s.behind) new change\(s.behind == 1 ? "" : "s"). Use Check for Updates to install."
                UNUserNotificationCenter.current().add(
                    UNNotificationRequest(identifier: "repo-update-\(s.remoteHead)", content: content, trigger: nil))
            }
        }
    }
}
