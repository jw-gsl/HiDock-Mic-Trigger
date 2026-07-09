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
    /// Cached "home" strip (trailing-week heatmap + gap). Rebuilt only when the
    /// heatmap changes — avoids rebuilding ~50 columns every loop (the seam
    /// jitter).
    private var baseStrip: [LEDColumn] = []
    private var baseDirty = true
    /// Whether the current pass carries a message (so a graceful stop knows when
    /// it's back to a clean heatmap-only pass).
    private var cycleHasContent = false
    /// Set by returnHomeThenStop: run once the conveyor is back at home.
    private var pendingHomeStop: (() -> Void)?

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
        guard viewportCols > 0, viewportCols != self.viewportCols else { return }
        self.viewportCols = viewportCols
        baseDirty = true   // home width changed → rebuild cached strip
    }

    // MARK: Public API — these just update state; the running loop folds them in.

    func setHeatmap(_ cols: [LEDColumn]) {
        guard cols != heatmap else { return }
        heatmap = cols
        baseDirty = true   // heatmap changed → rebuild cached home strip
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
        pendingHomeStop = nil
        timer?.invalidate(); timer = nil
    }

    /// Graceful stop: keep scrolling until the conveyor is back at home (the full
    /// heatmap), then stop and run `completion`. Used when toggling the LED off so
    /// it slides back to the resting heatmap instead of freezing/jumping mid-pass.
    func returnHomeThenStop(_ completion: @escaping () -> Void) {
        guard timer != nil else { completion(); return }   // not running → nothing to wind down
        pendingHomeStop = completion
    }

    // MARK: Engine

    private func step() {
        guard !filmstrip.isEmpty else { beginCycle(); return }
        offset += 1
        if offset >= filmstrip.count {
            // Completed a full pass — we're back at the seam (home = heatmap start).
            if let done = pendingHomeStop {
                // Graceful stop: settle on the bare heatmap (no trailing message)
                // and stop. This is the resting frame the static heatmap matches,
                // so the hand-off is seamless — no jump.
                pendingHomeStop = nil
                rebuildBaseIfNeeded()
                filmstrip = baseStrip
                offset = 0
                renderWindow()
                timer?.invalidate(); timer = nil
                done()
                return
            }
            // Rebuild to fold in any new content — the seam is at the heatmap's
            // start in BOTH the old and new strip, so it's continuous (no jump).
            beginCycle()
            return
        }
        renderWindow()
    }

    /// Build one conveyor pass: heatmap, then whatever's pending, then a gap so
    /// the heatmap of the next pass scrolls back on cleanly.
    private func beginCycle() {
        rebuildBaseIfNeeded()
        let gap = Array(repeating: LEDColumn.off, count: 6)
        var strip = baseStrip                 // heatmap (home) + gap, cached
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
            hadContent = true
        }
        filmstrip = strip
        cycleHasContent = hadContent
        offset = 0
        renderWindow()
    }

    /// Rebuild the cached home strip (trailing-week heatmap + a trailing gap).
    /// Only runs when the heatmap actually changed, so a plain loop doesn't
    /// rebuild ~50 columns every pass (that rebuild cost is the seam jitter).
    private func rebuildBaseIfNeeded() {
        guard baseDirty else { return }
        baseDirty = false
        baseStrip = homeColumns() + Array(repeating: LEDColumn.off, count: 6)
    }

    /// The resting view: the trailing `viewportCols` weeks — identical to what the
    /// static heatmap grid shows, so stopping here hands off without a jump.
    private func homeColumns() -> [LEDColumn] {
        if heatmap.isEmpty { return Array(repeating: .off, count: viewportCols) }
        if heatmap.count >= viewportCols { return Array(heatmap.suffix(viewportCols)) }
        return heatmap + Array(repeating: .off, count: viewportCols - heatmap.count)
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
