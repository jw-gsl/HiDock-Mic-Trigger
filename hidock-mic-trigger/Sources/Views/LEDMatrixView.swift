import SwiftUI

/// Renders the LED ticker: a fixed grid of dots. Each dot is either **off**
/// (one constant dim grid dot) or **on** (the colour the engine put in that
/// cell — heatmap intensity green, brightest green message text, or red REC/
/// errors). The engine (`LEDMatrix`) publishes `columns`; this view just paints
/// them, so nothing here dims or moves — the apparent motion is the engine
/// flipping dots on/off. When parked on the heatmap the engine stops updating,
/// so this view stops redrawing (zero idle cost).
struct LEDMatrixView: View {
    @ObservedObject var matrix: LEDMatrix
    /// Column count decided by the parent so the month-label row above lines up.
    var fixedCols: Int? = nil
    /// The heatmap as LED columns (per-row colour; nil = off) — the engine's
    /// resting content. Updated when the meeting data changes.
    var heatmap: [LEDColumn] = []

    private let cell: CGFloat = 11
    private let gap: CGFloat = 3
    private var pitch: CGFloat { cell + gap }
    private let maxCols = 53
    private let gridRows = 7
    /// The constant "off" dot — matches the heatmap's empty-day dot so the LED
    /// panel is pixel-identical to the heatmap at rest.
    private let offColor = Color.secondary.opacity(0.12)

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
        Canvas { gc, _ in draw(gc, cols: cols) }
            .frame(width: CGFloat(cols) * pitch - gap,
                   height: CGFloat(gridRows) * pitch - gap,
                   alignment: .leading)
            .onAppear {
                matrix.configure(viewportCols: cols)
                matrix.setHeatmap(heatmap)
                matrix.start()
            }
            .onChange(of: cols) { matrix.configure(viewportCols: $0) }
            .onChange(of: heatmap) { matrix.setHeatmap($0) }
            .onDisappear { matrix.stop() }   // no off-screen spinning
    }

    private func draw(_ gc: GraphicsContext, cols: Int) {
        let visible = matrix.columns
        for v in 0..<cols {
            let col = v < visible.count ? visible[v] : LEDColumn.off
            let x = CGFloat(v) * pitch
            for y in 0..<gridRows {
                let lit = y < col.cells.count ? col.cells[y] : nil
                let path = Path(roundedRect: CGRect(x: x, y: CGFloat(y) * pitch, width: cell, height: cell), cornerRadius: 2)
                gc.fill(path, with: .color(lit ?? offColor))
            }
        }
    }
}
