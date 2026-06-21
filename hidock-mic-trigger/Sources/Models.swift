import Foundation

struct HiDockSyncRecording: Codable {
    let name: String
    let createDate: String
    let createTime: String
    let length: Int
    let duration: Double
    let version: Int
    let mode: String
    let signature: String
    let outputPath: String
    let outputName: String
    let downloaded: Bool
    let localExists: Bool
    let downloadedAt: String?
    let lastError: String?
    let status: String
    let humanLength: String
    /// True when the local file has been trimmed in-place via the
    /// Mac app's Trim action. Flows from state.json through the
    /// extractor's status response. Decoded optionally for backward
    /// compatibility with older state.json entries.
    let trimmed: Bool?
    /// True when `duration` is the extractor's `bytes / 8000` fallback
    /// rather than the authoritative value read from MP3 frame headers.
    /// Drives the table column's `~` prefix even on downloaded rows
    /// where mutagen failed (e.g. missing dep). Optional for backward
    /// compatibility — older payloads default to "treat as estimated
    /// when not local, authoritative when local" per the previous
    /// behaviour.
    let durationEstimated: Bool?
    /// True when the user removed the local copy via the Mac app's
    /// Remove action. Excludes the row from auto-download (extractor
    /// skips with reason `user_removed`) and auto-transcribe. Surfaces
    /// as a "Removed" status pill — semantically the same UX as
    /// Skipped but with a different intent (deleted on purpose vs
    /// never wanted). Optional for state.json entries written before
    /// the field existed.
    let removed: Bool?
}

struct HiDockStorageStats: Codable {
    let totalFiles: Int
    let returnedFiles: Int
    let totalBytesReturned: Int
    let truncated: Bool
}

struct HiDockSyncStatusResponse: Codable {
    let connected: Bool
    let outputDir: String
    let statePath: String
    let configPath: String
    let recordings: [HiDockSyncRecording]
    let error: String?
    let storage: HiDockStorageStats?
    /// True when `connected`/`recordings` came from the cached catalog — a
    /// `*-cached-status` command, or a live probe that timed out / couldn't
    /// open the device and fell back to cache — rather than an authoritative
    /// live read. The UI must NOT treat `connected:false` from a cached
    /// response as a real disconnect (it would otherwise clobber the
    /// connection baseline and make the next live probe look "freshly
    /// connected", spuriously re-firing auto-download). See renderSyncStatus.
    /// Optional: absent in older/live payloads → decodes as nil (= live).
    let cached: Bool?
}

struct HiDockSyncDownloadResult: Codable {
    let filename: String
    let written: Int
    let expectedLength: Int
    let outputPath: String
    let downloaded: Bool
}

struct HiDockSyncDownloadNewResponse: Codable {
    struct Skipped: Codable {
        let filename: String
        let reason: String
    }

    let connected: Bool
    let outputDir: String
    let downloaded: [HiDockSyncDownloadResult]
    let skipped: [Skipped]
    let error: String?
}

struct HiDockDevice: Codable {
    let vendorId: Int
    let productId: Int
    let productName: String?
    let serialNumber: String?
    let bus: Int?
    let address: Int?

    var displayName: String {
        sanitizeDeviceName(productName ?? "HiDock")
    }

    var shortName: String {
        let name = displayName
        if name.hasPrefix("HiDock ") {
            return String(name.dropFirst("HiDock ".count))
        }
        return name
    }
}

struct HiDockDeviceListResponse: Codable {
    let devices: [HiDockDevice]
}

/// The kind of device: HiDock proprietary USB, generic mass-storage volume, or
/// Plaud cloud account.
enum DeviceType: String, Codable {
    case hidock = "hidock"
    case volume = "volume"
    case plaud = "plaud"
}

struct HiDockPairedDevice: Codable, Equatable {
    let productId: Int
    let displayName: String
    var deviceType: DeviceType
    var volumeName: String?     // For volume devices: mount name / drive letter
    var subpath: String?        // Optional subfolder to scan on volume
    var plaudAccountId: String? // For Plaud devices: stable account key
    var plaudRegion: String?    // "us" or "eu"
    var plaudEmail: String?
    var pairedAt: String?       // ISO-8601 timestamp

    // Custom Codable to handle backwards compatibility with old JSON
    // that only had productId + displayName (no deviceType field).
    enum CodingKeys: String, CodingKey {
        case productId, displayName, deviceType, volumeName, subpath
        case plaudAccountId, plaudRegion, plaudEmail, pairedAt
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.productId = try container.decode(Int.self, forKey: .productId)
        self.displayName = try container.decode(String.self, forKey: .displayName)
        self.deviceType = try container.decodeIfPresent(DeviceType.self, forKey: .deviceType) ?? .hidock
        self.volumeName = try container.decodeIfPresent(String.self, forKey: .volumeName)
        self.subpath = try container.decodeIfPresent(String.self, forKey: .subpath)
        self.plaudAccountId = try container.decodeIfPresent(String.self, forKey: .plaudAccountId)
        self.plaudRegion = try container.decodeIfPresent(String.self, forKey: .plaudRegion)
        self.plaudEmail = try container.decodeIfPresent(String.self, forKey: .plaudEmail)
        self.pairedAt = try container.decodeIfPresent(String.self, forKey: .pairedAt)
    }

    var cleanName: String {
        if deviceType == .plaud {
            return "Plaud"
        }
        return sanitizeDeviceName(displayName)
    }

    var shortName: String {
        if deviceType == .plaud {
            return "Plaud"
        }
        let name = cleanName
        if name.hasPrefix("HiDock ") {
            return String(name.dropFirst("HiDock ".count))
        }
        return name
    }

    /// Unique string identity used for state keys and filtering.
    var deviceId: String {
        switch deviceType {
        case .hidock:
            return "hidock:\(productId)"
        case .volume:
            return "volume:\(volumeName ?? String(productId))"
        case .plaud:
            return "plaud:\(plaudAccountId ?? plaudEmail ?? String(productId))"
        }
    }

    static func == (lhs: HiDockPairedDevice, rhs: HiDockPairedDevice) -> Bool {
        lhs.deviceId == rhs.deviceId
    }

    /// Backwards-compatible init for existing HiDock pairing code.
    init(productId: Int, displayName: String) {
        self.productId = productId
        self.displayName = displayName
        self.deviceType = .hidock
        self.volumeName = nil
        self.subpath = nil
        self.plaudAccountId = nil
        self.plaudRegion = nil
        self.plaudEmail = nil
        self.pairedAt = ISO8601DateFormatter().string(from: Date())
    }

    /// Deterministic hash for volume name (stable across runs, unlike hashValue).
    private static func stableHash(_ string: String) -> Int {
        let data = Data(string.utf8)
        var hash: UInt32 = 5381
        for byte in data {
            hash = ((hash << 5) &+ hash) &+ UInt32(byte) // djb2
        }
        return Int(hash & 0x7FFFFFFF)
    }

    /// Full init for volume devices.
    init(volumeName: String, displayName: String, subpath: String? = nil) {
        self.productId = Self.stableHash(volumeName)
        self.displayName = displayName
        self.deviceType = .volume
        self.volumeName = volumeName
        self.subpath = subpath
        self.plaudAccountId = nil
        self.plaudRegion = nil
        self.plaudEmail = nil
        self.pairedAt = ISO8601DateFormatter().string(from: Date())
    }

    /// Full init for Plaud cloud accounts.
    init(plaudAccountId: String, displayName: String, email: String?, region: String) {
        self.productId = Self.stableHash("plaud:\(plaudAccountId)")
        self.displayName = displayName
        self.deviceType = .plaud
        self.volumeName = nil
        self.subpath = nil
        self.plaudAccountId = plaudAccountId
        self.plaudRegion = region
        self.plaudEmail = email
        self.pairedAt = ISO8601DateFormatter().string(from: Date())
    }
}

/// Volume scan result from the extractor `scan-volumes` command.
struct VolumeScanResult: Codable {
    let volumeName: String
    let mountPoint: String
    let audioFileCount: Int
    let totalSizeBytes: Int
    let audioExtensions: [String]
}

struct VolumeScanResponse: Codable {
    let volumes: [VolumeScanResult]
}

struct HiDockSyncRecordingEntry: Identifiable {
    let id: String
    let recording: HiDockSyncRecording
    let deviceProductId: Int
    let deviceId: String
    let deviceName: String
    var transcribed: Bool = false
    var transcriptPath: String? = nil
    var speakersTagged: Bool = false
    var summaryPath: String? = nil
    /// User explicitly opted out of transcribing this recording. The file
    /// is downloaded but they don't want it in the transcription queue.
    /// Independent of `transcribed` — if false and skipped is true, the
    /// UI shows "Skipped" and auto-transcribe filters it out.
    var transcriptionSkipped: Bool = false

    init(recording: HiDockSyncRecording, deviceProductId: Int, deviceId: String, deviceName: String, transcribed: Bool = false, transcriptPath: String? = nil, speakersTagged: Bool = false, summaryPath: String? = nil, transcriptionSkipped: Bool = false) {
        self.id = "\(deviceId)-\(recording.name)"
        self.recording = recording
        self.deviceProductId = deviceProductId
        self.deviceId = deviceId
        self.deviceName = deviceName
        self.transcribed = transcribed
        self.transcriptPath = transcriptPath
        self.speakersTagged = speakersTagged
        self.summaryPath = summaryPath
        self.transcriptionSkipped = transcriptionSkipped
    }

    /// Cascading lifecycle: On device → Downloaded → Transcribed.
    /// Each step implies the earlier ones (you can't transcribe a file
    /// you haven't downloaded, you can't download one that isn't on the
    /// device). The Tagged column is separate — that's about speaker
    /// tagging on top of the transcription.
    var statusText: String {
        // Cascade order:
        //   Transcribed > Removed > Skipped > Imported > Downloaded > Failed > On device
        //
        // Transcribed first — it's the pipeline-end state and always
        // overrides earlier ones (an imported-then-transcribed row
        // shouldn't read "Imported" forever).
        //
        // Removed and Skipped sit together right after — both are
        // user-driven exclusions ("don't process this"), and the user
        // wants their explicit intent surfaced before metadata-derived
        // labels like "Imported" or "Downloaded". A downloaded file
        // that the user transcription-skipped now correctly shows
        // "Skipped", not "Downloaded" — making the opt-out visible.
        if summaryPath != nil { return "Summarised" }
        if transcribed { return "Transcribed" }
        if recording.removed == true { return "Removed" }
        // `downloaded == true` + `localExists == false` in the extractor
        // state means the user marked it downloaded without ever pulling
        // it (the old "Skip" flow to stop the device showing it).
        if recording.downloaded && !recording.localExists { return "Skipped" }
        if transcriptionSkipped { return "Skipped" }
        if deviceId == "imported:local" { return "Imported" }
        // Filesystem wins over metadata: if the MP3 is on disk, it's
        // downloaded regardless of what the extractor's state.json says.
        if recording.localExists { return "Downloaded" }
        if recording.lastError != nil { return "Failed" }
        return "On device"
    }

    var statusLevel: StatusLevel {
        if summaryPath != nil { return .summarised }                 // indigo: "summarised"
        if transcribed { return .transcribed }                       // purple: "fully processed"
        if recording.removed == true { return .removed }             // muted red: "I deleted this"
        if recording.downloaded && !recording.localExists { return .skipped }
        if transcriptionSkipped { return .skipped }                  // cyan: "I told it to skip"
        if deviceId == "imported:local" { return .info }             // blue: source marker, not a warning
        if recording.localExists { return .success }                 // green: "got the bytes"
        if recording.lastError != nil { return .error }              // red: needs attention
        return .secondary                                            // grey: not yet acted on
    }

    /// The classification type of this recording's summary, e.g. "Brainstorming".
    /// Parsed from the summary filename, which is "<stem> - <Type> - <Area> -
    /// <Desc>.md" — we strip the known recording stem prefix so the type is
    /// unambiguous even if the Area/Desc contain " - ". nil when not summarised.
    var summaryType: String? {
        guard let sp = summaryPath, !sp.isEmpty else { return nil }
        let base = ((sp as NSString).lastPathComponent as NSString).deletingPathExtension
        let stem = (recording.outputName as NSString).deletingPathExtension
        let prefix = stem + " - "
        guard base.hasPrefix(prefix) else { return nil }
        let rest = String(base.dropFirst(prefix.count))
        if let r = rest.range(of: " - ") { return String(rest[..<r.lowerBound]) }
        return rest.isEmpty ? nil : rest
    }
}

/// User-selectable filter that restricts the recordings table to a
/// pipeline-stage subset. Distinct from the per-device filter (which
/// is set by clicking the filter icon on a device card) — both can be
/// active simultaneously and are AND-ed together.
enum SyncStatusFilter: String, CaseIterable, Identifiable {
    case all
    case onDevice
    case downloaded
    case untranscribed
    case transcribed
    case summarised
    case skipped
    case removed
    case failed
    case imported

    var id: String { rawValue }

    var label: String {
        switch self {
        case .all: return "All"
        case .onDevice: return "On device"
        case .downloaded: return "Downloaded"
        case .untranscribed: return "Untranscribed"
        case .transcribed: return "Transcribed"
        case .summarised: return "Summarised"
        case .skipped: return "Skipped"
        case .removed: return "Removed"
        case .failed: return "Failed"
        case .imported: return "Imported"
        }
    }
}

enum DisplayRow: Identifiable {
    case recording(HiDockSyncRecordingEntry)
    case mergeParent(MergeGroup)
    case mergeChild(HiDockSyncRecordingEntry)

    var id: String {
        switch self {
        case .recording(let e): return e.id
        case .mergeParent(let g): return "merge-\(g.id)"
        case .mergeChild(let e): return "child-\(e.id)"
        }
    }
}

enum StatusLevel {
    case normal, success, warning, error, info, secondary
    /// Used for the "Transcribed" status — distinct from `.success`
    /// (which remains green for "Downloaded") so Downloaded and
    /// Transcribed rows are visually distinguishable at a glance.
    case transcribed
    /// "Summarised" — a transcript that also has a typed summary. Indigo,
    /// distinct from transcribed purple, reads as "one step further processed".
    case summarised
    /// User-driven opt-out: "I told you to skip this." Cyan reads as a
    /// deliberate choice — distinct from grey ("not yet acted on")
    /// and from blue (informational).
    case skipped
    /// User-driven destructive action: "I deleted the local copy."
    /// Renders as muted red — reminds the user a destructive action
    /// was taken without screaming "error" the way full red would.
    case removed
}

struct MergeGroup: Codable, Identifiable {
    let id: String
    let outputPath: String
    let outputName: String
    let childNames: [String]  // recording names (e.g. "2026Apr10-130151-Rec07.hda")
    let createdAt: String
    let totalDuration: Double

    init(outputPath: String, childNames: [String], totalDuration: Double) {
        self.id = outputPath
        self.outputPath = outputPath
        self.outputName = (outputPath as NSString).lastPathComponent
        self.childNames = childNames
        self.createdAt = ISO8601DateFormatter().string(from: Date())
        self.totalDuration = totalDuration
    }
}

/// One detected merge-candidate chain: 2+ recordings on the same
/// device, small wall-clock gaps, transcripts present on every piece.
/// Decoded from `extractor.py merge-candidates` JSON output.
struct MergeCandidate: Codable, Identifiable {
    struct Piece: Codable {
        let device_name: String   // e.g. "2026Apr22-203106-Rec52.hda"
        let mp3_name: String
        let mp3_path: String
        let start: String         // ISO-8601
        let duration_s: Double
        let pid: Int?
    }
    let score: Int
    let high_confidence: Bool
    let total_min: Double
    let max_gap_s: Double
    let continuity_signals: [String]
    let pair_key: String          // stable order-independent key for dismiss
    let pieces: [Piece]

    /// Identifiable conformance — pair_key is stable across rescans
    /// (sorted concat of first+last filename), so SwiftUI can keep
    /// row identity even when scores or signals change.
    var id: String { pair_key }
}

struct MergeCandidatesPayload: Codable {
    let chains: [MergeCandidate]
    let high_confidence_count: Int
    let total_count: Int
}

struct TranscriptionQueueItem: Identifiable {
    let id: String
    let path: String
    let filename: String
    var status: TranscriptionQueueStatus
    var progress: Int = 0
    /// Captured on `.failed` / `.cancelled`. Drives the clickable
    /// red-X icon in the recordings table — tapping it surfaces the
    /// actual stderr or NSError text instead of forcing the user to
    /// dig through the menu-bar log file.
    var errorMessage: String? = nil

    init(path: String) {
        self.id = path
        self.path = path
        self.filename = (path as NSString).lastPathComponent
        self.status = .queued
    }
}

enum TranscriptionQueueStatus: String {
    case queued = "Queued"
    case transcribing = "Transcribing"
    case completed = "Completed"
    case failed = "Failed"
    case cancelled = "Cancelled"
}
