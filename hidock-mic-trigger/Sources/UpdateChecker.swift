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
    }

    /// Checks GitHub for a newer release. Calls `completion` on the main thread only if an update is available
    /// and the user hasn't already been notified about this version.
    static func checkForUpdate(completion: @escaping (_ title: String, _ body: String, _ url: String) -> Void) {
        guard let currentVersion = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String else {
            return
        }

        let urlString = "https://api.github.com/repos/jw-gsl/HiDock-Mic-Trigger/releases/latest"
        guard let url = URL(string: urlString) else { return }

        var request = URLRequest(url: url)
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        request.timeoutInterval = 15

        URLSession.shared.dataTask(with: request) { data, response, error in
            guard let data = data, error == nil else { return }
            guard let release = try? JSONDecoder().decode(GitHubRelease.self, from: data) else { return }

            let remoteVersion = release.tag_name.hasPrefix("v")
                ? String(release.tag_name.dropFirst())
                : release.tag_name

            guard isVersion(remoteVersion, newerThan: currentVersion) else { return }

            // Don't nag for the same version twice
            let lastChecked = UserDefaults.standard.string(forKey: lastCheckedVersionKey)
            if lastChecked == remoteVersion { return }
            UserDefaults.standard.set(remoteVersion, forKey: lastCheckedVersionKey)

            DispatchQueue.main.async {
                completion(
                    "Update Available: \(release.name)",
                    "Version \(remoteVersion) is available (you have \(currentVersion)).",
                    release.html_url
                )
            }
        }.resume()
    }

    /// Simple numeric version comparison: "1.2.3" > "1.2.0"
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

    /// Register the notification category with a "Download Update" action button.
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
        // Merge with existing categories
        let center = UNUserNotificationCenter.current()
        center.getNotificationCategories { existing in
            var categories = existing
            categories.insert(category)
            center.setNotificationCategories(categories)
        }
    }

    /// Post a macOS notification with the "Download Update" action.
    static func postUpdateNotification(title: String, body: String, url: String) {
        // Store the URL so the delegate can open it when the notification is tapped
        UserDefaults.standard.set(url, forKey: updateURLKey)

        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = .default
        content.categoryIdentifier = updateCategoryID

        let request = UNNotificationRequest(
            identifier: "hidock-update-check",
            content: content,
            trigger: nil
        )
        UNUserNotificationCenter.current().add(request)
    }
}
