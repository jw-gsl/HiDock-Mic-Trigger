import Foundation

func formatRecordingDuration(_ seconds: Double) -> String {
    let total = max(Int(seconds.rounded()), 0)
    let hours = total / 3600
    let minutes = (total % 3600) / 60
    let secs = total % 60
    if hours > 0 {
        return String(format: "%d:%02d:%02d", hours, minutes, secs)
    }
    return String(format: "%d:%02d", minutes, secs)
}

func shortenMicName(_ name: String) -> String {
    let noise: [String] = [
        "Microphone", "Mic", "USB Audio", "USB", "Audio Device",
        "Digital", "Sound", "Device", "Input",
    ]
    var parts = name.components(separatedBy: " ")
    parts = parts.filter { word in
        !noise.contains { word.caseInsensitiveCompare($0) == .orderedSame }
    }
    let short = parts.joined(separator: " ")
        .trimmingCharacters(in: .whitespaces)
    if short.isEmpty { return name }
    return short
}

/// Sanitizes a HiDock device name: removes serial numbers in brackets/parentheses
/// and replaces underscores with spaces.
func sanitizeDeviceName(_ raw: String) -> String {
    var s = raw
    if let range = s.range(of: "\\s*[\\(\\[].*[\\)\\]]", options: .regularExpression) {
        s.removeSubrange(range)
    }
    return s.replacingOccurrences(of: "_", with: " ").trimmingCharacters(in: .whitespaces)
}
