import Foundation

/// Recordings that have been split into parts, and so must stop behaving like
/// recordings in their own right.
///
/// Splitting used to *add* two parts and leave the original sitting in the
/// table alongside them. That is not a split, it is a duplication, and it was
/// not merely cosmetic: on 2026-07-31 a merge run immediately afterwards swept
/// up all three rows, so `merge_groups.json` recorded
///
///     Merged-…Rec01-Part-1-to-…Rec01-Part-2.mp3
///       <- ['…Rec01-Part-1.mp3', '…Rec01.hda', '…Rec01-Part-2.mp3']
///
/// and the merged audio contained the whole conversation twice.
///
/// The source file itself is deliberately kept on disk. The parts are freshly
/// encoded, so the original is the only lossless-relative copy of the audio; if
/// a split turns out to be in the wrong place, it is the thing you need. This
/// store records that it has been *superseded* — hidden from the table and
/// ineligible for merging — rather than deleting it.
///
/// Keyed by absolute output path, because that is what `splitRecording` knows
/// and what merge candidacy is filtered on. Stored in
/// `~/HiDock/split_sources.json` alongside the other local state files.
enum SplitSourcesStore {
    static var path: String {
        "\(NSHomeDirectory())/HiDock/split_sources.json"
    }

    static func load() -> Set<String> {
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)),
              let paths = try? JSONDecoder().decode([String].self, from: data) else {
            return []
        }
        return Set(paths)
    }

    static func save(_ paths: Set<String>) {
        do {
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            let data = try encoder.encode(Array(paths).sorted())
            try data.write(to: URL(fileURLWithPath: path))
        } catch {
            NSLog("SplitSourcesStore: save failed: %@", error.localizedDescription)
        }
    }
}
