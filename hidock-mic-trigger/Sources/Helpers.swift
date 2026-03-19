import Foundation

/// Converts a raw sync error string into a user-friendly description.
func syncErrorDescription(_ error: String) -> String {
    // Check for "held by" in any error (device not found, access denied, etc.)
    if let range = error.range(of: "held by ") {
        let owner = String(error[range.upperBound...]).trimmingCharacters(in: .whitespacesAndNewlines)
        if error.localizedCaseInsensitiveContains("not found") {
            return "Device held by \(owner). Close it and Refresh."
        }
        return "USB busy — held by \(owner). Close it and Refresh."
    }
    if error.contains("Errno 13") || error.localizedCaseInsensitiveContains("Access denied") {
        if error.contains("WebUSB") || error.contains("browser") {
            return "USB busy — a browser (WebUSB) may have the device open. Close the tab and Refresh."
        }
        return "USB busy — another app has the device open. Close it and Refresh."
    }
    return error
}

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

/// Returns an SF Symbol name for a HiDock device type based on its short name.
/// H1 = docking station with speaker, P1 = handheld recorder.
func hidockDeviceIcon(_ shortName: String) -> String {
    let name = shortName.lowercased()
    if name.contains("h1") || name.contains("dock") {
        return "hifispeaker"       // docking station / speaker
    }
    if name.contains("p1") || name.contains("portable") {
        return "waveform.and.mic"  // handheld recorder / dictaphone
    }
    return "externaldrive.connected.to.line.below"  // generic USB device
}

/// Returns a Unicode emoji/symbol for a device type (for text-only contexts like menu bar).
func hidockDeviceEmoji(_ shortName: String) -> String {
    let name = shortName.lowercased()
    if name.contains("h1") || name.contains("dock") {
        return "🔊"  // speaker / dock
    }
    if name.contains("p1") || name.contains("portable") {
        return "🎙️"  // microphone / recorder
    }
    return "🔌"  // generic device
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
