import SwiftUI

/// Renders the LED ticker as a **fixed dot grid** (like a real LED panel): the
/// dim "off" dots stay put and only the lit dots change as the message steps
/// across, one column at a time (crisp — no blur). Drawing is a single `Canvas`
/// redrawn by `TimelineView` at the column-step rate.
///
/// The grid is 7 rows tall to line up with the heatmap's Mon–Sun labels, but the
/// text uses the compact 5-row font placed in the middle **Tue–Sat** band, so it
/// reads as a punchy ticker inset in the calendar. The parent supplies the month
/// row and weekday-label gutter around it.
struct LEDMatrixView: View {
    @ObservedObject var matrix: LEDMatrix
    @ObservedObject var settings: LEDSettings
    /// Column count decided by the parent so the month-label row above can use
    /// the exact same geometry (nil → self-size from available width).
    var fixedCols: Int? = nil
    /// Heatmap day colours per visible column ([col][row 0–6]) rendered as the
    /// unlit state, so at rest the LED panel is pixel-identical to the heatmap
    /// it replaces — nothing appears to move or vanish on load. nil → plain
    /// dim dots (standalone use).
    var background: [[Color]]? = nil

    private let cell: CGFloat = 11
    private let gap: CGFloat = 3
    private var pitch: CGFloat { cell + gap }
    private let maxCols = 53
    /// Grid rows (Mon–Sun) for alignment; text sits in rows 1–5 (Tue–Sat).
    private let gridRows = 7
    private let rowOffset = 1

    var body: some View {
        if let cols = fixedCols {
            grid(cols: cols)
        } else {
            GeometryReader { geo in
                grid(cols: min(maxCols, max(8, Int(geo.size.width / pitch))))
            }
            .frame(height: CGFloat(gridRows) * pitch - gap)
        }
    }

    private func grid(cols: Int) -> some View {
        // Redraw at the column-step rate, not 60fps: the crisp grid only
        // changes when the integer offset advances a whole column, so this
        // stays sharp and cuts CPU (important while transcription runs).
        let interval = max(0.03, 1.0 / matrix.colsPerSecond)
        return TimelineView(.animation(minimumInterval: interval, paused: matrix.mode == .blank)) { tl in
            Canvas { gc, _ in draw(gc, cols: cols, now: tl.date) }
                .frame(width: CGFloat(cols) * pitch - gap,
                       height: CGFloat(gridRows) * pitch - gap,
                       alignment: .leading)
        }
        .onAppear { matrix.configure(viewportCols: cols); matrix.start() }
        .onChange(of: cols) { matrix.configure(viewportCols: $0) }
    }

    /// Unlit colour for a dot. With a heatmap background: the real day colour,
    /// except in the Tue–Sat text band while a message is showing, where the
    /// day colour is ghosted so the text keeps contrast (the band "powers on"
    /// under the ticker, Mon/Sun calendar chrome stays lit throughout).
    private func offColor(col v: Int, row y: Int) -> Color {
        guard let bg = background, v < bg.count, y < bg[v].count else {
            return Color.secondary.opacity(0.10)
        }
        let day = bg[v][y]
        let textShowing = matrix.mode != .blank
        let inTextBand = y >= rowOffset && y < rowOffset + LEDFont.height5
        return (textShowing && inTextBand) ? day.opacity(0.30) : day
    }

    private func draw(_ gc: GraphicsContext, cols: Int, now: Date) {
        let track = matrix.track

        let elapsed = max(0, now.timeIntervalSince(matrix.trackStart))
        let offset = matrix.mode == .scroll ? Int(elapsed * matrix.colsPerSecond) : 0
        let blinkOn = matrix.mode == .blink ? Int(elapsed / 0.45) % 2 == 0 : true

        for v in 0..<cols {
            let idx = v + offset
            let col: LEDColumn? = (idx >= 0 && idx < track.count) ? track[idx] : nil
            let x = CGFloat(v) * pitch
            for y in 0..<gridRows {
                // Map grid row → 5-row glyph row (rows 0/Mon and 6/Sun keep chrome).
                let gr = y - rowOffset
                let lit = (col.map { gr >= 0 && gr < $0.bits.count && $0.bits[gr] } ?? false) && blinkOn
                let path = Path(roundedRect: CGRect(x: x, y: CGFloat(y) * pitch, width: cell, height: cell), cornerRadius: 2)
                gc.fill(path, with: .color(lit ? (col?.color ?? .clear).opacity(settings.brightness) : offColor(col: v, row: y)))
            }
        }
    }
}
