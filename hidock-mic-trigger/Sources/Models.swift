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

struct HiDockSyncStatusResponse: Codable {
    let connected: Bool
    let outputDir: String
    let statePath: String
    let configPath: String
    let recordings: [HiDockSyncRecording]
    let error: String?
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

    /// Full init for volume devices.
    init(volumeName: String, displayName: String, subpath: String? = nil) {
        self.productId = volumeName.hashValue
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

struct HiDockSyncRecordingEntry: Identifiable {
    let id: String
    let recording: HiDockSyncRecording
    let deviceProductId: Int
    let deviceName: String
    var transcribed: Bool = false
    var transcriptPath: String? = nil
    var speakersTagged: Bool = false
    var summaryPath: String? = nil

    init(recording: HiDockSyncRecording, deviceProductId: Int, deviceName: String, transcribed: Bool = false, transcriptPath: String? = nil, speakersTagged: Bool = false, summaryPath: String? = nil) {
        self.id = "\(deviceProductId)-\(recording.name)"
        self.recording = recording
        self.deviceProductId = deviceProductId
        self.deviceName = deviceName
        self.transcribed = transcribed
        self.transcriptPath = transcriptPath
        self.speakersTagged = speakersTagged
        self.summaryPath = summaryPath
    }

    var statusText: String {
        if recording.downloaded && recording.localExists { return "Downloaded" }
        if recording.downloaded && !recording.localExists { return "Marked" }
        if recording.lastError != nil { return "Failed" }
        return "On device"
    }

    var statusLevel: StatusLevel {
        if recording.downloaded && recording.localExists { return .success }
        if recording.downloaded && !recording.localExists { return .info }
        if recording.lastError != nil { return .error }
        return .secondary
    }
}

enum StatusLevel {
    case normal, success, warning, error, info, secondary
}
