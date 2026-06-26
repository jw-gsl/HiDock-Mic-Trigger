import Foundation

/// A 5×7 dot-matrix font for the LED ticker. Glyphs are authored as 7 rows of
/// 5 characters (`#` = lit, space = off) — far easier to read/edit than packed
/// hex columns — and converted to column bitmaps on demand.
///
/// The ticker is 7 pixels tall (the heatmap's 7 rows), so these map 1:1 onto
/// the grid. Letters are uppercase only (classic ticker look); lowercase input
/// is upcased. Unknown characters fall back to a blank.
enum LEDFont {
    /// One glyph column = 7 booleans, top (row 0) → bottom (row 6).
    typealias Column = [Bool]

    static let width = 5
    static let height = 7
    /// Blank column inserted between glyphs.
    static let spacing = 1

    /// Columns for a whole string: each glyph's 5 columns + a spacer.
    static func columns(for text: String) -> [Column] {
        var out: [Column] = []
        for ch in text.uppercased() {
            let rows = glyph(ch).map(Array.init)   // [[Character]]
            for c in 0..<width {
                var col = Column(repeating: false, count: height)
                for r in 0..<height where r < rows.count {
                    let line = rows[r]
                    col[r] = c < line.count && line[c] != " "
                }
                out.append(col)
            }
            out.append(Column(repeating: false, count: height))  // spacer
        }
        return out
    }

    private static func glyph(_ ch: Character) -> [String] {
        glyphs[ch] ?? glyphs[" "]!
    }

    /// 7×5 patterns. Keep rows exactly 5 chars wide.
    static let glyphs: [Character: [String]] = [
        " ": ["     ", "     ", "     ", "     ", "     ", "     ", "     "],
        "A": [" ### ", "#   #", "#   #", "#####", "#   #", "#   #", "#   #"],
        "B": ["#### ", "#   #", "#   #", "#### ", "#   #", "#   #", "#### "],
        "C": [" ####", "#    ", "#    ", "#    ", "#    ", "#    ", " ####"],
        "D": ["#### ", "#   #", "#   #", "#   #", "#   #", "#   #", "#### "],
        "E": ["#####", "#    ", "#    ", "#### ", "#    ", "#    ", "#####"],
        "F": ["#####", "#    ", "#    ", "#### ", "#    ", "#    ", "#    "],
        "G": [" ####", "#    ", "#    ", "#  ##", "#   #", "#   #", " ####"],
        "H": ["#   #", "#   #", "#   #", "#####", "#   #", "#   #", "#   #"],
        "I": ["#####", "  #  ", "  #  ", "  #  ", "  #  ", "  #  ", "#####"],
        "J": ["  ###", "   # ", "   # ", "   # ", "#  # ", "#  # ", " ##  "],
        "K": ["#   #", "#  # ", "# #  ", "##   ", "# #  ", "#  # ", "#   #"],
        "L": ["#    ", "#    ", "#    ", "#    ", "#    ", "#    ", "#####"],
        "M": ["#   #", "## ##", "# # #", "# # #", "#   #", "#   #", "#   #"],
        "N": ["#   #", "##  #", "# # #", "#  ##", "#   #", "#   #", "#   #"],
        "O": [" ### ", "#   #", "#   #", "#   #", "#   #", "#   #", " ### "],
        "P": ["#### ", "#   #", "#   #", "#### ", "#    ", "#    ", "#    "],
        "Q": [" ### ", "#   #", "#   #", "#   #", "# # #", "#  # ", " ## #"],
        "R": ["#### ", "#   #", "#   #", "#### ", "# #  ", "#  # ", "#   #"],
        "S": [" ####", "#    ", "#    ", " ### ", "    #", "    #", "#### "],
        "T": ["#####", "  #  ", "  #  ", "  #  ", "  #  ", "  #  ", "  #  "],
        "U": ["#   #", "#   #", "#   #", "#   #", "#   #", "#   #", " ### "],
        "V": ["#   #", "#   #", "#   #", "#   #", "#   #", " # # ", "  #  "],
        "W": ["#   #", "#   #", "#   #", "# # #", "# # #", "## ##", "#   #"],
        "X": ["#   #", "#   #", " # # ", "  #  ", " # # ", "#   #", "#   #"],
        "Y": ["#   #", "#   #", " # # ", "  #  ", "  #  ", "  #  ", "  #  "],
        "Z": ["#####", "    #", "   # ", "  #  ", " #   ", "#    ", "#####"],
        "0": [" ### ", "#   #", "#  ##", "# # #", "##  #", "#   #", " ### "],
        "1": ["  #  ", " ##  ", "  #  ", "  #  ", "  #  ", "  #  ", " ### "],
        "2": [" ### ", "#   #", "    #", "   # ", "  #  ", " #   ", "#####"],
        "3": ["#####", "   # ", "  #  ", "   # ", "    #", "#   #", " ### "],
        "4": ["   # ", "  ## ", " # # ", "#  # ", "#####", "   # ", "   # "],
        "5": ["#####", "#    ", "#### ", "    #", "    #", "#   #", " ### "],
        "6": [" ### ", "#    ", "#    ", "#### ", "#   #", "#   #", " ### "],
        "7": ["#####", "    #", "   # ", "  #  ", " #   ", " #   ", " #   "],
        "8": [" ### ", "#   #", "#   #", " ### ", "#   #", "#   #", " ### "],
        "9": [" ### ", "#   #", "#   #", " ####", "    #", "    #", " ### "],
        ".": ["     ", "     ", "     ", "     ", "     ", " ##  ", " ##  "],
        ",": ["     ", "     ", "     ", "     ", " ##  ", " ##  ", "#    "],
        ":": ["     ", " ##  ", " ##  ", "     ", " ##  ", " ##  ", "     "],
        "-": ["     ", "     ", "     ", "#####", "     ", "     ", "     "],
        "+": ["     ", "  #  ", "  #  ", "#####", "  #  ", "  #  ", "     "],
        "/": ["    #", "    #", "   # ", "  #  ", " #   ", "#    ", "#    "],
        "!": ["  #  ", "  #  ", "  #  ", "  #  ", "  #  ", "     ", "  #  "],
        "?": [" ### ", "#   #", "    #", "   # ", "  #  ", "     ", "  #  "],
        "%": ["##  #", "##  #", "   # ", "  #  ", " #   ", "#  ##", "#  ##"],
        "$": ["  #  ", " ####", "# #  ", " ### ", "  # #", "#### ", "  #  "],
        "(": ["   # ", "  #  ", " #   ", " #   ", " #   ", "  #  ", "   # "],
        ")": [" #   ", "  #  ", "   # ", "   # ", "   # ", "  #  ", " #   "],
        "'": ["  #  ", "  #  ", "  #  ", "     ", "     ", "     ", "     "],
        "#": [" # # ", " # # ", "#####", " # # ", "#####", " # # ", " # # "],
        // Icon glyphs (mapped to private-use characters via the constants below)
        "\u{2193}": ["  #  ", "  #  ", "  #  ", "  #  ", "# # #", " ### ", "  #  "], // ↓ down arrow
        "\u{2713}": ["     ", "    #", "    #", "#  # ", "# #  ", " ##  ", "     "], // ✓ check
        "\u{2717}": ["     ", "#   #", " # # ", "  #  ", " # # ", "#   #", "     "], // ✗ cross
        "\u{25CF}": ["     ", " ### ", "#####", "#####", "#####", " ### ", "     "], // ● dot
    ]

    // Convenience icon constants for call sites.
    static let arrowDown: Character = "\u{2193}"
    static let check: Character = "\u{2713}"
    static let cross: Character = "\u{2717}"
    static let dot: Character = "\u{25CF}"
}
