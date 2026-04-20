import Foundation

/// Persists the set of recordings the user has opted out of transcribing —
/// a downloaded file they don't care to transcribe (perhaps a short
/// accidental recording, or audio where the quality is too poor to bother).
///
/// This is orthogonal to the extractor's `mark-downloaded` flag, which
/// tracks the download state of on-device recordings. Here we track a
/// post-download, pre-transcribe user choice.
///
/// Stored as a JSON array of filenames (MP3 basenames) in
/// `~/HiDock/skipped_transcriptions.json` so it survives app restarts and
/// is user-editable if needed.
enum SkippedTranscriptionsStore {
    static var path: String {
        "\(NSHomeDirectory())/HiDock/skipped_transcriptions.json"
    }

    static func load() -> Set<String> {
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
              let arr = try? JSONDecoder().decode([String].self, from: data) else {
            return []
        }
        return Set(arr)
    }

    static func save(_ names: Set<String>) {
        do {
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            let data = try encoder.encode(Array(names).sorted())
            try data.write(to: URL(fileURLWithPath: path))
        } catch {
            NSLog("SkippedTranscriptionsStore: save failed: %@", error.localizedDescription)
        }
    }
}
