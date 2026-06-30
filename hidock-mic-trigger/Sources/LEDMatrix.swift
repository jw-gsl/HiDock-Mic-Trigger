import SwiftUI
import Combine

/// One rendered column of the LED display: 7 vertical pixels + a colour.
struct LEDColumn: Equatable {
    var bits: [Bool]            // top (0) → bottom (6)
    var color: Color
    static let blank = LEDColumn(bits: Array(repeating: false, count: LEDFont.height), color: .clear)
}

/// A high-level app event the ticker can announce.
struct LEDEvent {
    let kind: LEDEventKind
    let text: String
    var priority: Int = 0       // >0 interrupts an idle scroll in progress
}

/// Drives the LED ticker. It is a small state machine that publishes a `track`
/// (the columns to render) plus a `mode` and a `trackStart` timestamp; the
/// *view* does the smooth, time-based rendering (60fps via TimelineView) so
/// scrolling glides at sub-pixel precision instead of stepping column-by-column.
///
/// The engine only decides *what* to show and *when* to advance:
///  - `.scroll` — a message/idle line scrolling right→left; an advance timer
///    fires when it has fully passed and picks the next thing to show.
///  - `.blink`  — a sticky, centred REC indicator that blinks (view-driven).
///  - `.blank`  — a static dim dot-grid (idle, nothing to say).
final class LEDMatrix: ObservableObject {
    enum Mode: Equatable { case blank, scroll, blink, status }

    @Published private(set) var track: [LEDColumn] = []
    @Published private(set) var mode: Mode = .blank
    @Published private(set) var trackStart: Date = .distantPast
    /// True while showing a real event (drives heatmap takeover); idle scrolls
    /// and the static grid do not count.
    @Published private(set) var isActive = false

    private let settings: LEDSettings
    private(set) var viewportCols: Int
    private var queue: [LEDEvent] = []
    private var recActive = false
    private var currentIsIdle = false
    /// Live sticky status (e.g. download %), shown when nothing higher-priority
    /// is scrolling. Updated in place as the value changes.
    private var statusText: String?
    private var statusColor: Color = .blue
    private var idleCursor = 0
    private var advanceTimer: Timer?
    private var started = false

    /// Supplies idle-ticker text (clock / streak / queue / meetings) on demand.
    var idleProvider: ((LEDIdleContent) -> String?)?

    /// Columns scrolled per second (also sets the view's pixel speed).
    var colsPerSecond: Double { max(4, settings.scrollSpeed) }

    init(settings: LEDSettings, viewportCols: Int = 48) {
        self.settings = settings
        self.viewportCols = viewportCols
    }

    func configure(viewportCols: Int) {
        guard viewportCols > 0 else { return }
        self.viewportCols = viewportCols
    }

    // MARK: Public API

    func notify(_ event: LEDEvent) {
        guard settings.enabled, settings.isEnabled(event.kind) else { return }
        if event.priority > 0 { queue.removeAll { $0.priority < event.priority } }
        queue.append(event)
        // Interrupt the static grid or an ambient idle line immediately;
        // otherwise let the current event finish, then the advance timer picks
        // this one up.
        if mode != .scroll || currentIsIdle { startNext() }
    }

    func setRecording(_ active: Bool) {
        guard settings.enabled else { return }
        recActive = active && settings.isEnabled(.micRecording)
        // REC is sticky; (re)evaluate unless a real event is mid-scroll.
        if mode != .scroll || currentIsIdle { startNext() }
    }

    /// Set (or clear, with nil) a live sticky status such as download progress.
    /// Updates in place if already showing a status, so the % can tick without
    /// resetting any animation.
    func setStatus(_ text: String?, color: Color = .blue, kind: LEDEventKind = .download) {
        guard settings.enabled else { return }
        let allowed = text != nil && settings.isEnabled(kind)
        statusText = allowed ? text : nil
        statusColor = color
        if mode == .status, let t = statusText {
            loadStatus(text: t, color: statusColor)   // update in place
        } else if mode != .scroll || currentIsIdle {
            startNext()
        }
    }

    func start() {
        guard settings.enabled, !started else { return }
        started = true
        startNext()
    }

    func stop() {
        advanceTimer?.invalidate(); advanceTimer = nil
        started = false
    }

    // MARK: State machine

    private func startNext() {
        advanceTimer?.invalidate(); advanceTimer = nil

        if !queue.isEmpty {
            let ev = queue.removeFirst()
            loadScroll(text: ev.text, color: color(for: ev.kind), idle: false)
            return
        }
        if recActive {
            loadBlink(text: "\(LEDFont.dot) REC", color: recColor)
            return
        }
        if let status = statusText {
            loadStatus(text: status, color: statusColor)
            return
        }
        if settings.idleTickerEnabled, let text = nextIdleText() {
            loadScroll(text: text, color: idleColor, idle: true)
            return
        }
        // Nothing to show — static dim grid; re-probe for idle text shortly.
        mode = .blank
        track = Array(repeating: .blank, count: viewportCols)
        currentIsIdle = false
        setActive(false)
        scheduleAdvance(after: 1.0)
    }

    /// Trailing blank columns after a message before the next loads. Small so
    /// messages flow continuously (a big trailing pad reads as "stop / blank").
    private let trailingPad = 4

    private func loadScroll(text: String, color: Color, idle: Bool) {
        let glyphs = LEDFont.columns(for: text).map { LEDColumn(bits: $0, color: color) }
        // Leading pad = a full viewport so text scrolls in from the right edge;
        // small trailing pad so the next message follows without a long blank.
        let lead = Array(repeating: LEDColumn.blank, count: viewportCols)
        let trail = Array(repeating: LEDColumn.blank, count: trailingPad)
        track = lead + glyphs + trail
        mode = .scroll
        currentIsIdle = idle
        trackStart = Date()
        setActive(!idle)
        // Fully exited once the integer offset reaches track.count - viewportCols.
        let steps = max(1, track.count - viewportCols)
        let duration = Double(steps) / colsPerSecond + 0.05
        scheduleAdvance(after: duration)
    }

    /// Static, centred, non-blinking sticky text (live status, e.g. download %).
    private func loadStatus(text: String, color: Color) {
        track = centered(text: text, color: color)
        mode = .status
        currentIsIdle = false
        trackStart = Date()
        setActive(true)
        // Sticky — no advance timer; cleared/updated via setStatus.
    }

    /// Centre `text` within the viewport, padded/clipped to viewportCols.
    private func centered(text: String, color: Color) -> [LEDColumn] {
        var cols = LEDFont.columns(for: text).map { LEDColumn(bits: $0, color: color) }
        if cols.count < viewportCols {
            let lead = (viewportCols - cols.count) / 2
            cols = Array(repeating: LEDColumn.blank, count: lead) + cols
            cols += Array(repeating: LEDColumn.blank, count: viewportCols - cols.count)
        } else {
            cols = Array(cols.prefix(viewportCols))
        }
        return cols
    }

    private func loadBlink(text: String, color: Color) {
        track = centered(text: text, color: color)
        mode = .blink
        currentIsIdle = false
        trackStart = Date()
        setActive(true)
        // No advance timer — REC stays until setRecording(false) / an event.
    }

    private func scheduleAdvance(after seconds: TimeInterval) {
        let t = Timer(timeInterval: seconds, repeats: false) { [weak self] _ in self?.startNext() }
        RunLoop.main.add(t, forMode: .common)
        advanceTimer = t
    }

    private func setActive(_ v: Bool) { if v != isActive { isActive = v } }

    private func nextIdleText() -> String? {
        let contents = LEDIdleContent.allCases.filter { settings.idleContents.contains($0) }
        guard !contents.isEmpty else { return nil }
        for _ in 0..<contents.count {
            let c = contents[idleCursor % contents.count]
            idleCursor += 1
            if let text = idleProvider?(c), !text.isEmpty { return text }
        }
        return nil
    }

    // MARK: Colours

    private var idleColor: Color { .green }
    private var recColor: Color { .red }

    private func color(for kind: LEDEventKind) -> Color {
        guard settings.colorScheme == .perEvent else { return .green }
        switch kind {
        case .download: return .blue
        case .transcription: return .orange
        case .summarise: return .green
        case .micRecording: return .red
        case .syncComplete: return .green
        case .error: return .red
        case .deviceConnect: return .teal
        }
    }
}
