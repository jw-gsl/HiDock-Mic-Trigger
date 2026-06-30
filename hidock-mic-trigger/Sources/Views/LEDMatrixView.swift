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

        // Continuous offset. For .scroll we keep the fractional part and blend
        // adjacent columns so lit dots fade across the FIXED grid — the dim dots
        // never move, only their brightness does, which reads as smooth motion
        // without the column-snap jerkiness.
        let elapsed = max(0, now.timeIntervalSince(matrix.trackStart))
        let offsetCols = matrix.mode == .scroll ? elapsed * matrix.colsPerSecond : 0
        let base = Int(floor(offsetCols))
        let frac = offsetCols - Double(base)
        let blinkOn = matrix.mode == .blink ? Int(elapsed / 0.45) % 2 == 0 : true

        func litColor(_ idx: Int, _ y: Int) -> Color? {
            guard idx >= 0, idx < track.count else { return nil }
            let c = track[idx]
            return (y < c.bits.count && c.bits[y]) ? c.color : nil
        }

        for v in 0..<cols {
            let x = CGFloat(v) * pitch
            for y in 0..<LEDFont.height {
                let path = Path(roundedRect: CGRect(x: x, y: CGFloat(y) * pitch, width: cell, height: cell), cornerRadius: 2)
                // Stationary dim dot underneath.
                gc.fill(path, with: .color(off))
                guard blinkOn else { continue }
                if matrix.mode == .scroll {
                    // Blend this grid cell between the two source columns it sits
                    // between as the message slides (anti-aliased motion).
                    let c0 = litColor(v + base, y)
                    let c1 = litColor(v + base + 1, y)
                    let i0 = c0 != nil ? (1 - frac) : 0
                    let i1 = c1 != nil ? frac : 0
                    let intensity = i0 + i1
                    if intensity > 0.01 {
                        let color = (c1 ?? c0) ?? .clear
                        gc.fill(path, with: .color(color.opacity(settings.brightness * intensity)))
                    }
                } else if let c = litColor(v, y) {
                    gc.fill(path, with: .color(c.opacity(settings.brightness)))
                }
            }
        }
    }
}
