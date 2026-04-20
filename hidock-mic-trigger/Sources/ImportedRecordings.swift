import AVFoundation
import Foundation

/// Persistence + helpers for recordings that the user imported manually from
/// outside the HiDock (e.g. Zoom exports, iPhone voice memos, colleague's
/// audio file). Imported files sit alongside HiDock-downloaded files in
/// `~/HiDock/Recordings/` and appear in the recordings table as entries
/// belonging to a virtual "Imported" device.
struct ImportedRecordingEntry: Codable {
    /// Filename inside `~/HiDock/Recordings/` (e.g. `imported-AiAccTrans.wav`).
    let name: String
    /// Absolute path to the file on disk.
    let outputPath: String
    /// Where the user imported it from (for display / debugging).
    let originalPath: String
    /// File size in bytes at import time.
    let length: Int
    /// Seconds — 0 if we can't determine without ffmpeg.
    let duration: Double
    /// ISO-8601 date of the original file's mtime (or import date as fallback).
    let createdAt: String
    /// ISO-8601 date the user clicked Import.
    let importedAt: String
}

/// Virtual device identifier used for imported recordings. Mirrors the
/// "hidock:<productId>" / "volume:<name>" pattern used elsewhere.
let IMPORTED_DEVICE_ID = "imported:local"
let IMPORTED_DEVICE_NAME = "Imported"

/// Audio / video extensions the import flow accepts. Whisper / Parakeet /
/// the shared audio_utils loader all delegate to ffmpeg under the hood,
/// so any format ffmpeg can decode works — including video containers
/// (mp4/mov/m4v) where we just grab the audio track.
let IMPORT_ALLOWED_EXTENSIONS = [
    "mp3", "wav", "m4a", "aac", "flac", "ogg", "opus",
    "mp4", "mov", "m4v",
]

enum ImportedRecordingsStore {
    /// JSON file holding the list of imports. Sits in `~/HiDock/` so it
    /// survives app upgrades and isn't confused with the pipeline state.
    static var path: String {
        "\(NSHomeDirectory())/HiDock/imported_recordings.json"
    }

    static func load() -> [ImportedRecordingEntry] {
        guard let data = try? Data(contentsOf: URL(fileURLWithPath: path)) else {
            return []
        }
        do {
            return try JSONDecoder().decode([ImportedRecordingEntry].self, from: data)
        } catch {
            NSLog("ImportedRecordingsStore: failed to decode %@: %@",
                  path, error.localizedDescription)
            return []
        }
    }

    static func save(_ entries: [ImportedRecordingEntry]) {
        do {
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            let data = try encoder.encode(entries)
            try data.write(to: URL(fileURLWithPath: path))
        } catch {
            NSLog("ImportedRecordingsStore: failed to save %@: %@",
                  path, error.localizedDescription)
        }
    }

    /// Compute a collision-free destination filename inside the recordings
    /// folder. Prefixes with `imported-` so imported files sort separately
    /// from device-downloaded ones (which start with dates like `2026Apr…`).
    static func uniqueDestinationName(
        for sourceURL: URL, in recordingsDir: URL,
    ) -> String {
        let stem = sourceURL.deletingPathExtension().lastPathComponent
        let ext = sourceURL.pathExtension.lowercased()
        let base = "imported-\(stem).\(ext)"
        let baseURL = recordingsDir.appendingPathComponent(base)
        if !FileManager.default.fileExists(atPath: baseURL.path) {
            return base
        }
        // Collision — append -2, -3, … until we find a free slot.
        var i = 2
        while true {
            let candidate = "imported-\(stem)-\(i).\(ext)"
            let url = recordingsDir.appendingPathComponent(candidate)
            if !FileManager.default.fileExists(atPath: url.path) {
                return candidate
            }
            i += 1
        }
    }

    /// Build a synthetic HiDockSyncRecording from an imported entry so the
    /// existing recordings table can display it without any schema change.
    ///
    /// Dates are emitted in HiDock's `YYYY/MM/DD` / `HH:MM:SS` format so
    /// sorting interleaves correctly with device-reported recordings —
    /// otherwise imports would sort to a weird position because `-` and `/`
    /// collate differently in string comparison.
    static func asSyncRecording(_ entry: ImportedRecordingEntry) -> HiDockSyncRecording {
        let (createDate, createTime) = formatAsHiDockDate(entry.createdAt)
        return HiDockSyncRecording(
            name: entry.name,
            createDate: createDate,
            createTime: createTime,
            length: entry.length,
            duration: entry.duration,
            version: 1,
            mode: "",
            signature: "",
            outputPath: entry.outputPath,
            outputName: entry.name,
            downloaded: true,
            localExists: FileManager.default.fileExists(atPath: entry.outputPath),
            downloadedAt: entry.importedAt,
            lastError: nil,
            status: "imported",
            humanLength: formatDuration(entry.duration)
        )
    }

    /// Convert an ISO-8601 timestamp to HiDock's display format.
    private static func formatAsHiDockDate(_ iso: String) -> (date: String, time: String) {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        guard let date = formatter.date(from: iso) else {
            return ("", "")
        }
        let dateFmt = DateFormatter()
        dateFmt.dateFormat = "yyyy/MM/dd"
        dateFmt.locale = Locale(identifier: "en_US_POSIX")
        let timeFmt = DateFormatter()
        timeFmt.dateFormat = "HH:mm:ss"
        timeFmt.locale = Locale(identifier: "en_US_POSIX")
        return (dateFmt.string(from: date), timeFmt.string(from: date))
    }

    /// Probe an audio / video file with AVFoundation to get its duration
    /// in seconds. Returns 0 if the file can't be read (unknown codec,
    /// corrupted, or AVFoundation simply doesn't support it).
    static func probeDuration(at path: String) -> Double {
        let url = URL(fileURLWithPath: path)
        let asset = AVURLAsset(url: url)
        // Synchronous load is fine here — we're on a background thread
        // during import, not the main queue.
        let seconds = CMTimeGetSeconds(asset.duration)
        return seconds.isFinite && seconds > 0 ? seconds : 0
    }

    /// Format seconds as HH:MM:SS or MM:SS to match HiDock's humanLength.
    private static func formatDuration(_ seconds: Double) -> String {
        guard seconds > 0 else { return "" }
        let total = Int(seconds)
        let hours = total / 3600
        let minutes = (total % 3600) / 60
        let secs = total % 60
        if hours > 0 {
            return String(format: "%d:%02d:%02d", hours, minutes, secs)
        }
        return String(format: "%d:%02d", minutes, secs)
    }
}
