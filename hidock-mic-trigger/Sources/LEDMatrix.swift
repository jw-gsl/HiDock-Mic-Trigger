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

/// Drives the LED ticker as a **real LED sign**: the dots never move — the
/// engine sets each fixed dot on/off to create the illusion of content scrolling
/// **right → left**. It publishes `columns` (the current viewport, one `LEDColumn`
/// per grid column); the view just paints them. At rest it shows the heatmap and
/// stops its timer (zero idle cost); an event scrolls a message through the
/// middle rows and returns to the heatmap.
final class LEDMatrix: ObservableObject {
    /// Current viewport content (exactly `viewportCols` columns). The view draws
    /// this directly — no sampling/offset logic in the view.
    @Published private(set) var columns: [LEDColumn] = []
    /// True while a real event/REC is showing (kept for the heatmap-takeover
    /// check in the view).
    @Published private(set) var isActive = false

    private let settings: LEDSettings
    private(set) var viewportCols: Int

    /// Resting content — the heatmap, one column (7 rows) per visible week.
    private var heatmap: [LEDColumn] = []
    private var queue: [LEDEvent] = []
    private var recActive = false
    private var statusText: String?
    private var statusColor: Color = .green

    // Scroll state
    private var filmstrip: [LEDColumn] = []
    private var offset = 0
    private var scrolling = false
    private var currentIsIdle = false

    // Timing
    private var timer: Timer?
    private var idleTimer: Timer?
    private var tick = 0
    private var parkedTicks = 0
    private var idleCursor = 0
    private var started = false

    /// Supplies idle-ticker text (clock / streak / queue / meetings) on demand.
    var idleProvider: ((LEDIdleContent) -> String?)?

    /// Columns scrolled per second (integer stepping — one dot-column per step).
    var colsPerSecond: Double { max(4, settings.scrollSpeed) }
    private var stepInterval: TimeInterval { 1.0 / colsPerSecond }

    // Colours
    private let offCell: Color? = nil
    private var messageColor: Color { .green }              // brightest green
    private var recColor: Color { .red }
    private func color(for kind: LEDEventKind) -> Color {
        // Green everywhere except REC / errors (red). Blue/amber dropped.
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
        if !scrolling { renderParked() }
    }

    // MARK: Public API

    /// Set the resting heatmap content (per-row colours; nil = off).
    func setHeatmap(_ cols: [LEDColumn]) {
        guard cols != heatmap else { return }
        heatmap = cols
        if !scrolling && !recActive && statusText == nil { renderParked() }
    }

    func notify(_ event: LEDEvent) {
        guard settings.enabled, settings.isEnabled(event.kind) else { return }
        if event.priority > 0 { queue.removeAll { $0.priority < event.priority } }
        queue.append(event)
        ensureRunning()
    }

    func setRecording(_ active: Bool) {
        guard settings.enabled else { return }
        recActive = active && settings.isEnabled(.micRecording)
        ensureRunning()
    }

    /// Live sticky status (e.g. download %), shown centred while active.
    func setStatus(_ text: String?, color: Color = .green, kind: LEDEventKind = .download) {
        guard settings.enabled else { return }
        statusText = (text != nil && settings.isEnabled(kind)) ? text : nil
        statusColor = color
        ensureRunning()
    }

    func start() { ensureRunning() }
    func stop() {
        timer?.invalidate(); timer = nil
        idleTimer?.invalidate(); idleTimer = nil
        started = false
    }

    // MARK: Engine

    private func ensureRunning() {
        guard settings.enabled else { return }
        started = true
        if timer == nil {
            let t = Timer(timeInterval: stepInterval, repeats: true) { [weak self] _ in self?.step() }
            RunLoop.main.add(t, forMode: .common)
            timer = t
        }
    }

    private func setActive(_ v: Bool) { if v != isActive { isActive = v } }

    /// Only publish (and thus redraw) when the frame actually changed — critical
    /// so parked/blink states don't churn the view.
    private func setColumns(_ new: [LEDColumn]) { if new != columns { columns = new } }

    private func stopFastTimer() { timer?.invalidate(); timer = nil }

    /// The fast (per-column-step) timer runs ONLY while there's motion to show —
    /// scrolling a message or blinking REC. Static states (heatmap / download
    /// status) render once and stop it, so idle cost is zero.
    private func step() {
        tick += 1
        if scrolling { advanceScroll(); return }
        if !queue.isEmpty {
            let ev = queue.removeFirst()
            beginScroll(text: ev.text, color: color(for: ev.kind), idle: false)
            return
        }
        if recActive { renderREC(); return }               // blink — keep ticking
        if let status = statusText {                        // static — render once, stop
            renderCentered(text: status, color: statusColor)
            setActive(true)
            stopFastTimer()
            return
        }
        // Heatmap rest — render once, stop the timer, and (if enabled) arm the
        // idle ticker via a slow one-shot rather than spinning the fast timer.
        renderParked()
        setActive(false)
        stopFastTimer()
        armIdle()
    }

    private func armIdle() {
        idleTimer?.invalidate(); idleTimer = nil
        guard settings.idleTickerEnabled, !settings.idleContents.isEmpty else { return }
        let t = Timer(timeInterval: 5, repeats: false) { [weak self] _ in
            guard let self = self,
                  !self.scrolling, self.queue.isEmpty, !self.recActive, self.statusText == nil,
                  let text = self.nextIdleText() else { return }
            self.ensureRunning()
            self.beginScroll(text: text, color: self.messageColor, idle: true)
        }
        RunLoop.main.add(t, forMode: .common)
        idleTimer = t
    }

    // MARK: Scroll

    private func beginScroll(text: String, color: Color, idle: Bool) {
        let rest = restColumns()
        let gap = Array(repeating: LEDColumn.off, count: 3)
        filmstrip = rest + gap + messageColumns(text, color: color) + gap + rest
        offset = 0
        scrolling = true
        currentIsIdle = idle
        parkedTicks = 0
        setActive(!idle)
        renderWindow()
        offset = 1
    }

    private func advanceScroll() {
        if offset > filmstrip.count - viewportCols {
            scrolling = false
            filmstrip = []
            step()               // immediately decide the next state (queue / park)
            return
        }
        renderWindow()
        offset += 1
    }

    private func renderWindow() {
        let end = min(offset + viewportCols, filmstrip.count)
        var window = Array(filmstrip[max(0, offset)..<end])
        if window.count < viewportCols {
            window += Array(repeating: .off, count: viewportCols - window.count)
        }
        setColumns(window)
    }

    // MARK: Parked / centred renders

    private func restColumns() -> [LEDColumn] {
        if heatmap.count >= viewportCols { return Array(heatmap.suffix(viewportCols)) }
        return heatmap + Array(repeating: .off, count: viewportCols - heatmap.count)
    }

    private func renderParked() { setColumns(restColumns()) }

    private func renderCentered(text: String, color: Color) {
        setColumns(centeredColumns(text, color: color))
    }

    private func renderREC() {
        // Blink the red REC roughly twice a second.
        let on = (tick / max(1, Int(colsPerSecond / 2))) % 2 == 0
        setColumns(on ? centeredColumns("\(LEDFont.dot) REC", color: recColor)
                      : Array(repeating: .off, count: viewportCols))
        setActive(true)
    }

    // MARK: Column builders

    /// Message glyph columns: the 5-row font placed in rows 1–5 (Tue–Sat);
    /// Mon/Sun rows off. Lit dots use `color`.
    private func messageColumns(_ text: String, color: Color) -> [LEDColumn] {
        LEDFont.columns5(for: text).map { glyphCol in
            var cells = [Color?](repeating: nil, count: LEDFont.height)
            for r in 0..<LEDFont.height5 where r < glyphCol.count && glyphCol[r] {
                cells[r + 1] = color               // +1 → Tue–Sat band
            }
            return LEDColumn(cells: cells)
        }
    }

    private func centeredColumns(_ text: String, color: Color) -> [LEDColumn] {
        var cols = messageColumns(text, color: color)
        if cols.count < viewportCols {
            let lead = (viewportCols - cols.count) / 2
            cols = Array(repeating: LEDColumn.off, count: lead) + cols
            cols += Array(repeating: LEDColumn.off, count: viewportCols - cols.count)
        } else {
            cols = Array(cols.prefix(viewportCols))
        }
        return cols
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
