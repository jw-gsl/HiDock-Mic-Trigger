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
            TimelineView(.animation(minimumInterval: 1.0 / 60.0, paused: matrix.mode == .blank)) { tl in
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

        // Integer column offset → the message snaps to grid columns (authentic
        // LED look). 0 for the static grid / blink.
        var offset = 0
        if matrix.mode == .scroll {
            offset = Int(max(0, now.timeIntervalSince(matrix.trackStart)) * matrix.colsPerSecond)
        }
        let blinkOn = matrix.mode == .blink
            ? Int(max(0, now.timeIntervalSince(matrix.trackStart)) / 0.45) % 2 == 0
            : true

        // Fixed grid: every viewport cell is drawn at a stationary position;
        // only whether it's lit changes.
        for v in 0..<cols {
            let idx = v + offset
            let col: LEDColumn? = (idx >= 0 && idx < track.count) ? track[idx] : nil
            let x = CGFloat(v) * pitch
            for y in 0..<LEDFont.height {
                let rect = CGRect(x: x, y: CGFloat(y) * pitch, width: cell, height: cell)
                let lit = (col.map { y < $0.bits.count && $0.bits[y] } ?? false) && blinkOn
                let color = lit ? (col?.color ?? .clear).opacity(settings.brightness) : off
                gc.fill(Path(roundedRect: rect, cornerRadius: 2), with: .color(color))
            }
        }
    }
}
