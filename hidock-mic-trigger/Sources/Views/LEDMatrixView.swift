import SwiftUI

/// Renders the LED ticker. Drawing is time-based: a `TimelineView(.animation)`
/// redraws a single `Canvas` ~60fps and the horizontal offset is derived from
/// elapsed time × speed, so scrolling glides smoothly (sub-pixel) rather than
/// stepping a whole column per tick. Pixel size matches the heatmap.
struct LEDMatrixView: View {
    @ObservedObject var matrix: LEDMatrix
    @ObservedObject var settings: LEDSettings

    private let cell: CGFloat = 11
    private let gap: CGFloat = 3
    private var pitch: CGFloat { cell + gap }

    var body: some View {
        GeometryReader { geo in
            let cols = max(8, Int(geo.size.width / pitch))
            TimelineView(.animation(minimumInterval: 1.0 / 60.0, paused: matrix.mode == .blank)) { tl in
                Canvas { gc, size in draw(gc, size: size, now: tl.date) }
            }
            .onAppear { matrix.configure(viewportCols: cols); matrix.start() }
            .onChange(of: cols) { matrix.configure(viewportCols: $0) }
        }
        .frame(height: CGFloat(LEDFont.height) * pitch - gap)
    }

    private func draw(_ gc: GraphicsContext, size: CGSize, now: Date) {
        let track = matrix.track
        guard !track.isEmpty else { return }
        let off = Color.secondary.opacity(0.10)

        // Continuous scroll offset (columns) for .scroll; fixed for .blink/.blank.
        var offsetCols: Double = 0
        if matrix.mode == .scroll {
            offsetCols = max(0, now.timeIntervalSince(matrix.trackStart)) * matrix.colsPerSecond
        }
        // Blink phase for the sticky REC indicator.
        let blinkOn = matrix.mode == .blink
            ? Int(max(0, now.timeIntervalSince(matrix.trackStart)) / 0.45) % 2 == 0
            : true

        for (i, col) in track.enumerated() {
            let x = (CGFloat(i) - CGFloat(offsetCols)) * pitch
            if x <= -pitch || x >= size.width { continue }
            for y in 0..<LEDFont.height {
                let rect = CGRect(x: x, y: CGFloat(y) * pitch, width: cell, height: cell)
                let lit = (y < col.bits.count && col.bits[y]) && blinkOn
                gc.fill(Path(roundedRect: rect, cornerRadius: 2),
                        with: .color(lit ? col.color.opacity(settings.brightness) : off))
            }
        }
    }
}
