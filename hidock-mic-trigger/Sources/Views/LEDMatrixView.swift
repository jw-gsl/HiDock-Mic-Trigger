import SwiftUI

/// Renders the LED ticker as a single `Canvas` (one draw pass — far cheaper than
/// hundreds of live SwiftUI cells at ~20fps). Pixel size matches the heatmap so
/// it can occupy the same strip seamlessly.
struct LEDMatrixView: View {
    @ObservedObject var matrix: LEDMatrix
    @ObservedObject var settings: LEDSettings

    private let cell: CGFloat = 11
    private let gap: CGFloat = 3
    private var pitch: CGFloat { cell + gap }

    var body: some View {
        let cols = matrix.visible
        Canvas { ctx, _ in
            let off = Color.secondary.opacity(0.10)
            for (x, col) in cols.enumerated() {
                for y in 0..<LEDFont.height {
                    let rect = CGRect(x: CGFloat(x) * pitch, y: CGFloat(y) * pitch, width: cell, height: cell)
                    let path = Path(roundedRect: rect, cornerRadius: 2)
                    let lit = y < col.bits.count && col.bits[y]
                    ctx.fill(path, with: .color(lit ? col.color.opacity(settings.brightness) : off))
                }
            }
        }
        .frame(width: CGFloat(cols.count) * pitch - gap,
               height: CGFloat(LEDFont.height) * pitch - gap)
        .onAppear { matrix.start() }
    }
}
