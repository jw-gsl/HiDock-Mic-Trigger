import Foundation

/// The app's side of `shared/review_feedback.py`: JSON-lines logs of the
/// reviewer's decisions under `~/HiDock/.review-feedback/`, which the pipeline
/// reads to stop re-suggesting skipped lines and to measure accuracy.
enum ReviewFeedbackLog {
    static let directory = URL(fileURLWithPath: NSHomeDirectory())
        .appendingPathComponent("HiDock/.review-feedback", isDirectory: true)
    static let calendarLog = "calendar-decisions.jsonl"

    static func append(_ row: [String: Any], to name: String) {
        var row = row
        row["at"] = ISO8601DateFormatter().string(from: Date())
        guard let data = try? JSONSerialization.data(withJSONObject: row, options: [.sortedKeys]) else { return }
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let url = directory.appendingPathComponent(name)
        if !FileManager.default.fileExists(atPath: url.path) {
            FileManager.default.createFile(atPath: url.path, contents: nil)
        }
        guard let handle = try? FileHandle(forWritingTo: url) else { return }
        defer { try? handle.close() }
        _ = try? handle.seekToEnd()
        try? handle.write(contentsOf: data + Data("\n".utf8))
    }

    /// How often each meeting title has been confirmed (accepted, chosen or
    /// linked) — the recurring-meeting signal.
    static func confirmedMeetingTitles() -> [String: Int] {
        let url = directory.appendingPathComponent(calendarLog)
        guard let text = try? String(contentsOf: url, encoding: .utf8) else { return [:] }
        var counts: [String: Int] = [:]
        for line in text.split(separator: "\n") {
            guard let row = try? JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any],
                  let decision = row["decision"] as? String,
                  ["accepted", "chose_other", "linked"].contains(decision),
                  let title = row["final_title"] as? String else { continue }
            counts[title, default: 0] += 1
        }
        return counts
    }

    /// False once tuning has found that recurring-meeting picks are mostly
    /// declined or swapped (see `review_feedback.tune`).
    static func recurringMeetingsEnabled() -> Bool {
        let url = directory.appendingPathComponent("tuning.json")
        guard let data = try? Data(contentsOf: url),
              let tuning = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return true }
        return tuning["recurring_meetings"] as? Bool ?? true
    }
}
