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
    var priority: Int = 0       // higher clears a lower-priority scroll in progress
}

/// Drives the LED ticker: a queue of scrolling messages, a sticky blinking REC
/// indicator while the mic is held, and an idle ticker. Produces `visible` —
/// the current viewport columns — which `LEDMatrixView` renders. The timer only
/// runs while there's something to show.
final class LEDMatrix: ObservableObject {
    @Published private(set) var visible: [LEDColumn]
    /// True while there's something to display (scrolling message, queued
    /// message, or sticky REC) — used to drive heatmap "takeover".
    @Published private(set) var isActive = false

    private let settings: LEDSettings
    private var viewportCols: Int
    private var scrollBuffer: [LEDColumn] = []
    private var pos = 0
    private var queue: [LEDEvent] = []
    private var timer: Timer?
    private var tick = 0
    private var recActive = false
    private var idleCursor = 0
    private var framesSinceIdle = 0

    /// Supplies idle-ticker text (clock / streak / queue / meetings) on demand.
    var idleProvider: ((LEDIdleContent) -> String?)?

    init(settings: LEDSettings, viewportCols: Int = 48) {
        self.settings = settings
        self.viewportCols = viewportCols
        self.visible = Array(repeating: .blank, count: viewportCols)
    }

    func configure(viewportCols: Int) {
        guard viewportCols > 0, viewportCols != self.viewportCols else { return }
        self.viewportCols = viewportCols
        if scrollBuffer.isEmpty { visible = Array(repeating: .blank, count: viewportCols) }
    }

    // MARK: Public API

    /// Announce an event. Ignored if the feature or this event kind is off.
    func notify(_ event: LEDEvent) {
        guard settings.enabled, settings.isEnabled(event.kind) else { return }
        if event.priority > 0 { queue.removeAll { $0.priority < event.priority } }
        queue.append(event)
        ensureRunning()
    }

    /// Mic-trigger holding the HiDock input — show a sticky blinking REC.
    func setRecording(_ active: Bool) {
        guard settings.enabled else { return }
        recActive = active && settings.isEnabled(.micRecording)
        ensureRunning()
    }

    func start() { ensureRunning() }
    func stop() { timer?.invalidate(); timer = nil }

    // MARK: Engine

    private func ensureRunning() {
        guard settings.enabled, timer == nil else { return }
        let interval = 1.0 / max(4.0, settings.scrollSpeed)
        let t = Timer(timeInterval: interval, repeats: true) { [weak self] _ in self?.step() }
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    private func updateActive() {
        let active = !scrollBuffer.isEmpty || !queue.isEmpty || recActive
        if active != isActive { isActive = active }
    }

    private func step() {
        tick += 1
        updateActive()
        // Currently scrolling a message?
        if !scrollBuffer.isEmpty {
            renderWindow()
            pos += 1
            if pos > scrollBuffer.count - viewportCols { scrollBuffer = []; pos = 0 }
            return
        }
        // Next queued message?
        if !queue.isEmpty {
            let ev = queue.removeFirst()
            loadMessage(ev.text, color: color(for: ev.kind))
            return
        }
        // Sticky REC indicator (blinks).
        if recActive {
            let on = (tick / max(1, Int(settings.scrollSpeed / 3))) % 2 == 0
            visible = recFrame(on: on)
            return
        }
        // Idle ticker.
        if settings.idleTickerEnabled, !settings.idleContents.isEmpty {
            framesSinceIdle += 1
            if framesSinceIdle >= Int(settings.scrollSpeed * 3) {  // ~3s gap between idle lines
                framesSinceIdle = 0
                if let text = nextIdleText() { loadMessage(text, color: idleColor) }
            } else {
                blankIfNeeded()
            }
            return
        }
        // Nothing to show — blank and park the timer.
        blankIfNeeded()
        stop()
    }

    private func loadMessage(_ text: String, color: Color) {
        let glyphs = LEDFont.columns(for: text).map { LEDColumn(bits: $0, color: color) }
        let pad = Array(repeating: LEDColumn.blank, count: viewportCols)
        scrollBuffer = pad + glyphs + pad
        pos = 0
        renderWindow()
        pos = 1
    }

    private func renderWindow() {
        let end = min(pos + viewportCols, scrollBuffer.count)
        var window = Array(scrollBuffer[pos..<end])
        if window.count < viewportCols {
            window += Array(repeating: .blank, count: viewportCols - window.count)
        }
        visible = window
    }

    private func blankIfNeeded() {
        let blanks = Array(repeating: LEDColumn.blank, count: viewportCols)
        if visible != blanks { visible = blanks }
    }

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

    /// "● REC" centred-ish, used for the sticky blink.
    private func recFrame(on: Bool) -> [LEDColumn] {
        let text = on ? "\(LEDFont.dot) REC" : "  REC"
        let glyphs = LEDFont.columns(for: text).map { LEDColumn(bits: $0, color: recColor) }
        var frame = glyphs
        if frame.count < viewportCols {
            frame += Array(repeating: LEDColumn.blank, count: viewportCols - frame.count)
        } else if frame.count > viewportCols {
            frame = Array(frame.prefix(viewportCols))
        }
        return frame
    }

    // MARK: Colours

    private var idleColor: Color { settings.colorScheme == .green ? .green : .green }
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
