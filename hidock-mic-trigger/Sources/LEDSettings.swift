import Foundation
import SwiftUI

/// Kinds of app event that can drive the LED ticker. Each can be toggled on/off
/// in settings, and (in per-event colour mode) maps to a colour.
enum LEDEventKind: String, CaseIterable, Identifiable, Codable {
    case download, transcription, summarise, micRecording, syncComplete, error, deviceConnect
    var id: String { rawValue }
    var label: String {
        switch self {
        case .download: return "New downloads"
        case .transcription: return "Transcription"
        case .summarise: return "Summarise"
        case .micRecording: return "Mic recording (REC)"
        case .syncComplete: return "Sync complete"
        case .error: return "Errors"
        case .deviceConnect: return "Device connect/disconnect"
        }
    }
}

/// What the idle ticker cycles through when nothing is happening.
enum LEDIdleContent: String, CaseIterable, Identifiable, Codable {
    case clock, streak, queue, meetingsToday
    var id: String { rawValue }
    var label: String {
        switch self {
        case .clock: return "Clock"
        case .streak: return "Day streak"
        case .queue: return "Queue depth"
        case .meetingsToday: return "Meetings today"
        }
    }
}

enum LEDColorScheme: String, CaseIterable, Identifiable, Codable {
    case green, perEvent
    var id: String { rawValue }
    var label: String { self == .green ? "Green (mono)" : "Per-event colour" }
}

enum LEDDefaultView: String, CaseIterable, Identifiable, Codable {
    case heatmap, led
    var id: String { rawValue }
    var label: String { self == .heatmap ? "Heatmap" : "LED ticker" }
}

/// User-facing LED ticker settings, persisted in `UserDefaults` under the
/// `led.` prefix. An `ObservableObject` so the settings popover and the matrix
/// react live.
final class LEDSettings: ObservableObject {
    private let d = UserDefaults.standard
    private func key(_ s: String) -> String { "led.\(s)" }

    @Published var enabled: Bool { didSet { d.set(enabled, forKey: key("enabled")) } }
    @Published var defaultView: LEDDefaultView { didSet { d.set(defaultView.rawValue, forKey: key("defaultView")) } }
    @Published var eventTakeover: Bool { didSet { d.set(eventTakeover, forKey: key("eventTakeover")) } }
    @Published var autoRevertSeconds: Double { didSet { d.set(autoRevertSeconds, forKey: key("autoRevert")) } }
    @Published var colorScheme: LEDColorScheme { didSet { d.set(colorScheme.rawValue, forKey: key("color")) } }
    @Published var brightness: Double { didSet { d.set(brightness, forKey: key("brightness")) } }
    /// Columns scrolled per second.
    @Published var scrollSpeed: Double { didSet { d.set(scrollSpeed, forKey: key("speed")) } }
    @Published var idleTickerEnabled: Bool { didSet { d.set(idleTickerEnabled, forKey: key("idle")) } }
    @Published var idleContents: Set<LEDIdleContent> { didSet { saveSet(idleContents.map(\.rawValue), "idleContents") } }
    @Published var enabledEvents: Set<LEDEventKind> { didSet { saveSet(enabledEvents.map(\.rawValue), "events") } }
    @Published var micVU: Bool { didSet { d.set(micVU, forKey: key("micVU")) } }
    @Published var bootAnimation: Bool { didSet { d.set(bootAnimation, forKey: key("boot")) } }

    init() {
        // Use UserDefaults.standard directly (not self.d) — these run before
        // all stored properties are initialized.
        let u = UserDefaults.standard
        func boolOr(_ k: String, _ def: Bool) -> Bool { u.object(forKey: "led.\(k)") == nil ? def : u.bool(forKey: "led.\(k)") }
        func dblOr(_ k: String, _ def: Double) -> Double { u.object(forKey: "led.\(k)") == nil ? def : u.double(forKey: "led.\(k)") }

        enabled = boolOr("enabled", true)
        defaultView = LEDDefaultView(rawValue: u.string(forKey: "led.defaultView") ?? "") ?? .heatmap
        eventTakeover = boolOr("eventTakeover", true)
        autoRevertSeconds = dblOr("autoRevert", 6)
        colorScheme = LEDColorScheme(rawValue: u.string(forKey: "led.color") ?? "") ?? .perEvent
        brightness = dblOr("brightness", 1.0)
        scrollSpeed = dblOr("speed", 22)
        idleTickerEnabled = boolOr("idle", true)
        micVU = boolOr("micVU", true)
        bootAnimation = boolOr("boot", true)

        let idleRaw = u.stringArray(forKey: "led.idleContents") ?? ["clock", "meetingsToday"]
        idleContents = Set(idleRaw.compactMap(LEDIdleContent.init(rawValue:)))
        let evRaw = u.stringArray(forKey: "led.events") ?? LEDEventKind.allCases.map(\.rawValue)
        enabledEvents = Set(evRaw.compactMap(LEDEventKind.init(rawValue:)))
    }

    private func saveSet(_ values: [String], _ k: String) { d.set(values, forKey: key(k)) }

    func isEnabled(_ kind: LEDEventKind) -> Bool { enabledEvents.contains(kind) }
}
