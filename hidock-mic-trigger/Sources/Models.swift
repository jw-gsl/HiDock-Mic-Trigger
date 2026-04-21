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

/// The kind of device: HiDock proprietary USB or generic mass-storage volume.
enum DeviceType: String, Codable {
    case hidock = "hidock"
    case volume = "volume"
}

struct HiDockPairedDevice: Codable, Equatable {
    let productId: Int
    let displayName: String
    var deviceType: DeviceType
    var volumeName: String?     // For volume devices: mount name / drive letter
    var subpath: String?        // Optional subfolder to scan on volume
    var pairedAt: String?       // ISO-8601 timestamp

    // Custom Codable to handle backwards compatibility with old JSON
    // that only had productId + displayName (no deviceType field).
    enum CodingKeys: String, CodingKey {
        case productId, displayName, deviceType, volumeName, subpath, pairedAt
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.productId = try container.decode(Int.self, forKey: .productId)
        self.displayName = try container.decode(String.self, forKey: .displayName)
        self.deviceType = try container.decodeIfPresent(DeviceType.self, forKey: .deviceType) ?? .hidock
        self.volumeName = try container.decodeIfPresent(String.self, forKey: .volumeName)
        self.subpath = try container.decodeIfPresent(String.self, forKey: .subpath)
        self.pairedAt = try container.decodeIfPresent(String.self, forKey: .pairedAt)
    }

    var cleanName: String {
        sanitizeDeviceName(displayName)
    }

    var shortName: String {
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

    var statusText: String {
        // Imported files come from outside the HiDock — don't label them as
        // "Downloaded" (semantically wrong and visually indistinguishable).
        if deviceId == "imported:local" { return "Imported" }
        if recording.downloaded && !recording.localExists { return "Skipped" }
        if transcriptionSkipped { return "Skipped" }  // downloaded-but-won't-transcribe
        if recording.downloaded && recording.localExists { return "Downloaded" }
        if recording.lastError != nil { return "Failed" }
        return "On device"
    }

    var statusLevel: StatusLevel {
        if deviceId == "imported:local" { return .warning }  // orange — distinct from green Downloaded
        if recording.downloaded && !recording.localExists { return .info }  // download-skipped
        if transcriptionSkipped { return .info }                            // transcription-skipped
        if recording.downloaded && recording.localExists { return .success }
        if recording.lastError != nil { return .error }
        return .secondary
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

struct TranscriptionQueueItem: Identifiable {
    let id: String
    let path: String
    let filename: String
    var status: TranscriptionQueueStatus
    var progress: Int = 0

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
