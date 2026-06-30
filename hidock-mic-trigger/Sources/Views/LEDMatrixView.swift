import SwiftUI

/// Renders the LED ticker as a **fixed dot grid** (like a real LED panel): the
/// dim "off" dots stay put and only the lit dots change as the message steps
/// across, one column at a time. Drawing is done in a single `Canvas`, redrawn
/// by `TimelineView` so the column stepping is evenly paced.
///
/// Width is capped to the heatmap's grid footprint (so it never spills to full
/// window width) and aligned under the grid (past the weekday gutter).
struct LEDMatrixView: View {
    @ObservedObject var matrix: LEDMatrix
    @ObservedObject var settings: LEDSettings

    private let cell: CGFloat = 11
    private let gap: CGFloat = 3
    private var pitch: CGFloat { cell + gap }
    /// Match the heatmap: 53 week-columns and a 30pt weekday gutter.
    private let maxCols = 53
    private let leadingGutter: CGFloat = 30 + 3

    var body: some View {
        GeometryReader { geo in
            let usable = max(0, geo.size.width - leadingGutter)
            let cols = min(maxCols, max(8, Int(usable / pitch)))
            // Redraw at the column-step rate, not 60fps: the crisp grid only
            // changes when the integer offset advances a whole column, so this
            // keeps it sharp and cuts CPU — important while transcription is
            // running and competing for the main thread.
            let interval = max(0.03, 1.0 / matrix.colsPerSecond)
            TimelineView(.animation(minimumInterval: interval, paused: matrix.mode == .blank)) { tl in
                Canvas { gc, _ in draw(gc, cols: cols, now: tl.date) }
                    .frame(width: CGFloat(cols) * pitch - gap,
                           height: CGFloat(LEDFont.height) * pitch - gap)
            }
            .padding(.leading, leadingGutter)
            .onAppear { matrix.configure(viewportCols: cols); matrix.start() }
            .onChange(of: cols) { matrix.configure(viewportCols: $0) }
        }
        .frame(height: CGFloat(LEDFont.height) * pitch - gap)
    }

    private func draw(_ gc: GraphicsContext, cols: Int, now: Date) {
        let track = matrix.track
        let off = Color.secondary.opacity(0.10)

        // CRISP fixed-grid LED: the message snaps to whole columns (integer
        // offset), so every lit dot is fully on or fully off — no brightness
        // blending, no blur. The dim dots are stationary; only which ones light
        // up changes as the text steps across.
        let elapsed = max(0, now.timeIntervalSince(matrix.trackStart))
        let offset = matrix.mode == .scroll ? Int(elapsed * matrix.colsPerSecond) : 0
        let blinkOn = matrix.mode == .blink ? Int(elapsed / 0.45) % 2 == 0 : true

        for v in 0..<cols {
            let idx = v + offset
            let col: LEDColumn? = (idx >= 0 && idx < track.count) ? track[idx] : nil
            let x = CGFloat(v) * pitch
            for y in 0..<LEDFont.height {
                let path = Path(roundedRect: CGRect(x: x, y: CGFloat(y) * pitch, width: cell, height: cell), cornerRadius: 2)
                let lit = (col.map { y < $0.bits.count && $0.bits[y] } ?? false) && blinkOn
                gc.fill(path, with: .color(lit ? (col?.color ?? .clear).opacity(settings.brightness) : off))
            }
        }
    }
}
