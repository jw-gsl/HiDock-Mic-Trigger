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

struct HiDockPairedDevice: Codable, Equatable {
    let productId: Int
    let displayName: String

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

    static func == (lhs: HiDockPairedDevice, rhs: HiDockPairedDevice) -> Bool {
        lhs.productId == rhs.productId
    }
}

struct HiDockSyncRecordingEntry: Identifiable {
    let id: String
    let recording: HiDockSyncRecording
    let deviceProductId: Int
    let deviceName: String
    var transcribed: Bool = false
    var transcriptPath: String? = nil

    init(recording: HiDockSyncRecording, deviceProductId: Int, deviceName: String, transcribed: Bool = false, transcriptPath: String? = nil) {
        self.id = "\(deviceProductId)-\(recording.name)"
        self.recording = recording
        self.deviceProductId = deviceProductId
        self.deviceName = deviceName
        self.transcribed = transcribed
        self.transcriptPath = transcriptPath
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
