import Foundation
import UserNotifications
import AppKit

final class UpdateChecker {
    private static let lastCheckedVersionKey = "hidockLastCheckedUpdateVersion"
    static let updateURLKey = "hidockPendingUpdateURL"
    static let updateActionID = "DOWNLOAD_UPDATE"
    static let updateCategoryID = "UPDATE_AVAILABLE"

    struct GitHubRelease: Decodable {
        let tag_name: String
        let name: String
        let html_url: String
        let assets: [Asset]

        struct Asset: Decodable {
            let name: String
            let browser_download_url: String
        }
    }

    /// Checks GitHub for a newer release. Calls `completion` on the main thread only if an update is available.
    static func checkForUpdate(completion: @escaping (_ title: String, _ body: String, _ release: GitHubRelease) -> Void) {
        guard let currentVersion = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String else {
            return
        }

        fetchLatestRelease { release in
            guard let release = release else { return }
            let remoteVersion = release.tag_name.hasPrefix("v")
                ? String(release.tag_name.dropFirst())
                : release.tag_name

            guard isVersion(remoteVersion, newerThan: currentVersion) else { return }

            let lastChecked = UserDefaults.standard.string(forKey: lastCheckedVersionKey)
            if lastChecked == remoteVersion { return }
            UserDefaults.standard.set(remoteVersion, forKey: lastCheckedVersionKey)

            DispatchQueue.main.async {
                completion(
                    "Update Available: \(release.name)",
                    "Version \(remoteVersion) is available (you have \(currentVersion)).",
                    release
                )
            }
        }
    }

    /// Manual check — always shows a result.
    static func manualCheck() {
        guard let currentVersion = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String else {
            showUpToDateAlert()
            return
        }

        fetchLatestRelease { release in
            DispatchQueue.main.async {
                guard let release = release else {
                    showUpToDateAlert()
                    return
                }

                let remoteVersion = release.tag_name.hasPrefix("v")
                    ? String(release.tag_name.dropFirst())
                    : release.tag_name

                if isVersion(remoteVersion, newerThan: currentVersion) {
                    showUpdateAlert(
                        title: "Update Available: \(release.name)",
                        body: "Version \(remoteVersion) is available (you have \(currentVersion)).",
                        release: release
                    )
                } else {
                    showUpToDateAlert()
                }
            }
        }
    }

    // MARK: - GitHub API

    private static func fetchLatestRelease(completion: @escaping (GitHubRelease?) -> Void) {
        let urlString = "https://api.github.com/repos/jw-gsl/HiDock-Mic-Trigger/releases/latest"
        guard let url = URL(string: urlString) else { completion(nil); return }

        var request = URLRequest(url: url)
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        request.timeoutInterval = 15

        URLSession.shared.dataTask(with: request) { data, response, error in
            guard let data = data, error == nil,
                  let release = try? JSONDecoder().decode(GitHubRelease.self, from: data) else {
                completion(nil)
                return
            }
            completion(release)
        }.resume()
    }

    // MARK: - UI

    /// Pending update to install on quit
    private static var pendingRelease: GitHubRelease?
    private static var pendingZipPath: URL?

    static func showUpdateAlert(title: String, body: String, release: GitHubRelease) {
        let alert = NSAlert()
        alert.alertStyle = .informational
        alert.messageText = title
        alert.informativeText = body
        alert.addButton(withTitle: "Restart & Update")     // button 0 (1000)
        alert.addButton(withTitle: "Update on Quit")        // button 1 (1001)
        alert.addButton(withTitle: "Skip this version")      // button 2 (1002)
        alert.icon = NSImage(systemSymbolName: "arrow.down.circle", accessibilityDescription: "Update")

        let response = alert.runModal()
        if response == .alertFirstButtonReturn {
            // Restart & Update — download, install, relaunch now
            downloadAndInstall(release: release, relaunch: true)
        } else if response == .alertSecondButtonReturn {
            // Update on Quit — download now, install when app quits
            downloadForLater(release: release)
        }
    }

    /// A callback to update status bar text. Set by AppDelegate.
    static var onStatusUpdate: ((String) -> Void)?

    /// Whether a download is currently in progress.
    private static var isDownloading = false

    /// Retains the download-progress KVO observation for the active download.
    /// NSKeyValueObservation invalidates itself on deinit, so a function-local
    /// `let observation = …` dies as soon as the function returns and the
    /// progress UI never updates. Cleared when the download completes.
    private static var progressObservation: NSKeyValueObservation?

    /// Install pending update if one was downloaded. Call from applicationWillTerminate.
    /// Does NOT relaunch — next open gets the new version.
    static func installPendingUpdateIfNeeded() {
        // If download is still in progress, skip gracefully
        guard !isDownloading, let zipPath = pendingZipPath else {
            if isDownloading {
                NSLog("Update download still in progress, skipping install on quit")
                // Clean up partial download
                if let zip = pendingZipPath {
                    try? FileManager.default.removeItem(at: zip.deletingLastPathComponent())
                }
            }
            return
        }

        let appPath = Bundle.main.bundlePath
        let tempDir = zipPath.deletingLastPathComponent()
        let extractDir = tempDir.appendingPathComponent("extracted")

        // Install synchronously during quit — no relaunch
        let script = """
        #!/bin/bash
        set -euo pipefail
        sleep 1
        mkdir -p "\(extractDir.path)"
        ditto -x -k "\(zipPath.path)" "\(extractDir.path)"
        APP=$(find "\(extractDir.path)" -maxdepth 2 -name "*.app" -type d | head -1)
        if [ -z "$APP" ]; then
            echo "Update failed: could not find app in download."
            exit 1
        fi

        sign_and_verify() {
            local candidate="$1"
            # Preserve a valid Developer ID signature from a notarized release.
            if codesign --verify --deep --strict "$candidate" >/dev/null 2>&1 \
              && codesign -dv --verbose=4 "$candidate" 2>&1 \
                | grep -q "Authority=Developer ID Application:"; then
                echo "Using the existing Developer ID signature"
            else
                local sign_id="${HIDOCK_SIGNING_IDENTITY:-}"
                if [ -z "$sign_id" ]; then
                    sign_id=$(security find-identity -v -p codesigning 2>/dev/null \
                      | awk -F'"' '/Developer ID Application:/ {print $2; exit}')
                fi
                case "$sign_id" in
                    "Developer ID Application:"*) ;;
                    *)
                        echo "Update failed: no valid Developer ID Application identity found."
                        exit 1
                        ;;
                esac
                echo "Signing update with: $sign_id"
                codesign --force --deep --sign "$sign_id" "$candidate"
            fi
            codesign --verify --deep --strict --verbose=2 "$candidate"
        }

        # Stage and verify before replacing the live app. A failed signature
        # must leave the current working installation untouched.
        STAGE_DIR=$(mktemp -d "${TMPDIR:-/tmp}/hidock-update.XXXXXX")
        trap 'rm -rf "$STAGE_DIR"' EXIT
        STAGED_APP="$STAGE_DIR/HiDock Mic Trigger.app"
        cp -R "$APP" "$STAGED_APP"
        sign_and_verify "$STAGED_APP"

        rm -rf "\(appPath)"
        cp -R "$STAGED_APP" "\(appPath)"
        codesign --verify --deep --strict --verbose=2 "\(appPath)"
        # Re-register with LaunchServices so Launchpad picks it up
        /System/Library/Frameworks/CoreServices.framework/Versions/Current/Frameworks/LaunchServices.framework/Versions/Current/Support/lsregister -f "\(appPath)" 2>/dev/null
        rm -rf "\(tempDir.path)"
        """

        let scriptPath = tempDir.appendingPathComponent("update-on-quit.sh")
        try? script.write(to: scriptPath, atomically: true, encoding: .utf8)
        try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: scriptPath.path)

        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/bash")
        process.arguments = [scriptPath.path]
        process.standardOutput = nil
        process.standardError = nil
        try? process.run()
    }

    /// Download update in background with status bar progress. No modal windows.
    private static func downloadForLater(release: GitHubRelease) {
        guard let asset = release.assets.first(where: { $0.name.contains("macOS") && $0.name.hasSuffix(".zip") }),
              let downloadURL = URL(string: asset.browser_download_url) else { return }

        let tempDir = FileManager.default.temporaryDirectory.appendingPathComponent("hidock-update-\(UUID().uuidString)")
        try? FileManager.default.createDirectory(at: tempDir, withIntermediateDirectories: true)
        let zipPath = tempDir.appendingPathComponent("update.zip")

        isDownloading = true
        onStatusUpdate?("Downloading update...")

        let task = URLSession.shared.downloadTask(with: downloadURL) { tempURL, response, error in
            DispatchQueue.main.async {
                isDownloading = false
                progressObservation = nil

                guard let tempURL = tempURL, error == nil else {
                    NSLog("Update download failed: \(error?.localizedDescription ?? "unknown")")
                    onStatusUpdate?("")
                    // Clean up
                    try? FileManager.default.removeItem(at: tempDir)
                    return
                }

                do {
                    try FileManager.default.moveItem(at: tempURL, to: zipPath)
                    pendingRelease = release
                    pendingZipPath = zipPath
                    NSLog("Update downloaded and ready to install on quit")
                    onStatusUpdate?("Update ready — will install on quit")
                } catch {
                    NSLog("Failed to save update: \(error)")
                    onStatusUpdate?("")
                    try? FileManager.default.removeItem(at: tempDir)
                }
            }
        }

        // Held in the static so it survives this function returning —
        // see progressObservation.
        progressObservation = task.progress.observe(\.fractionCompleted) { progress, _ in
            DispatchQueue.main.async {
                let mb = Double(task.countOfBytesReceived) / (1024 * 1024)
                let total = Double(task.countOfBytesExpectedToReceive) / (1024 * 1024)
                if total > 0 {
                    onStatusUpdate?(String(format: "Downloading update... %.0f/%.0f MB", mb, total))
                }
            }
        }

        task.resume()
    }

    static func showUpToDateAlert() {
        let currentVersion = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0.0"
        let alert = NSAlert()
        alert.alertStyle = .informational
        alert.messageText = "You're up to date"
        alert.informativeText = "HiDock Mic Trigger \(currentVersion) is the latest version."
        alert.addButton(withTitle: "OK")
        alert.runModal()
    }

    // MARK: - Auto-Update

    private static func downloadAndInstall(release: GitHubRelease, relaunch: Bool = true) {
        // Find the macOS zip asset
        guard let asset = release.assets.first(where: { $0.name.contains("macOS") && $0.name.hasSuffix(".zip") }),
              let downloadURL = URL(string: asset.browser_download_url) else {
            let alert = NSAlert()
            alert.messageText = "Update Failed"
            alert.informativeText = "Could not find macOS download in this release."
            alert.runModal()
            return
        }

        // Show progress window
        let progressWindow = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 380, height: 100),
            styleMask: [.titled],
            backing: .buffered, defer: false
        )
        progressWindow.center()
        progressWindow.title = "Updating HiDock"
        progressWindow.isReleasedWhenClosed = false

        let container = NSView(frame: NSRect(x: 0, y: 0, width: 380, height: 100))

        let label = NSTextField(labelWithString: "Downloading update...")
        label.frame = NSRect(x: 20, y: 60, width: 340, height: 20)
        label.font = .systemFont(ofSize: 13)
        container.addSubview(label)

        let progressBar = NSProgressIndicator(frame: NSRect(x: 20, y: 30, width: 340, height: 20))
        progressBar.style = .bar
        progressBar.isIndeterminate = false
        progressBar.minValue = 0
        progressBar.maxValue = 100
        progressBar.doubleValue = 0
        container.addSubview(progressBar)

        progressWindow.contentView = container
        progressWindow.makeKeyAndOrderFront(nil)

        // Download in background
        let tempDir = FileManager.default.temporaryDirectory.appendingPathComponent("hidock-update-\(UUID().uuidString)")
        try? FileManager.default.createDirectory(at: tempDir, withIntermediateDirectories: true)
        let zipPath = tempDir.appendingPathComponent("update.zip")

        let task = URLSession.shared.downloadTask(with: downloadURL) { tempURL, response, error in
            DispatchQueue.main.async {
                progressWindow.close()
                progressObservation = nil

                guard let tempURL = tempURL, error == nil else {
                    let alert = NSAlert()
                    alert.messageText = "Download Failed"
                    alert.informativeText = error?.localizedDescription ?? "Unknown error"
                    alert.runModal()
                    return
                }

                do {
                    try FileManager.default.moveItem(at: tempURL, to: zipPath)
                    label.stringValue = "Installing update..."
                    performInstall(zipPath: zipPath, tempDir: tempDir)
                } catch {
                    let alert = NSAlert()
                    alert.messageText = "Update Failed"
                    alert.informativeText = "Could not save download: \(error.localizedDescription)"
                    alert.runModal()
                }
            }
        }

        // Observe progress — held in the static so it survives this
        // function returning (see progressObservation).
        progressObservation = task.progress.observe(\.fractionCompleted) { progress, _ in
            DispatchQueue.main.async {
                progressBar.doubleValue = progress.fractionCompleted * 100
                let mb = Double(task.countOfBytesReceived) / (1024 * 1024)
                let total = Double(task.countOfBytesExpectedToReceive) / (1024 * 1024)
                if total > 0 {
                    label.stringValue = String(format: "Downloading update... %.0f/%.0f MB", mb, total)
                }
            }
        }

        task.resume()
    }

    private static func performInstall(zipPath: URL, tempDir: URL) {
        let appPath = Bundle.main.bundlePath
        let extractDir = tempDir.appendingPathComponent("extracted")

        // Write an updater script that runs after the app quits
        let script = """
        #!/bin/bash
        set -euo pipefail
        # Wait for the app to quit
        while pgrep -f "HiDock Mic Trigger" > /dev/null 2>&1; do
            sleep 0.5
        done
        sleep 1

        # Extract the zip
        mkdir -p "\(extractDir.path)"
        ditto -x -k "\(zipPath.path)" "\(extractDir.path)"

        # Find the .app in the extracted folder
        APP=$(find "\(extractDir.path)" -maxdepth 2 -name "*.app" -type d | head -1)
        if [ -z "$APP" ]; then
            osascript -e 'display dialog "Update failed: could not find app in download." buttons {"OK"}'
            exit 1
        fi

        sign_and_verify() {
            local candidate="$1"
            # Preserve a valid Developer ID signature from a notarized release.
            if codesign --verify --deep --strict "$candidate" >/dev/null 2>&1 \
              && codesign -dv --verbose=4 "$candidate" 2>&1 \
                | grep -q "Authority=Developer ID Application:"; then
                echo "Using the existing Developer ID signature"
            else
                local sign_id="${HIDOCK_SIGNING_IDENTITY:-}"
                if [ -z "$sign_id" ]; then
                    sign_id=$(security find-identity -v -p codesigning 2>/dev/null \
                      | awk -F'"' '/Developer ID Application:/ {print $2; exit}')
                fi
                case "$sign_id" in
                    "Developer ID Application:"*) ;;
                    *)
                        osascript -e 'display dialog "Update failed: no valid Developer ID Application identity was found." buttons {"OK"}'
                        exit 1
                        ;;
                esac
                echo "Signing update with: $sign_id"
                codesign --force --deep --sign "$sign_id" "$candidate"
            fi
            codesign --verify --deep --strict --verbose=2 "$candidate"
        }

        # Stage and verify before replacing the live app. A failed signature
        # must leave the current working installation untouched.
        STAGE_DIR=$(mktemp -d "${TMPDIR:-/tmp}/hidock-update.XXXXXX")
        trap 'rm -rf "$STAGE_DIR"' EXIT
        STAGED_APP="$STAGE_DIR/HiDock Mic Trigger.app"
        cp -R "$APP" "$STAGED_APP"
        sign_and_verify "$STAGED_APP"

        # Replace the current app only after the staged bundle is verified.
        rm -rf "\(appPath)"
        cp -R "$STAGED_APP" "\(appPath)"
        codesign --verify --deep --strict --verbose=2 "\(appPath)"

        # Re-register with LaunchServices so Launchpad picks it up
        /System/Library/Frameworks/CoreServices.framework/Versions/Current/Frameworks/LaunchServices.framework/Versions/Current/Support/lsregister -f "\(appPath)" 2>/dev/null

        # Relaunch
        open -a "\(appPath)"

        # Clean up
        rm -rf "\(tempDir.path)"
        """

        let scriptPath = tempDir.appendingPathComponent("update.sh")
        try? script.write(to: scriptPath, atomically: true, encoding: .utf8)
        try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: scriptPath.path)

        // Launch the updater script and quit
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/bash")
        process.arguments = [scriptPath.path]
        process.standardOutput = nil
        process.standardError = nil
        try? process.run()

        // Quit the app — the script will handle the rest
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
            NSApp.terminate(nil)
        }
    }

    // MARK: - Notification (kept for background updates)

    static func registerCategory() {
        let downloadAction = UNNotificationAction(
            identifier: updateActionID,
            title: "Download Update",
            options: .foreground
        )
        let category = UNNotificationCategory(
            identifier: updateCategoryID,
            actions: [downloadAction],
            intentIdentifiers: []
        )
        let center = UNUserNotificationCenter.current()
        center.getNotificationCategories { existing in
            var categories = existing
            categories.insert(category)
            center.setNotificationCategories(categories)
        }
    }

    // MARK: - Version comparison

    static func isVersion(_ a: String, newerThan b: String) -> Bool {
        let aParts = a.split(separator: ".").compactMap { Int($0) }
        let bParts = b.split(separator: ".").compactMap { Int($0) }
        let count = max(aParts.count, bParts.count)
        for i in 0..<count {
            let aVal = i < aParts.count ? aParts[i] : 0
            let bVal = i < bParts.count ? bParts[i] : 0
            if aVal > bVal { return true }
            if aVal < bVal { return false }
        }
        return false
    }
}
