import Foundation
import SwiftUI

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

/// Returns an SF Symbol name for a device based on its type and short name.
/// H1 = docking station with speaker, P1 = handheld recorder, volume = external drive.
/// Falls back through the shared `hidockSKU` matcher so "HiDock P1" doesn't
/// mis-match on the word "dock".
func hidockDeviceIcon(_ shortName: String, deviceType: DeviceType = .hidock) -> String {
    if deviceType == .volume {
        return "externaldrive"
    }
    if deviceType == .plaud {
        return "waveform.and.mic"
    }
    switch hidockSKU(for: shortName, deviceType: deviceType) {
    case .h1, .h1e: return "hifispeaker"
    case .p1:       return "waveform.and.mic"
    case .none:     return "externaldrive.connected.to.line.below"
    }
}

/// Returns a Unicode emoji/symbol for a device type (for text-only contexts like menu bar).
func hidockDeviceEmoji(_ shortName: String, deviceType: DeviceType = .hidock) -> String {
    if deviceType == .volume {
        return "💾"
    }
    if deviceType == .plaud {
        return "▯"
    }
    switch hidockSKU(for: shortName, deviceType: deviceType) {
    case .h1, .h1e: return "🔊"
    case .p1:       return "🎙️"
    case .none:     return "🔌"
    }
}

/// Which HiDock SKU a device corresponds to based on its short name.
/// Returns nil for generic USB volumes and unrecognised HiDock SKUs.
enum HiDockSKU {
    case p1, h1, h1e
}

func hidockSKU(for shortName: String, deviceType: DeviceType = .hidock) -> HiDockSKU? {
    guard deviceType == .hidock else { return nil }
    let name = shortName.lowercased()
    // Match on explicit model tokens. Check H1e before H1 since "h1e" contains
    // "h1"; never match "dock" alone because "HiDock P1" contains it too.
    if name.contains("h1e") { return .h1e }
    if name.contains("h1") { return .h1 }
    if name.contains("p1") { return .p1 }
    return nil
}

/// Returns the rich product-photo asset for a HiDock SKU, or nil for
/// generic volumes. Use in the big device cards at the top of the sync
/// window — H1/H1E/P1 are visually distinct at 44pt.
func hidockDeviceImage(_ shortName: String, deviceType: DeviceType = .hidock, recording: Bool = false) -> Image? {
    _ = recording
    if deviceType == .plaud {
        return Image("DeviceRecordingPlaud")
    }
    guard let sku = hidockSKU(for: shortName, deviceType: deviceType) else { return nil }
    switch sku {
    case .p1:  return Image("DeviceRecordingP1")
    case .h1:  return Image("DeviceRecordingH1")
    case .h1e: return Image("DeviceRecordingH1e")
    }
}

/// Returns the flat line-glyph asset (template-rendered SVG) for a
/// HiDock SKU. Use in compact contexts like table rows where the
/// detailed product photo is too busy — a 16pt glyph reads cleanly
/// as a visual cue beside the device name. Falls back to the H1
/// glyph for H1E because there isn't a dedicated asset yet.
func hidockDeviceGlyph(_ shortName: String, deviceType: DeviceType = .hidock) -> Image? {
    if deviceType == .plaud {
        return Image("DeviceGlyphPlaud")
    }
    guard let sku = hidockSKU(for: shortName, deviceType: deviceType) else { return nil }
    switch sku {
    case .p1:       return Image("DeviceGlyphP1")
    case .h1, .h1e: return Image("DeviceGlyphH1")
    }
}

// MARK: - Main window size

/// Shared min sizes for the main sync window. The recordings table uses fixed
/// column widths that sum to ~1141pt (full) / ~931pt (detail-pane, two columns
/// hidden). The window must not go narrower than that or the left edge of the
/// pane clips off-screen. Keep AppDelegate's initial `minSize` and
/// `MainWindowView`'s SwiftUI min in sync via these constants + `WindowMinSizeEnforcer`.
enum MainWindowMetrics {
    static let minHeight: CGFloat = 510
    /// Full table (all columns) + modest padding — no detail pane.
    static let minWidth: CGFloat = 1200
    /// Detail pane open: main list still needs room after column-hiding + detail min.
    static let minWidthWithDetail: CGFloat = 1280

    static func minSize(detailPaneVisible: Bool) -> NSSize {
        NSSize(
            width: detailPaneVisible ? minWidthWithDetail : minWidth,
            height: minHeight
        )
    }
}

/// Keeps `NSWindow.minSize` aligned with SwiftUI state and grows the frame if
/// it's currently smaller than the new minimum (e.g. after a min-width bump or
/// when the detail pane opens). SwiftUI's `.frame(minWidth:)` alone does not
/// reliably set the AppKit window minimum on macOS.
struct WindowMinSizeEnforcer: NSViewRepresentable {
    let minSize: NSSize

    func makeNSView(context: Context) -> NSView {
        let view = NSView(frame: .zero)
        view.isHidden = true
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        DispatchQueue.main.async {
            guard let window = nsView.window else { return }
            if window.minSize != minSize {
                window.minSize = minSize
            }
            var frame = window.frame
            var grew = false
            if frame.width < minSize.width {
                // Grow to the right so the left edge (and traffic lights) stay put.
                frame.size.width = minSize.width
                // If growing would push past the screen, shift left instead.
                if let screen = window.screen ?? NSScreen.main {
                    let maxX = screen.visibleFrame.maxX
                    if frame.maxX > maxX {
                        frame.origin.x = max(screen.visibleFrame.minX, maxX - frame.width)
                    }
                }
                grew = true
            }
            if frame.height < minSize.height {
                let maxY = frame.maxY
                frame.size.height = minSize.height
                frame.origin.y = maxY - minSize.height
                grew = true
            }
            if grew {
                window.setFrame(frame, display: true)
            }
        }
    }
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
