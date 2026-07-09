import SwiftUI
import Combine

/// One column of the fixed LED grid: a colour per row, `nil` = the dot is off
/// (drawn as the constant dim grid dot). Index 0 = top row (Mon) … 6 (Sun).
struct LEDColumn: Equatable {
    var cells: [Color?]
    static let off = LEDColumn(cells: Array(repeating: nil, count: LEDFont.height))
}

/// A high-level app event the ticker announces.
struct LEDEvent {
    let kind: LEDEventKind
    let text: String
    var priority: Int = 0
}

/// Drives the LED ticker as a **real LED sign on a conveyor**: the dots never
/// move — the engine sets fixed dots on/off so content appears to scroll
/// **right → left, constantly**. Each cycle it builds a strip
/// `heatmap + gap + (REC / download% / queued messages / idle line) + gap` and
/// scrolls it; at the end it immediately starts the next cycle. So the heatmap
/// scrolls off the left, the message follows, and the heatmap scrolls back on —
/// continuous movement while the panel is shown. It runs ONLY between
/// `start()`/`stop()` (the view calls these on appear/disappear), so it costs
/// nothing when the panel isn't visible.
final class LEDMatrix: ObservableObject {
    /// Current viewport content (exactly `viewportCols` columns). The view draws
    /// this directly.
    @Published private(set) var columns: [LEDColumn] = []

    private let settings: LEDSettings
    private(set) var viewportCols: Int

    /// The heatmap, one column (7 rows) per visible week — the conveyor's base
    /// content. Updated when the meeting data changes.
    private var heatmap: [LEDColumn] = []
    private var queue: [LEDEvent] = []
    private var recActive = false
    private var statusText: String?
    private var statusColor: Color = .green

    // Scroll state
    private var filmstrip: [LEDColumn] = []
    private var offset = 0
    private var timer: Timer?
    private var idleCursor = 0

    /// Supplies idle-ticker text (clock / streak / queue / meetings).
    var idleProvider: ((LEDIdleContent) -> String?)?

    /// Columns scrolled per second (integer stepping — one dot-column per step).
    var colsPerSecond: Double { max(4, settings.scrollSpeed) }
    private var stepInterval: TimeInterval { 1.0 / colsPerSecond }

    // Colours: green everywhere except REC / errors (red).
    private var messageColor: Color { .green }
    private var recColor: Color { .red }
    private func color(for kind: LEDEventKind) -> Color {
        (kind == .error || kind == .micRecording) ? .red : .green
    }

    init(settings: LEDSettings, viewportCols: Int = 48) {
        self.settings = settings
        self.viewportCols = viewportCols
        self.columns = Array(repeating: .off, count: viewportCols)
    }

    func configure(viewportCols: Int) {
        guard viewportCols > 0 else { return }
        self.viewportCols = viewportCols
    }

    // MARK: Public API — these just update state; the running loop folds them in.

    func setHeatmap(_ cols: [LEDColumn]) {
        guard cols != heatmap else { return }
        heatmap = cols
    }

    func notify(_ event: LEDEvent) {
        guard settings.enabled, settings.isEnabled(event.kind) else { return }
        if event.priority > 0 { queue.removeAll { $0.priority < event.priority } }
        queue.append(event)
    }

    func setRecording(_ active: Bool) {
        recActive = active && settings.isEnabled(.micRecording)
    }

    func setStatus(_ text: String?, color: Color = .green, kind: LEDEventKind = .download) {
        statusText = (text != nil && settings.isEnabled(kind)) ? text : nil
        statusColor = color
    }

    /// Start/stop the conveyor. The view calls these on appear/disappear so the
    /// ticker only runs (and only costs CPU) while it's on screen.
    func start() {
        guard settings.enabled, timer == nil else { return }
        beginCycle()
        let t = Timer(timeInterval: stepInterval, repeats: true) { [weak self] _ in self?.step() }
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    func stop() {
        timer?.invalidate(); timer = nil
    }

    // MARK: Engine

    private func step() {
        guard !filmstrip.isEmpty else { beginCycle(); return }
        offset += 1
        if offset >= filmstrip.count {
            // Completed a full pass. Rebuild to fold in any new content — the
            // seam is at the heatmap's start in BOTH the old and new strip, so
            // it's continuous (no jump). Everything that differs (messages) is
            // off-screen at this moment.
            beginCycle()
            return
        }
        renderWindow()
    }

    /// Build one conveyor pass: heatmap, then whatever's pending, then a gap so
    /// the heatmap of the next pass scrolls back on cleanly.
    private func beginCycle() {
        let gap = Array(repeating: LEDColumn.off, count: 6)
        var strip = fullHeatmapColumns() + gap
        var hadContent = false
        if recActive {
            strip += messageColumns("\(LEDFont.dot) REC", color: recColor) + gap
            hadContent = true
        }
        if let s = statusText {
            strip += messageColumns(s, color: statusColor) + gap
            hadContent = true
        }
        if !queue.isEmpty {
            for ev in queue { strip += messageColumns(ev.text, color: color(for: ev.kind)) + gap }
            queue.removeAll()
            hadContent = true
        }
        if !hadContent, settings.idleTickerEnabled, let t = nextIdleText() {
            strip += messageColumns(t, color: messageColor) + gap
        }
        filmstrip = strip
        offset = 0
        renderWindow()
    }

    private func fullHeatmapColumns() -> [LEDColumn] {
        heatmap.isEmpty ? Array(repeating: .off, count: viewportCols) : heatmap
    }

    private func renderWindow() {
        let n = filmstrip.count
        guard n > 0 else { return }
        // Circular read: the strip's tail (trailing gap) flows seamlessly back
        // into its head (the heatmap), so the loop never cuts/jumps.
        var window = [LEDColumn](); window.reserveCapacity(viewportCols)
        for i in 0..<viewportCols { window.append(filmstrip[(offset + i) % n]) }
        columns = window
    }

    // MARK: Column builders

    /// Message glyph columns: the 5-row font in rows 1–5 (Tue–Sat), Mon/Sun off.
    private func messageColumns(_ text: String, color: Color) -> [LEDColumn] {
        LEDFont.columns5(for: text).map { glyphCol in
            var cells = [Color?](repeating: nil, count: LEDFont.height)
            for r in 0..<LEDFont.height5 where r < glyphCol.count && glyphCol[r] {
                cells[r + 1] = color
            }
            return LEDColumn(cells: cells)
        }
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
}
